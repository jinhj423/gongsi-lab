"""Daily OHLCV cache + event windows. PROTECTED.

KR: FinanceDataReader (pip install finance-datareader)
US: yfinance (pip install yfinance), auto-adjusted.
Cache: $LAB_DATA/prices/<market>/<ticker>.csv
"""
from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from common import DATA, MARKETS, cfg

COLS = ["Open", "High", "Low", "Close", "Volume"]
_mem: dict[tuple[str, str], pd.DataFrame | None] = {}


def _path(market: str, ticker: str):
    safe = ticker.replace("/", "_").replace("^", "_")
    return DATA / "prices" / market / f"{safe}.csv"


def load(market: str, ticker: str) -> pd.DataFrame | None:
    key = (market, ticker)
    if key in _mem:
        return _mem[key]
    p = _path(market, ticker)
    df = None
    if p.exists():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        df = df[COLS].dropna(subset=["Open", "Close"])
        df = df[(df["Open"] > 0) & (df["Close"] > 0)].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        if df.empty:
            df = None
    _mem[key] = df
    return df


def _fetch(market: str, ticker: str, start: str) -> pd.DataFrame:
    if market == "kr":
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start)
    else:
        import yfinance as yf
        df = yf.Ticker(ticker).history(start=start, auto_adjust=True)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "Date"
    return df[[c for c in COLS if c in df.columns]]


def update(market: str, ticker: str, start: str = "2015-01-01", max_age_hours: float = 12) -> pd.DataFrame | None:
    """Refresh cache if stale. Network errors leave the cache as is."""
    p = _path(market, ticker)
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age_hours * 3600:
        return load(market, ticker)
    try:
        old = load(market, ticker)
        since = start if old is None else (old.index[-1] - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
        new = _fetch(market, ticker, since)
        df = new if old is None else pd.concat([old[old.index < new.index.min()] if len(new) else old, new])
        if df is not None and len(df):
            p.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(p)
    except Exception as e:  # noqa: BLE001
        print(f"[prices] {market}:{ticker} 갱신 실패: {e}", flush=True)
    _mem.pop((market, ticker), None)
    return load(market, ticker)


def bench_ticker(market: str, board: str | None) -> str:
    b = cfg()["benchmarks"][market]
    return b.get(board or "", b["default"])


# ---------------------------------------------------------------- windows

def actionable_local(market: str, filed_date: str, filed_at: str | None, recorded_at_utc: dt.datetime | None) -> dt.datetime:
    """Earliest local wall-clock moment the event could have been acted on.

    KR (no filing time): treat as filed at 23:59 local -> next session.
    US: acceptance time is ET wall clock.
    Forward forecasts: also no earlier than the moment the forecast was recorded.
    """
    tz = MARKETS[market]["tz"]
    if filed_at:
        t = dt.datetime.fromisoformat(filed_at)
    else:
        t = dt.datetime.fromisoformat(filed_date + "T23:59:00")
    if recorded_at_utc is not None:
        rec = recorded_at_utc.astimezone(tz).replace(tzinfo=None)
        t = max(t, rec)
    return t


def entry_session(market: str, sessions: pd.DatetimeIndex, t_local: dt.datetime) -> pd.Timestamp | None:
    """First session whose OPEN is strictly after t_local."""
    open_t = MARKETS[market]["open"]
    d = pd.Timestamp(t_local.date())
    i = sessions.searchsorted(d)  # first session >= that date
    if i < len(sessions) and sessions[i] == d and t_local.time() >= open_t:
        i += 1
    return sessions[i] if i < len(sessions) else None


def event_window(market: str, ticker: str, bench: str, t_local: dt.datetime, h: int, side: int = 1) -> dict:
    """Open(entry) -> Close(entry+h-1) return for stock and benchmark.

    Returns {"status": "ok", ...} or {"status": <reason>}.
    """
    df = load(market, ticker)
    bdf = load(market, bench)
    if df is None:
        return {"status": "no_price"}
    if bdf is None:
        return {"status": "no_bench"}
    e = entry_session(market, bdf.index, t_local)
    if e is None:
        return {"status": "pending"}
    i = df.index.searchsorted(e)  # stock may be halted on e: take its next session
    if i >= len(df):
        return {"status": "pending" if df.index[-1] >= bdf.index[-1] - pd.Timedelta(days=7) else "no_trading"}
    if (df.index[i] - e).days > 10:
        return {"status": "halted"}
    j = i + h - 1
    if j >= len(df):
        return {"status": "pending"}
    ent, ex = df.index[i], df.index[j]
    lim = cfg()["max_abs_daily_return"][market]
    seg = df["Close"].iloc[max(i - 1, 0): j + 1].pct_change().abs()
    if (seg > lim).any():
        return {"status": "suspect_adjust"}
    bi = bdf.index.searchsorted(ent)
    bj = bdf.index.searchsorted(ex, side="right") - 1
    if bi >= len(bdf) or bj < bi:
        return {"status": "no_bench"}
    r = df["Close"].iloc[j] / df["Open"].iloc[i] - 1
    rb = bdf["Close"].iloc[bj] / bdf["Open"].iloc[bi] - 1
    pre = df.iloc[max(i - 20, 0): i]
    return {
        "status": "ok",
        "entry_date": ent.strftime("%Y-%m-%d"),
        "exit_date": ex.strftime("%Y-%m-%d"),
        "entry_px": float(df["Open"].iloc[i]),
        "exit_px": float(df["Close"].iloc[j]),
        "r": float(r),
        "rb": float(rb),
        "ar": float(side * (r - rb)),
        "pre_ret20": float(pre["Close"].iloc[-1] / pre["Close"].iloc[0] - 1) if len(pre) >= 15 else None,
        "traded_value20": float((pre["Close"] * pre["Volume"]).mean()) if len(pre) >= 15 else None,
    }
