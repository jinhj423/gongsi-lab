"""시총 상위 기업 비교 -> 매매 후보 선정. PROTECTED (가중치는 strategy/screen.yaml).

  python3 core/screen.py kr|us [--no-refresh]

1) 대상 기업군 갱신 (시총 상위)          core/universe.py
2) 실적 갱신 (며칠마다)                  core/fundamentals.py
3) 주가 갱신 + 추세 지표 계산             아래 trend()
4) 시장 내 백분위 점수 + 필수 조건 + 섹터 비교 -> 순위
5) 상위 후보를 journal/candidates.jsonl 에 기록 (나중에 지수 대비 성과로 채점)
결과: state/screen-<market>.json (텔레그램·옵시디언·직원 리포트가 읽음)

추세 지표 (일봉 종가 기준):
  SMA200 위/아래, SMA200 20일 기울기, EMA 20/60/112/224 정배열 개수(0~4),
  지수 대비 3·6개월 초과수익(rs_63, rs_126), 52주 고점 대비 거리, 20일 평균 거래대금,
  최근 10거래일 안에 SMA200 상향 돌파(대추세 전환) 여부.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

import pandas as pd
import yaml

from common import JOURNAL, STATE, STRATEGY, append_jsonl, git_rev, iso, now_utc, out, read_jsonl
import fundamentals
import prices
import universe

CANDIDATES = JOURNAL / "candidates.jsonl"


def weights() -> dict:
    return yaml.safe_load((STRATEGY / "screen.yaml").read_text(encoding="utf-8")) or {}


def path(market: str):
    return STATE / f"screen-{market}.json"


def load(market: str) -> dict:
    p = path(market)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def trend(df: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    c = df["Close"]
    if len(c) < 210:
        return {"enough_history": False}
    sma200 = c.rolling(200).mean()
    e = {n: c.ewm(span=n, adjust=False).mean().iloc[-1] for n in (20, 60, 112, 224)}
    last = float(c.iloc[-1])
    align = sum([last > e[20], e[20] > e[60], e[60] > e[112], e[112] > e[224]])
    above = c > sma200
    cross = bool(above.iloc[-1] and (~above.iloc[-11:-1]).any())
    hi52 = float(c.iloc[-252:].max())
    t = {
        "enough_history": True, "close": last, "date": c.index[-1].strftime("%Y-%m-%d"),
        "sma200": float(sma200.iloc[-1]), "above_sma200": bool(last > sma200.iloc[-1]),
        "sma200_slope20": float(sma200.iloc[-1] / sma200.iloc[-21] - 1),
        "ema_align": int(align), "sma200_cross_up_10d": cross,
        "from_52w_high": last / hi52 - 1,
        "ret_63": float(c.iloc[-1] / c.iloc[-64] - 1), "ret_126": float(c.iloc[-1] / c.iloc[-127] - 1),
        "traded_value20": float((df["Close"] * df["Volume"]).iloc[-20:].mean()),
    }
    if bench is not None and len(bench) > 130:
        b = bench["Close"].reindex(c.index).ffill()
        t["rs_63"] = t["ret_63"] - float(b.iloc[-1] / b.iloc[-64] - 1)
        t["rs_126"] = t["ret_126"] - float(b.iloc[-1] / b.iloc[-127] - 1)
    return t


def pct_rank(values: dict[str, float | None]) -> dict[str, float | None]:
    s = pd.Series({k: v for k, v in values.items() if v is not None}, dtype=float)
    if s.empty:
        return {k: None for k in values}
    r = s.rank(pct=True) * 100
    return {k: (float(r[k]) if k in r else None) for k in values}


def score(rows: list[dict], market: str, w: dict) -> list[dict]:
    feats = {
        "op_yoy": lambda r: r.get("op_yoy"), "rev_yoy": lambda r: r.get("rev_yoy"),
        "op_margin_chg": lambda r: r.get("op_margin_chg"), "ema_align": lambda r: r.get("ema_align"),
        "rs_126": lambda r: r.get("rs_126"), "rs_63": lambda r: r.get("rs_63"),
        "near_high": lambda r: r.get("from_52w_high"),
    }
    ranks = {f: pct_rank({r["ticker"]: fn(r) for r in rows}) for f, fn in feats.items()}
    ws = w.get("weights", {})
    g = w.get("gates", {})
    for r in rows:
        tot, wsum, parts = 0.0, 0.0, {}
        for f, wt in ws.items():
            v = ranks.get(f, {}).get(r["ticker"])
            if v is None:
                continue
            parts[f] = round(v)
            tot += v * wt
            wsum += wt
        r["score"] = round(tot / wsum, 1) if wsum else None
        r["score_parts"] = parts
        fails = []
        if g.get("above_sma200") and not r.get("above_sma200"):
            fails.append("SMA200 아래")
        if g.get("sma200_rising") and not (r.get("sma200_slope20") or 0) > 0:
            fails.append("SMA200 하락")
        if g.get("op_yoy_min") is not None:
            g["op_yoy_min"] = float(g["op_yoy_min"])
        if g.get("op_yoy_min") is not None and not ((r.get("op_yoy") is not None and r["op_yoy"] >= g["op_yoy_min"]) or r.get("turnaround")):
            fails.append("영업이익 역성장" if r.get("op_yoy") is not None else "실적 데이터 없음")
        mtv = (g.get("min_traded_value") or {}).get(market)
        mtv = float(mtv) if mtv not in (None, "") else None  # YAML 의 1e9 표기는 문자열이 될 수 있다
        if mtv and (r.get("traded_value20") or 0) < mtv:
            fails.append("거래대금 부족")
        if not r.get("enough_history"):
            fails.append("주가 이력 부족")
        r["gate_fails"] = fails
    # 섹터 안 순위
    by_sec: dict[str, list[dict]] = {}
    for r in rows:
        by_sec.setdefault(r.get("sector") or "미분류", []).append(r)
    for sec, rs in by_sec.items():
        rs_sorted = sorted([x for x in rs if x.get("score") is not None], key=lambda x: -x["score"])
        for i, x in enumerate(rs_sorted, 1):
            x["sector_rank"] = f"{i}/{len(rs_sorted)}"
            if w.get("sector_bonus") and len(rs_sorted) >= 3 and i <= max(1, len(rs_sorted) // 3):
                x["score"] = round(x["score"] + w["sector_bonus"], 1)
    return rows


def sector_summary(rows: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r.get("sector") or "미분류", []).append(r)
    res = []
    for sec, rs in by.items():
        def med(k):
            v = [x[k] for x in rs if x.get(k) is not None]
            return float(pd.Series(v).median()) if v else None
        res.append({"sector": sec, "n": len(rs), "market_cap": sum(x.get("market_cap") or 0 for x in rs),
                    "median_op_yoy": med("op_yoy"), "median_rs_126": med("rs_126"),
                    "pct_above_sma200": sum(1 for x in rs if x.get("above_sma200")) / len(rs),
                    "passing": sum(1 for x in rs if not x.get("gate_fails"))})
    return sorted(res, key=lambda x: -(x["median_rs_126"] if x["median_rs_126"] is not None else -9))


def run(market: str, refresh: bool = True) -> dict:
    t0 = time.time()
    if refresh:
        if market == "kr":
            universe.build_kr()
        f = fundamentals.load(market)
        age = (dt.date.today() - dt.date.fromisoformat(f["updated"])).days if f.get("updated") else 999
        if age >= universe.conf().get("fundamentals_max_age_days", 3) or market == "us" and not universe.load("us"):
            if market == "kr":
                fundamentals.build_kr(universe.load("kr"))
            else:
                fundamentals.build_us(universe.conf()["universe"]["us_top"])
    uni = universe.load(market)
    fund = fundamentals.load(market).get("companies", {})
    start = (dt.date.today() - dt.timedelta(days=560)).isoformat()
    benches = {}
    for b in (["KS11", "KQ11"] if market == "kr" else ["SPY"]):
        benches[b] = prices.update(market, b, start=start, max_age_hours=6) if refresh else prices.load(market, b)
    rows = []
    for u in uni:
        df = prices.update(market, u["ticker"], start=start, max_age_hours=6) if refresh else prices.load(market, u["ticker"])
        bench = benches.get(prices.bench_ticker(market, u.get("board")))
        tr = trend(df, bench) if df is not None else {"enough_history": False}
        fm = {k: v for k, v in fund.get(u["ticker"], {}).items() if k != "quarters"}
        rows.append({**u, **fm, **tr})
    w = weights()
    rows = score(rows, market, w)
    ranked = sorted([r for r in rows if r.get("score") is not None], key=lambda r: -r["score"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    k = universe.conf()["candidates_per_market"]
    cands = [r for r in ranked if not r["gate_fails"]][:k]
    prev = load(market)
    prev_c = {c["ticker"] for c in prev.get("candidates", [])}
    for c in cands:
        c["is_new"] = c["ticker"] not in prev_c
    dropped = [t for t in prev_c if t not in {c["ticker"] for c in cands}]
    # 주의: 시총 상위인데 대추세 이탈 (SMA200 하향) 이 최근 발생
    warn = [r for r in rows if r.get("enough_history") and not r.get("above_sma200") and r.get("cap_rank", 999) <= 30]
    res = {
        "market": market, "asof": max((r.get("date") or "") for r in rows) if rows else None,
        "generated": iso(now_utc()), "universe": len(uni), "scored": len(ranked),
        "weights_rev": git_rev(), "candidates": cands, "dropped": dropped,
        "warnings": [{"ticker": r["ticker"], "name": r["name"], "cap_rank": r.get("cap_rank")} for r in warn],
        "sectors": sector_summary(rows), "all": ranked,
    }
    path(market).parent.mkdir(parents=True, exist_ok=True)
    path(market).write_text(json.dumps(res, ensure_ascii=False, default=float), encoding="utf-8")
    # 후보 원장: 날짜당 한 번만
    today_rows = {(c["date"], c["ticker"]) for c in read_jsonl(CANDIDATES) if c.get("market") == market}
    for c in cands:
        if (res["asof"], c["ticker"]) in today_rows:
            continue
        append_jsonl(CANDIDATES, {
            "date": res["asof"], "recorded_at": iso(now_utc()), "market": market, "ticker": c["ticker"],
            "name": c["name"], "board": c.get("board"), "sector": c.get("sector"), "rank": c["rank"],
            "score": c["score"], "parts": c["score_parts"], "is_new": c["is_new"], "close": c.get("close"),
            "strategy_rev": git_rev(),
        })
    return {"market": market, "asof": res["asof"], "universe": len(uni), "scored": len(ranked),
            "candidates": [f"{c['name']}({c['score']})" for c in cands], "new": [c["name"] for c in cands if c["is_new"]],
            "seconds": round(time.time() - t0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", choices=["kr", "us"])
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()
    out(run(a.market, refresh=not a.no_refresh))


if __name__ == "__main__":
    main()
