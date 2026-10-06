"""Portfolio backtest of strategy/rules_<market>.yaml. PROTECTED.

  python3 core/backtest.py --market kr
  python3 core/backtest.py --market us --trades     # 거래 목록 CSV 를 reports/ 에 저장
  LAB_OPERATOR=1 python3 core/backtest.py --market kr --holdout   # 운영자 전용

규칙: 이벤트 후 첫 실행가능 시가에 진입, h거래일째 종가에 청산. 슬롯 max_positions 개를
균등 배분(직전 종가 기준 자산 / 슬롯 수), 슬롯이 차 있으면 신호 건너뜀, 같은 종목 중복 보유 금지.
비용은 config 의 왕복 bps 를 진입/청산에 절반씩.
하루 실행 횟수 제한(max_backtests_per_day)이 있다 - 반복 백테스트는 과최적화의 지름길.
"""
from __future__ import annotations

import argparse
import math
import statistics

import pandas as pd

from common import ROOT, cfg, die, is_operator, out
import prices
import study


def simulate(market: str, trades: list[dict], k_slots: int, cost_rt: float, side: int) -> dict:
    if not trades:
        return {"n_trades": 0}
    bench = prices.load(market, prices.bench_ticker(market, None))
    start = pd.Timestamp(trades[0]["entry_date"])
    end = max(pd.Timestamp(t["exit_date"]) for t in trades)
    sessions = bench.index[(bench.index >= start) & (bench.index <= end)]
    by_entry: dict[pd.Timestamp, list[dict]] = {}
    for t in trades:
        by_entry.setdefault(pd.Timestamp(t["entry_date"]), []).append(t)

    cash, eq_prev = 1.0, 1.0
    pos: list[dict] = []
    taken, skipped_full, skipped_dup = [], 0, 0
    curve, invested = [], []
    for d in sessions:
        for t in by_entry.get(d, []):
            if len(pos) >= k_slots:
                skipped_full += 1
                continue
            if any(p["ticker"] == t["ticker"] for p in pos):
                skipped_dup += 1
                continue
            alloc = min(eq_prev / k_slots, cash)
            if alloc <= 0:
                skipped_full += 1
                continue
            cash -= alloc
            pos.append({**t, "alloc": alloc * (1 - cost_rt / 2), "df": prices.load(market, t["ticker"]),
                        "exit_ts": pd.Timestamp(t["exit_date"])})
            taken.append(t)
        val = 0.0
        still = []
        for p in pos:
            px = p["exit_px"] if d >= p["exit_ts"] else p["df"]["Close"].asof(d)
            v = p["alloc"] * (1 + side * (px / p["entry_px"] - 1))
            if d >= p["exit_ts"]:
                cash += max(v, 0.0) * (1 - cost_rt / 2)
            else:
                val += max(v, 0.0)
                still.append(p)
        pos = still
        eq = cash + val
        curve.append(eq)
        invested.append(val / eq if eq > 0 else 0)
        eq_prev = eq

    eq_s = pd.Series(curve, index=sessions)
    rets = eq_s.pct_change().dropna()
    yrs = max(len(eq_s) / 252, 1e-9)
    b = bench["Close"].reindex(sessions).ffill()
    peak = eq_s.cummax()
    trade_net = [side * (t["exit_px"] / t["entry_px"] - 1) - cost_rt for t in taken]

    by_year = {}
    for y in sorted({d.year for d in sessions}):
        e_y = eq_s[eq_s.index.year == y]
        b_y = b[b.index.year == y]
        prev_e = eq_s[eq_s.index.year < y]
        prev_b = b[b.index.year < y]
        e0 = prev_e.iloc[-1] if len(prev_e) else 1.0
        b0 = prev_b.iloc[-1] if len(prev_b) else b_y.iloc[0]
        ty = [t for t in taken if t["entry_date"][:4] == str(y)]
        by_year[str(y)] = {
            "strategy": round(e_y.iloc[-1] / e0 - 1, 4),
            "bench": round(b_y.iloc[-1] / b0 - 1, 4),
            "trades": len(ty),
            "mean_ar": round(statistics.fmean([t["ar"] for t in ty]), 4) if ty else None,
        }
    return {
        "period": f"{sessions[0].date()} ~ {sessions[-1].date()}",
        "n_signals": len(trades),
        "n_trades": len(taken),
        "skipped_slots_full": skipped_full,
        "skipped_same_ticker": skipped_dup,
        "total_return": round(eq_s.iloc[-1] - 1, 4),
        "cagr": round(eq_s.iloc[-1] ** (1 / yrs) - 1, 4),
        "bench_total_return": round(b.iloc[-1] / b.iloc[0] - 1, 4),
        "bench_cagr": round((b.iloc[-1] / b.iloc[0]) ** (1 / yrs) - 1, 4),
        "max_drawdown": round(float((eq_s / peak - 1).min()), 4),
        "sharpe": round(rets.mean() / rets.std() * math.sqrt(252), 2) if rets.std() > 0 else None,
        "avg_exposure": round(statistics.fmean(invested), 3),
        "trade_hit_rate_net": round(sum(x > 0 for x in trade_net) / len(trade_net), 4) if trade_net else None,
        "trade_mean_net": round(statistics.fmean(trade_net), 5) if trade_net else None,
        "trade_mean_ar": round(statistics.fmean([t["ar"] for t in taken]), 5) if taken else None,
        "by_year": by_year,
        "_taken": taken,
    }


