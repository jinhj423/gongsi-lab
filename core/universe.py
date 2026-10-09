"""대상 기업군: 한국 시총 상위 N (KOSPI+KOSDAQ), 미국 S&P500 중 시총 상위 N. PROTECTED.

  python3 core/universe.py kr      # state/universe-kr.json 갱신
  python3 core/universe.py us      # (시총은 fundamentals 단계에서 계산 후 확정)

KR 시총: FinanceDataReader StockListing('KRX') (KRX 정보데이터시스템). 막히면 FinanceData 가 GitHub 에
매일 올려두는 같은 형식의 KRX 목록 캐시를 직접 읽는다.
US 구성종목: FinanceDataReader StockListing('S&P500') (위키백과 S&P 500 표, GICS 섹터 포함).
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import pandas as pd

from common import ROOT, STATE, out

FDR_KRX_CACHE = "https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/refs/heads/master/data/listing/krx/{d}.csv"


def conf() -> dict:
    return json.loads((ROOT / "config" / "screen.json").read_text(encoding="utf-8"))


def path(market: str):
    return STATE / f"universe-{market}.json"


def load(market: str) -> list[dict]:
    p = path(market)
    return json.loads(p.read_text(encoding="utf-8"))["companies"] if p.exists() else []


def save(market: str, rows: list[dict], source: str) -> None:
    p = path(market)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"updated": dt.date.today().isoformat(), "source": source, "companies": rows},
                            ensure_ascii=False, indent=0), encoding="utf-8")


def _valid(df: pd.DataFrame) -> bool:
    """휴장일·장애일 파일은 시총이 '-' 로 채워져 온다. 절반 이상이 양수여야 유효."""
    if df is None or not len(df) or "Marcap" not in df.columns:
        return False
    return (df["Marcap"].map(_f) > 0).mean() > 0.5


def kr_listing() -> tuple[pd.DataFrame, str]:
    errs = []
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        if _valid(df):
            return df, "FinanceDataReader KRX"
        errs.append("fdr: 시총 값 없음(휴장일?)")
    except Exception as e:  # noqa: BLE001
        errs.append(f"fdr: {str(e)[:100]}")
    for k in range(0, 15):
        d = (dt.date.today() - dt.timedelta(days=k)).isoformat()
        try:
            df = pd.read_csv(FDR_KRX_CACHE.format(d=d), dtype={"Code": str, "MarketId": str})
            if _valid(df):
                return df, f"FinanceData KRX cache {d}"
            errs.append(f"cache {d}: 시총 값 없음")
        except Exception as e:  # noqa: BLE001
            errs.append(f"cache {d}: {str(e)[:60]}")
    raise RuntimeError("KRX 시총 목록을 받지 못함: " + " / ".join(errs[:3]))


def _f(x) -> float:
    try:
        v = float(str(x).replace(",", ""))
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0  # 거래정지 종목 등은 '-' 로 온다


def build_kr() -> list[dict]:
    c = conf()["universe"]
    df, src = kr_listing()
    df = df.copy()
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    df["Marcap"] = df["Marcap"].map(_f)
    df = df[df["Market"].isin(c["kr_markets"])]
    if c.get("exclude_preferred"):
        df = df[df["Code"].str.endswith("0")]  # 보통주 코드는 0 으로 끝난다 (우선주 5/7/9, 신형우선주 K 등 제외)
    for w in c.get("exclude_name_contains", []):
        df = df[~df["Name"].astype(str).str.contains(w, regex=False)]
    df = df.sort_values("Marcap", ascending=False).head(int(c["kr_top"]))
    import sectors
    rows = []
    for i, r in enumerate(df.itertuples(), 1):
        sec = sectors.lookup("kr", r.Code)
        rows.append({
            "ticker": r.Code, "name": str(r.Name), "board": "Y" if str(r.Market) == "KOSPI" else "K",
            "market_cap": _f(r.Marcap), "shares": _f(getattr(r, "Stocks", 0)),
            "close": _f(getattr(r, "Close", 0)), "cap_rank": i,
            "sector": sec["sector"], "industry": sec.get("industry", ""),
        })
    save("kr", rows, src)
    return rows


def sp500() -> pd.DataFrame:
    import FinanceDataReader as fdr
    df = fdr.StockListing("S&P500")
    sym = "Symbol" if "Symbol" in df.columns else "Code"
    return df.rename(columns={sym: "ticker"})


def main() -> None:
    m = sys.argv[1] if len(sys.argv) > 1 else ""
    if m == "kr":
        rows = build_kr()
        out({"companies": len(rows), "top3": [r["name"] for r in rows[:3]]})
    elif m == "us":
        df = sp500()
        out({"sp500": len(df), "columns": list(df.columns)})
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
