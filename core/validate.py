"""Integrity tripwires. PROTECTED. Exit 1 on any violation.

  python3 core/validate.py
"""
from __future__ import annotations

import sys

from common import cfg, out
import ledger

CEIL = {
    "max_stake": {"kr": 2_000_000, "us": 2_000},
    "max_open_positions": 40,
    "max_new_bets_per_cycle": 10,
    "max_backtests_per_day": 20,
}


def main() -> None:
    c = cfg()
    errs = []
    p = c["paper"]
    for m in ("kr", "us"):
        if p["max_stake"][m] > CEIL["max_stake"][m]:
            errs.append(f"max_stake.{m} 상한 초과")
        if p["max_open_positions"][m] > CEIL["max_open_positions"]:
            errs.append(f"max_open_positions.{m} 상한 초과")
    if p["max_new_bets_per_cycle"] > CEIL["max_new_bets_per_cycle"]:
        errs.append("max_new_bets_per_cycle 상한 초과")
    if c["max_backtests_per_day"] > CEIL["max_backtests_per_day"]:
        errs.append("max_backtests_per_day 상한 초과")
    if not c["in_sample_start"] < c["holdout_start"]:
        errs.append("in_sample_start < holdout_start 아님")
    if c["primary_horizon"] not in c["horizons"]:
        errs.append("primary_horizon 이 horizons 에 없음")

    fc = ledger.forecasts()
    ids = [f["id"] for f in fc]
    if len(ids) != len(set(ids)):
        errs.append("forecasts.jsonl id 중복")
    for f in fc:
        if not 0.01 <= f["est_prob"] <= 0.99:
            errs.append(f"{f['id']} est_prob 범위 밖")
        if f["decision"] == "bet" and not 0 < f["stake"] <= p["max_stake"][f["market"]]:
            errs.append(f"{f['id']} stake 캡 위반")
        if f["decision"] != "bet" and f["stake"]:
            errs.append(f"{f['id']} 비베팅에 stake")
    seen = set()
    for r in ledger.read_jsonl(ledger.RESOLUTIONS):
        if r["id"] in seen:
            errs.append(f"resolution 중복 {r['id']}")
        seen.add(r["id"])
        if r["id"] not in set(ids):
            errs.append(f"resolution 의 forecast 없음 {r['id']}")
    out({"ok": not errs, "errors": errs, "forecasts": len(fc), "resolutions": len(seen)})
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