def main() -> None:
    c = cfg()
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--trades", action="store_true")
    a = ap.parse_args()
    period = "holdout" if a.holdout else "in"
    if a.holdout and not is_operator():
        die("홀드아웃은 운영자 전용입니다 (LAB_OPERATOR=1).")
    if not is_operator() and study.backtests_last_24h() >= c["max_backtests_per_day"]:
        die(f"24시간 백테스트 한도 {c['max_backtests_per_day']}회 도달. 가설을 더 다듬고 내일 다시.")

    rules = study.load_rules(a.market)
    h = int(rules.get("horizon", c["primary_horizon"]))
    if h not in c["horizons"]:
        die(f"rules horizon 은 {c['horizons']} 중 하나")
    side = -1 if rules.get("side") == "short" else 1
    if side < 0 and not c["paper"]["allow_short"][a.market]:
        die(f"{a.market} 은 공매도 비허용")
    k = int(rules.get("max_positions", 20))
    if not 1 <= k <= 50:
        die("max_positions 는 1~50")

    rows = study.metric_rows(a.market, h, period)
    trades = study.apply_rules(rows, rules, a.market)
    for t in trades:
        t["ar"] = side * (t["r"] - t["rb"])
    trades.sort(key=lambda t: (t["entry_date"], t["event_id"]))
    res = simulate(a.market, trades, k, c["cost_bps_roundtrip"][a.market] / 1e4, side)
    taken = res.pop("_taken", [])
    if a.trades and taken:
        rp = ROOT / "reports"
        rp.mkdir(exist_ok=True)
        f = rp / f"trades_{a.market}_{period}.csv"
        pd.DataFrame(taken)[["event_id", "ticker", "name", "type", "title", "entry_date", "exit_date",
                             "entry_px", "exit_px", "r", "rb", "ar"]].to_csv(f, index=False)
        res["trades_csv"] = str(f.relative_to(ROOT))
    years = [v for v in res.get("by_year", {}).values() if v["trades"] > 0]
    res["years_beating_bench"] = f"{sum(v['strategy'] > v['bench'] for v in years)}/{len(years)}"
    ntr = study.log_trial("backtest", a.market, period, {
        "h": h, "n_trades": res.get("n_trades"), "cagr": res.get("cagr"),
        "sharpe": res.get("sharpe"), "mdd": res.get("max_drawdown"),
        "years_beating_bench": res["years_beating_bench"],
    })
    out({"market": a.market, "period": period, "horizon": h, "rules_hash": study.rules_hash(a.market),
         **res, "trials_logged_total": ntr,
         "caution": "시도 횟수가 늘수록 우연히 좋은 결과가 나올 확률도 커진다. 연도별 일관성과 거래 수를 먼저 본다."})


if __name__ == "__main__":
    main()
