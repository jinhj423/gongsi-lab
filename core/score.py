"""Calibration & paper P&L report. PROTECTED.

  python3 core/score.py

핵심 지표 brier_delta = (내 Brier) - (기저율 Brier). 음수면 이벤트 스터디 기저율보다
내 판단이 나은 예측이었다는 뜻. 표본이 작을 때(<30) 결론 금지.
"""
from __future__ import annotations

import statistics

from common import out
import ledger


def agg(rows: list[tuple[dict, dict]]) -> dict:
    if not rows:
        return {"n": 0}
    b = [r["brier"] for _, r in rows]
    bb = [r["base_brier"] for _, r in rows]
    return {
        "n": len(rows),
        "brier": round(statistics.fmean(b), 4),
        "base_brier": round(statistics.fmean(bb), 4),
        "brier_delta": round(statistics.fmean(b) - statistics.fmean(bb), 4),
        "up_rate": round(statistics.fmean([r["up"] for _, r in rows]), 3),
        "mean_est": round(statistics.fmean([f["est_prob"] for f, _ in rows]), 3),
    }


def main() -> None:
    res = ledger.resolutions()
    fc = ledger.forecasts()
    done = [(f, res[f["id"]]) for f in fc if f["id"] in res and res[f["id"]]["status"] == "ok"]
    pending = [f for f in fc if f["id"] not in res]

    def group(key):
        g: dict[str, list] = {}
        for f, r in done:
            g.setdefault(key(f), []).append((f, r))
        return {k: agg(v) for k, v in sorted(g.items())}

    buckets = {}
    for f, r in done:
        k = f"{int(f['est_prob'] * 10) / 10:.1f}"
        buckets.setdefault(k, []).append(r["up"])
    calib = {k: {"n": len(v), "actual_up": round(statistics.fmean(v), 3)} for k, v in sorted(buckets.items())}

    out({
        "forecasts": len(fc), "resolved": len(done), "pending": len(pending),
        "overall": agg(done),
        "by_market": group(lambda f: f["market"]),
        "by_type": group(lambda f: f"{f['market']}:{f['type']}"),
        "by_decision": group(lambda f: f["decision"]),
        "calibration_buckets": calib,
        "paper_book": {m: ledger.book(m) for m in ("kr", "us")},
        "recent": [{"ticker": f["ticker"], "type": f["type"], "est": f["est_prob"], "base": f["base_p"],
                    "ar": round(r["ar"], 4), "pnl": r["pnl"]} for f, r in done[-10:]],
    })


if __name__ == "__main__":
    main()
