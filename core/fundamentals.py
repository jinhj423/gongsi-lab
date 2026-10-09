"""분기 실적 수집·계산. PROTECTED.

  python3 core/fundamentals.py kr     # DART 다중회사 주요계정 (fnlttMultiAcnt, 최대 100개사/호출, 2015년 이후)
  python3 core/fundamentals.py us     # SEC XBRL frames (개념 하나 x 분기 하나 = 전체 회사 한 번에)

결과: state/fundamentals-<market>.json
  분기별 매출(rev)·영업이익(op)·순이익(ni), 최신 분기 기준 YoY, 영업이익률과 그 변화,
  최근 4분기 순이익 합(ttm_ni), 자본총계(equity), 계산 PER/PBR(시총 기준).
숫자는 모두 공식 공시(정기보고서/10-Q·10-K XBRL)에서 온 값이고, PER/PBR 은 그 값으로 계산한 것이다.
잠정실적 공시는 여기 반영되지 않는다 (정기보고서 제출 후 반영).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from common import DATA, STATE, out, secret

REPRT = [("11013", "Q1"), ("11012", "Q2"), ("11014", "Q3"), ("11011", "Q4")]
KR_ACCOUNTS = {
    "rev": re.compile(r"^(매출액|수익\(매출액\)|영업수익|매출)$"),
    "op": re.compile(r"^영업이익(\(손실\))?$"),
    "ni": re.compile(r"^(당기순이익|당기순이익\(손실\)|분기순이익|반기순이익)(\(손실\))?$"),
    "equity": re.compile(r"^자본총계$"),
}
US_CONCEPTS = {
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "op": ["OperatingIncomeLoss"],
    "ni": ["NetIncomeLoss"],
}


def path(market: str):
    return STATE / f"fundamentals-{market}.json"


def load(market: str) -> dict:
    p = path(market)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"updated": None, "companies": {}}


def save(market: str, companies: dict, note: str = "") -> None:
    p = path(market)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"updated": dt.date.today().isoformat(), "note": note, "companies": companies},
                            ensure_ascii=False), encoding="utf-8")


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def qkey_prev_year(q: str) -> str:
    return f"{int(q[:4]) - 1}{q[4:]}"


def metrics(quarters: dict, market_cap: float | None) -> dict:
    """quarters: {'2025Q2': {'rev':..,'op':..,'ni':..}, ...} (3개월 값). equity 는 별도 키 '_equity'."""
    qs = sorted(k for k in quarters if re.match(r"^\d{4}Q[1-4]$", k) and quarters[k].get("rev") is not None)
    m: dict = {"latest_q": qs[-1] if qs else None, "n_quarters": len(qs)}
    if not qs:
        return m
    cur = quarters[qs[-1]]
    prev = quarters.get(qkey_prev_year(qs[-1]), {})

    def yoy(a, b):
        if a is None or b in (None, 0):
            return None
        return (a - b) / abs(b)
    m["rev"] = cur.get("rev")
    m["op"] = cur.get("op")
    m["rev_yoy"] = yoy(cur.get("rev"), prev.get("rev"))
    m["op_yoy"] = yoy(cur.get("op"), prev.get("op"))
    if cur.get("rev") and cur.get("op") is not None:
        m["op_margin"] = cur["op"] / cur["rev"]
    if prev.get("rev") and prev.get("op") is not None and m.get("op_margin") is not None:
        m["op_margin_chg"] = m["op_margin"] - prev["op"] / prev["rev"]
    m["turnaround"] = bool(prev.get("op") is not None and cur.get("op") is not None and prev["op"] < 0 <= cur["op"])
    # 최근 4분기가 연속으로 있을 때만 TTM
    last4 = qs[-4:]
    if len(last4) == 4 and all(quarters[k].get("ni") is not None for k in last4):
        y, q = int(last4[0][:4]), int(last4[0][5])
        expect = []
        for _ in range(4):
            expect.append(f"{y}Q{q}")
            q += 1
            if q == 5:
                y, q = y + 1, 1
        if expect == last4:
            m["ttm_ni"] = sum(quarters[k]["ni"] for k in last4)
            m["ttm_op"] = sum(quarters[k].get("op") or 0 for k in last4)
    eq = quarters.get("_equity")
    if eq:
        m["equity"] = eq
    if market_cap:
        if m.get("ttm_ni") and m["ttm_ni"] > 0:
            m["per"] = market_cap / m["ttm_ni"]
        if eq and eq > 0:
            m["pbr"] = market_cap / eq
    # 최근 8분기 영업이익 추세 (그래프·리포트용)
    m["op_hist"] = [[k, quarters[k].get("op")] for k in qs[-8:]]
    m["rev_hist"] = [[k, quarters[k].get("rev")] for k in qs[-8:]]
    return m


# ---------------------------------------------------------------- KR (DART)

def dart_corp_codes(max_age_days: int = 30) -> dict[str, str]:
    """stock_code -> corp_code (corpCode.xml zip)."""
    p = DATA / "dart" / "corpcode.json"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return json.loads(p.read_text())
    import requests
    import sources
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": sources.dart_key()}, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise RuntimeError("corpCode 응답이 zip 이 아님: " + r.content[:200].decode("utf-8", "ignore"))
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read(z.namelist()[0])
    m = {}
    for el in ET.fromstring(xml).iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = el.findtext("corp_code").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))
    return m


def parse_multi(rows: list[dict], year: int, q: str, acc: dict) -> None:
    """fnlttMultiAcnt 응답을 acc[stock_code][kind] = {'cum':..., 'amt':..., 'fs':...} 형태로 누적."""
    for r in rows:
        sc = (r.get("stock_code") or "").strip()
        nm = re.sub(r"\s", "", r.get("account_nm") or "")
        for kind, pat in KR_ACCOUNTS.items():
            if not pat.match(nm):
                continue
            key = (sc, f"{year}{q}", kind)
            fs = r.get("fs_div")
            prev = acc.get(key)
            if prev and prev["fs"] == "CFS" and fs != "CFS":
                continue  # 연결 우선
            acc[key] = {"fs": fs, "amt": _num(r.get("thstrm_amount")), "add": _num(r.get("thstrm_add_amount"))}


def kr_quarters(acc: dict, stock_code: str, years: list[int]) -> dict:
    """누적값(cumulative)으로 맞춘 뒤 차분해서 3개월 값을 만든다."""
    out_q: dict = {}
    for kind in ("rev", "op", "ni"):
        for y in years:
            cum: dict[str, float | None] = {}
            three: dict[str, float | None] = {}
            for _, q in REPRT:
                v = acc.get((stock_code, f"{y}{q}", kind))
                if not v:
                    continue
                if q == "Q1":
                    cum[q] = v["amt"]
                    three[q] = v["amt"]
                elif q in ("Q2", "Q3"):
                    cum[q] = v["add"] if v["add"] is not None else None
                    three[q] = v["amt"]  # 분기·반기보고서의 당기금액은 3개월 값
                    if v["add"] is not None and v["amt"] is not None and abs(v["add"] - v["amt"]) < 1:
                        three[q] = None  # 당기금액이 누적값으로 온 경우: 아래에서 누적 차분으로 계산
                else:  # 사업보고서 = 연간
                    cum[q] = v["amt"]
            for q, prev_q in (("Q2", "Q1"), ("Q3", "Q2")):
                if three.get(q) is None and cum.get(q) is not None and cum.get(prev_q) is not None:
                    three[q] = cum[q] - cum[prev_q]
            if cum.get("Q4") is not None:
                q3cum = cum.get("Q3")
                if q3cum is None and all(three.get(x) is not None for x in ("Q1", "Q2", "Q3")):
                    q3cum = three["Q1"] + three["Q2"] + three["Q3"]
                if q3cum is not None:
                    three["Q4"] = cum["Q4"] - q3cum
            for q, val in three.items():
                if val is not None:
                    out_q.setdefault(f"{y}{q}", {})[kind] = val
    # 자본총계: 가장 최근 보고서 값
    eq_keys = sorted(k for k in acc if k[0] == stock_code and k[2] == "equity")
    if eq_keys:
        out_q["_equity"] = acc[eq_keys[-1]]["amt"]
    return out_q


def build_kr(universe: list[dict]) -> dict:
    import requests
    import sources
    codes = dart_corp_codes()
    want = [(u["ticker"], codes.get(u["ticker"])) for u in universe]
    missing = [t for t, c in want if not c]
    corp = [c for _, c in want if c]
    this_year = dt.date.today().year
    years = [this_year - 2, this_year - 1, this_year]
    acc: dict = {}
    calls = 0
    for y in years:
        for rc, q in REPRT:
            for i in range(0, len(corp), 100):
                r = requests.get("https://opendart.fss.or.kr/api/fnlttMultiAcnt.json", timeout=60, params={
                    "crtfc_key": sources.dart_key(), "corp_code": ",".join(corp[i:i + 100]),
                    "bsns_year": str(y), "reprt_code": rc})
                calls += 1
                j = r.json()
                if j.get("status") == "000":
                    parse_multi(j.get("list", []), y, q, acc)
                elif j.get("status") == "020":
                    raise RuntimeError("DART 요청 한도 초과 - 내일 다시")
                time.sleep(0.2)
    caps = {u["ticker"]: u.get("market_cap") for u in universe}
    comps = {}
    for t, _ in want:
        qd = kr_quarters(acc, t, years)
        comps[t] = {"quarters": {k: v for k, v in qd.items() if k != "_equity"}, **metrics(qd, caps.get(t))}
    save("kr", comps, f"DART fnlttMultiAcnt, calls={calls}, missing_corp_code={missing[:10]}")
    return {"companies": len(comps), "with_data": sum(1 for c in comps.values() if c.get("latest_q")), "calls": calls}


# ---------------------------------------------------------------- US (SEC frames)

def recent_cal_quarters(n: int, today: dt.date | None = None) -> list[str]:
    d = today or dt.date.today()
    y, q = d.year, (d.month - 1) // 3 + 1
    res = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        res.append(f"{y}Q{q}")
    return res  # 최근 완료 분기부터 과거로


def sec_frame(concept: str, period: str, unit: str = "USD", taxonomy: str = "us-gaap") -> list[dict]:
    import sources
    try:
        j = sources.sec_get(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/CY{period}.json").json()
        return j.get("data", [])
    except Exception:  # noqa: BLE001  (아직 없는 분기 = 404)
        return []


def build_us(top_n: int) -> dict:
    import prices
    import sources
    import universe
    sp = universe.sp500()
    tmap = sources.sec_ticker_map()
    cik_of = {}
    for t in sp["ticker"]:
        info = tmap.get(str(t).upper().replace(".", "-")) or tmap.get(str(t).upper())
        if info:
            cik_of[str(t).upper()] = info["cik"]
    by_cik = {v: k for k, v in cik_of.items()}
    qs = recent_cal_quarters(9)
    quarters: dict[str, dict] = {t: {} for t in cik_of}
    for kind, concepts in US_CONCEPTS.items():
        for per in qs:
            got: set[int] = set()
            for c in concepts:
                for row in sec_frame(c, per):
                    cik = int(row.get("cik", 0))
                    if cik in by_cik and cik not in got:
                        quarters[by_cik[cik]].setdefault(per, {})[kind] = float(row["val"])
                        got.add(cik)
    shares: dict[str, float] = {}
    for per in qs[:3]:
        for row in sec_frame("EntityCommonStockSharesOutstanding", per + "I", unit="shares", taxonomy="dei"):
            t = by_cik.get(int(row.get("cik", 0)))
            if t and t not in shares:
                shares[t] = float(row["val"])
    equity: dict[str, float] = {}
    for per in qs[:3]:
        for row in sec_frame("StockholdersEquity", per + "I"):
            t = by_cik.get(int(row.get("cik", 0)))
            if t and t not in equity:
                equity[t] = float(row["val"])
    rows = []
    sector_col = next((c for c in ("Sector", "GICS Sector") if c in sp.columns), None)
    name_col = next((c for c in ("Name", "Security") if c in sp.columns), None)
    for _, r in sp.iterrows():
        t = str(r["ticker"]).upper()
        if t not in cik_of or t not in shares:
            continue
        df = prices.update("us", t, start=(dt.date.today() - dt.timedelta(days=500)).isoformat(), max_age_hours=20)
        if df is None or not len(df):
            continue
        close = float(df["Close"].iloc[-1])
        rows.append({"ticker": t, "name": str(r[name_col]) if name_col else t, "board": None,
                     "market_cap": shares[t] * close, "shares": shares[t], "close": close,
                     "sector": str(r[sector_col]) if sector_col else "미분류", "industry": "", "cik": cik_of[t]})
    rows.sort(key=lambda x: -x["market_cap"])
    rows = rows[:top_n]
    for i, r in enumerate(rows, 1):
        r["cap_rank"] = i
    universe.save("us", rows, "S&P500(위키백과) x SEC dei 발행주식수 x 종가")
    comps = {}
    for r in rows:
        qd = dict(quarters.get(r["ticker"], {}))
        if r["ticker"] in equity:
            qd["_equity"] = equity[r["ticker"]]
        comps[r["ticker"]] = {"quarters": {k: v for k, v in qd.items() if k != "_equity"}, **metrics(qd, r["market_cap"])}
    save("us", comps, "SEC XBRL frames (calendar-quarter aligned)")
    return {"companies": len(comps), "with_data": sum(1 for c in comps.values() if c.get("latest_q"))}


def main() -> None:
    m = sys.argv[1] if len(sys.argv) > 1 else ""
    import universe
    if m == "kr":
        if not secret("DART_API_KEY", "dart-key"):
            sys.exit("DART_API_KEY 없음")
        u = universe.load("kr") or universe.build_kr()
        out(build_kr(u))
    elif m == "us":
        out(build_us(universe.conf()["universe"]["us_top"]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
