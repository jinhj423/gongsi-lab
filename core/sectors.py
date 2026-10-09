"""Company -> sector/industry lookup. PROTECTED.

  python3 core/sectors.py refresh          # KR 전체 업종표 갱신 (FinanceDataReader KRX-DESC)
  python3 core/sectors.py get kr 005930
  python3 core/sectors.py get us NVDA      # SEC 회사 정보의 SIC 업종 설명

캐시: $LAB_DATA/sectors.json. 운영자 덮어쓰기: config/sector_overrides.yaml
  rename:  KRX/SIC 업종명 -> 내가 쓰는 섹터명 (여러 업종을 하나로 묶기)
  ticker:  종목별 지정 (예: "kr:005930": 반도체)
"""
from __future__ import annotations

import json
import sys
import time

import yaml

from common import ROOT, STATE, out

CACHE = STATE / "sectors.json"
UNKNOWN = "미분류"


def _load() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {"kr": {}, "us": {}, "kr_refreshed": 0}


def _save(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


def overrides() -> dict:
    p = ROOT / "config" / "sector_overrides.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def _pick(cols, *names):
    for n in names:
        if n in cols:
            return n
    return None


def refresh_kr() -> int:
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX-DESC")
    code = _pick(df.columns, "Code", "Symbol", "종목코드")
    sec = _pick(df.columns, "Sector", "업종")
    ind = _pick(df.columns, "Industry", "주요제품")
    name = _pick(df.columns, "Name", "종목명")
    if not code or not sec:
        raise RuntimeError(f"KRX-DESC 컬럼을 알 수 없음: {list(df.columns)}")
    c = _load()
    for _, r in df.iterrows():
        c["kr"][str(r[code]).zfill(6)] = {
            "sector": str(r[sec]) if r[sec] == r[sec] and r[sec] else UNKNOWN,
            "industry": str(r[ind]) if ind and r[ind] == r[ind] and r[ind] else "",
            "name": str(r[name]) if name else "",
        }
    c["kr_refreshed"] = time.time()
    _save(c)
    return len(c["kr"])


def fetch_us(ticker: str) -> dict | None:
    import sources
    tmap = sources.sec_ticker_map()
    info = tmap.get(ticker.upper().replace(".", "-")) or tmap.get(ticker.upper())
    if not info:
        return None
    j = sources.sec_get(f"https://data.sec.gov/submissions/CIK{info['cik']:010d}.json").json()
    return {"sector": j.get("sicDescription") or UNKNOWN, "industry": str(j.get("sic") or ""), "name": j.get("name", "")}


def lookup(market: str, ticker: str, network: bool = False) -> dict:
    """{'sector', 'industry', 'name'} with operator overrides applied. Never raises."""
    c = _load()
    t = ticker.upper() if market == "us" else ticker.zfill(6)
    rec = c.get(market, {}).get(t)
    if rec is None and network:
        try:
            if market == "kr":
                if time.time() - c.get("kr_refreshed", 0) > 3600:
                    refresh_kr()
            else:
                got = fetch_us(t)
                if got:
                    c = _load()
                    c["us"][t] = got
                    _save(c)
            rec = _load().get(market, {}).get(t)
        except Exception as e:  # noqa: BLE001
            print(f"[sectors] {market}:{t} 조회 실패: {e}", file=sys.stderr)
    rec = dict(rec or {"sector": UNKNOWN, "industry": "", "name": ""})
    ov = overrides()
    rec["raw_sector"] = rec["sector"]
    rec["sector"] = (ov.get("rename") or {}).get(rec["sector"], rec["sector"])
    forced = (ov.get("ticker") or {}).get(f"{market}:{t}")
    if forced:
        rec["sector"] = forced
    return rec


def main() -> None:
    a = sys.argv[1:]
    if a[:1] == ["refresh"]:
        out({"kr_companies": refresh_kr()})
    elif len(a) == 3 and a[0] == "get":
        out(lookup(a[1], a[2], network=True))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
