"""Disclosure sources: DART OpenAPI (KR) and SEC EDGAR (US). PROTECTED.

Keys never leave this module and are never printed:
  DART_API_KEY   (or ~/.config/gongsi-lab/dart-key)
  SEC_USER_AGENT (or ~/.config/gongsi-lab/sec-user-agent), e.g. "Hong Gildong you@example.com"
"""
from __future__ import annotations

import html
import io
import json
import re
import time
import zipfile

from common import DATA, die, secret
from classify import kr_type, us_type

DART = "https://opendart.fss.or.kr/api"


class SourceError(Exception):
    pass


def _requests():
    try:
        import requests  # noqa: F401
    except ImportError:
        die("requests 가 필요합니다: pip install requests")
    import requests
    return requests


# ---------------------------------------------------------------- DART (KR)

def dart_key() -> str:
    k = secret("DART_API_KEY", "dart-key")
    if not k:
        die("DART_API_KEY 가 없습니다. opendart.fss.or.kr 에서 발급 후 환경변수나 ~/.config/gongsi-lab/dart-key 에 저장하세요.")
    return k


def dart_list_day(yyyymmdd: str, pblntf_types: list[str]) -> list[dict]:
    """All disclosures filed on one day, for the given 공시유형 codes (B=주요사항, I=거래소공시 ...)."""
    rq = _requests()
    rows: list[dict] = []
    for ty in pblntf_types:
        page = 1
        while True:
            r = rq.get(
                f"{DART}/list.json",
                params={
                    "crtfc_key": dart_key(), "bgn_de": yyyymmdd, "end_de": yyyymmdd,
                    "pblntf_ty": ty, "page_no": page, "page_count": 100,
                },
                timeout=30,
            )
            r.raise_for_status()
            j = r.json()
            st = j.get("status")
            if st == "013":  # 조회된 데이터 없음
                break
            if st != "000":
                raise SourceError(f"DART status {st}: {j.get('message')}")
            rows.extend(j.get("list", []))
            if page >= int(j.get("total_page", 1)):
                break
            page += 1
            time.sleep(0.15)
        time.sleep(0.15)
    return rows


def dart_event(raw: dict) -> dict | None:
    code = (raw.get("stock_code") or "").strip()
    if not code or raw.get("corp_cls") not in ("Y", "K"):
        return None  # 비상장/코넥스/기타 제외
    typ, corr = kr_type(raw.get("report_nm", ""))
    d = raw["rcept_dt"]
    return {
        "event_id": f"kr:{raw['rcept_no']}",
        "market": "kr",
        "ticker": code,
        "name": raw.get("corp_name", ""),
        "board": raw.get("corp_cls"),
        "type": typ,
        "corr": corr,
        "title": raw.get("report_nm", "").strip(),
        "filed_date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
        "filed_at": None,  # DART list API 는 접수 시각을 주지 않음 -> 보수적으로 장 마감 후로 간주
        "source_id": raw["rcept_no"],
        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={raw['rcept_no']}",
        "rm": raw.get("rm", ""),
    }


def dart_document_text(rcept_no: str) -> str:
    rq = _requests()
    r = rq.get(f"{DART}/document.xml", params={"crtfc_key": dart_key(), "rcept_no": rcept_no}, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise SourceError("DART document 응답이 zip 이 아님: " + r.content[:300].decode("utf-8", "ignore"))
    parts = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in sorted(z.namelist()):
            raw = z.read(name)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    parts.append(raw.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue
    return strip_markup("\n".join(parts))


# ---------------------------------------------------------------- SEC (US)

def sec_get(url: str):
    ua = secret("SEC_USER_AGENT", "sec-user-agent")
    if not ua:
        die("SEC_USER_AGENT 가 없습니다. 예: export SEC_USER_AGENT='Hong Gildong you@example.com'")
    rq = _requests()
    time.sleep(0.12)  # SEC 공정사용: 초당 10건 이하
    r = rq.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    return r


def sec_ticker_map(refresh: bool = False) -> dict[str, dict]:
    p = DATA / "sec" / "company_tickers.json"
    if refresh or not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sec_get("https://www.sec.gov/files/company_tickers.json").text, encoding="utf-8")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {v["ticker"].upper(): {"cik": int(v["cik_str"]), "title": v["title"]} for v in raw.values()}


def _columnar(block: dict) -> list[dict]:
    keys = list(block.keys())
    n = len(block.get("accessionNumber", []))
    return [{k: block[k][i] if i < len(block[k]) else None for k in keys} for i in range(n)]


def sec_filings(cik: int, since: str) -> list[dict]:
    j = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    rows = _columnar(j["filings"]["recent"])
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= since:
            rows += _columnar(sec_get(f"https://data.sec.gov/submissions/{f['name']}").json())
    return rows


def sec_events(ticker: str, cik: int, rows: list[dict], since: str) -> list[dict]:
    evs = []
    for r in rows:
        form = r.get("form")
        if form not in ("8-K", "8-K/A") or (r.get("filingDate") or "") < since:
            continue
        acc = r["accessionNumber"]
        # acceptanceDateTime 은 'Z' 가 붙어 있지만 실제로는 미 동부 현지 시각이다 (EDGAR 관행).
        at = (r.get("acceptanceDateTime") or "").replace("Z", "").split(".")[0] or None
        evs.append({
            "event_id": f"us:{acc}",
            "market": "us",
            "ticker": ticker.upper(),
            "name": "",
            "board": None,
            "type": us_type(r.get("items") or ""),
            "corr": form == "8-K/A",
            "title": f"{form} items {r.get('items') or '-'}",
            "items": r.get("items") or "",
            "filed_date": r["filingDate"],
            "filed_at": at,  # 동부 현지 시각 (naive)
            "source_id": acc,
            "cik": cik,
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{r.get('primaryDocument') or ''}",
        })
    return evs


def sec_document_text(url: str) -> str:
    return strip_markup(sec_get(url).text)


# ---------------------------------------------------------------- util

def strip_markup(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()
