"""Forward ledger state helpers (read-only views over append-only files). PROTECTED."""
from __future__ import annotations

import json

from common import JOURNAL, cfg, read_jsonl

FEED = JOURNAL / "feed.jsonl"
FORECASTS = JOURNAL / "forecasts.jsonl"
RESOLUTIONS = JOURNAL / "resolutions.jsonl"


def feed_events() -> dict[str, dict]:
    return {e["event_id"]: e for e in read_jsonl(FEED)}


def forecasts() -> list[dict]:
    return read_jsonl(FORECASTS)


def resolutions() -> dict[str, dict]:
    return {r["id"]: r for r in read_jsonl(RESOLUTIONS)}


def baseline_p(market: str, h: int, typ: str) -> tuple[float, str]:
    p = JOURNAL / "baselines.json"
    if p.exists():
        b = json.loads(p.read_text(encoding="utf-8")).get(market, {}).get(str(h), {}).get(typ)
        if b:
            return float(b["p_up"]), f"eventstudy n={b['n']}"
    return 0.5, "none(0.5)"


def book(market: str) -> dict:
    """Paper book for one market: cash, open bets, realized pnl."""
    c = cfg()["paper"]
    res = resolutions()
    open_bets, realized, n_settled, wins = [], 0.0, 0, 0
    for f in forecasts():
        if f["market"] != market or f["decision"] != "bet":
            continue
        r = res.get(f["id"])
        if r is None:
            open_bets.append(f)
        elif r.get("status") == "ok":
            realized += r["pnl"]
            n_settled += 1
            wins += r["pnl"] > 0
        # void: stake returned, no pnl
    staked = sum(f["stake"] for f in open_bets)
    bank = c["bankroll"][market]
    return {
        "bankroll": bank, "realized_pnl": round(realized, 2), "open_stake": round(staked, 2),
        "cash": round(bank + realized - staked, 2), "open_bets": len(open_bets),
        "settled_bets": n_settled, "wins": wins,
    }
