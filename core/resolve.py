"""Settle forecasts whose horizon has elapsed. PROTECTED. Append-only.

  python3 core/resolve.py [--offline]

정답 = 기록 시점 이후 첫 시가 진입 -> h거래일째 종가, 벤치마크 대비 초과수익(AR) > 0 인가.
베팅 손익 = stake * (방향 * 단순수익률) - stake * 왕복비용.
거래정지/상폐로 120일 넘게 정산 불가하면 void (스테이크 반환, 채점 제외).
"""
from __future__ import annotations

import argparse

from common import append_jsonl, cfg, iso, now_utc, out, parse_utc
import ledger
import prices


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    c = cfg()
    res = ledger.resolutions()
    settled, pending, voided = [], 0, 0
    refreshed: set[tuple[str, str]] = set()
    for f in ledger.forecasts():
        if f["id"] in res:
            continue
        m = f["market"]
        bench = prices.bench_ticker(m, f.get("board"))
        if not a.offline:
            for t in (f["ticker"], bench):
                if (m, t) not in refreshed:
                    prices.update(m, t, max_age_hours=6)
                    refreshed.add((m, t))
        t_local = prices.actionable_local(m, f["filed_date"], f.get("filed_at"), parse_utc(f["recorded_at"]))
        w = prices.event_window(m, f["ticker"], bench, t_local, f["h"])
        age = (now_utc() - parse_utc(f["recorded_at"])).days
        if w["status"] == "ok":
            up = 1.0 if w["ar"] > 0 else 0.0
            pnl = 0.0
            if f["decision"] == "bet":
                sgn = -1 if f["side"] == "short" else 1
                ret = w["exit_px"] / w["entry_px"] - 1
                pnl = f["stake"] * (sgn * ret) - f["stake"] * c["cost_bps_roundtrip"][m] / 1e4
            row = {
                "id": f["id"], "resolved_at": iso(now_utc()), "status": "ok",
                **{k: w[k] for k in ("entry_date", "exit_date", "entry_px", "exit_px", "r", "rb", "ar")},
                "up": up, "brier": round((f["est_prob"] - up) ** 2, 5),
                "base_brier": round((f["base_p"] - up) ** 2, 5), "pnl": round(pnl, 2),
            }
            append_jsonl(ledger.RESOLUTIONS, row)
            settled.append({"id": f["id"], "ticker": f["ticker"], "type": f["type"], "est": f["est_prob"],
                            "ar": round(w["ar"], 4), "pnl": row["pnl"]})
        elif w["status"] in ("halted", "no_trading", "no_price", "suspect_adjust") and age > 120:
            append_jsonl(ledger.RESOLUTIONS, {"id": f["id"], "resolved_at": iso(now_utc()),
                                              "status": "void", "reason": w["status"]})
            voided += 1
        else:
            pending += 1
    out({"settled": len(settled), "voided": voided, "pending": pending, "new": settled})


if __name__ == "__main__":
    main()
