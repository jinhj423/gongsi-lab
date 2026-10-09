"""Record a forecast (and optionally a paper bet). PROTECTED. Append-only.

  python3 core/forecast.py record --event-id kr:2026... --est-prob 0.62 \
      --decision bet|no-edge|low-confidence|skip-other \
      [--stake 300000] [--side long|short] [--h 20] \
      --rationale "..." [--note "..."]

est-prob = 이 종목이 h거래일 동안 벤치마크를 이길 확률 (P(AR>0)).
진입가는 기록 시점 이후 첫 시가로 자동 결정 - 기록이 늦으면 진입도 늦다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib

from common import append_jsonl, cfg, die, git_rev, iso, now_utc, out
import ledger


def main() -> None:
    c = cfg()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--event-id", required=True)
    r.add_argument("--est-prob", type=float, required=True)
    r.add_argument("--decision", required=True, choices=["bet", "no-edge", "low-confidence", "skip-other"])
    r.add_argument("--h", type=int, default=c["primary_horizon"])
    r.add_argument("--stake", type=float, default=0.0)
    r.add_argument("--side", choices=["long", "short"], default="long")
    r.add_argument("--rationale", required=True)
    r.add_argument("--note", default="")
    a = ap.parse_args()

    ev = ledger.feed_events().get(a.event_id)
    if ev is None:
        die("state/feed.jsonl 에 없는 event_id")
    if a.h not in c["horizons"]:
        die(f"h 는 {c['horizons']} 중 하나")
    if not 0.01 <= a.est_prob <= 0.99:
        die("est-prob 는 0.01~0.99")
    fid = hashlib.sha1(f"{a.event_id}|{a.h}".encode()).hexdigest()[:12]
    if any(f["id"] == fid for f in ledger.forecasts()):
        die("이미 기록된 (event, h) 입니다")

    m = ev["market"]
    p = c["paper"]
    stake = 0.0
    if a.decision == "bet":
        if a.side == "short" and not p["allow_short"][m]:
            die(f"{m} 은 공매도 비허용")
        if a.stake <= 0 or a.stake > p["max_stake"][m]:
            die(f"stake 는 0 초과 {p['max_stake'][m]} 이하")
        b = ledger.book(m)
        if b["open_bets"] >= p["max_open_positions"][m]:
            die("오픈 포지션 한도 도달 - 정책이 작동한 것, 오류 아님")
        if a.stake > b["cash"]:
            die("현금 부족")
        recent = now_utc() - dt.timedelta(hours=3)
        n_new = sum(1 for f in ledger.forecasts()
                    if f["decision"] == "bet" and dt.datetime.fromisoformat(f["recorded_at"].replace("Z", "+00:00")) >= recent)
        if n_new >= p["max_new_bets_per_cycle"]:
            die("사이클당 신규 베팅 한도 도달")
        stake = a.stake
    elif a.stake:
        die("bet 가 아니면 stake 를 주지 말 것")

    base, src = ledger.baseline_p(m, a.h, ev["type"])
    row = {
        "id": fid, "recorded_at": iso(now_utc()), "event_id": a.event_id, "market": m,
        "ticker": ev["ticker"], "name": ev.get("name", ""), "board": ev.get("board"),
        "type": ev["type"], "title": ev["title"], "filed_date": ev["filed_date"], "filed_at": ev.get("filed_at"),
        "h": a.h, "est_prob": round(a.est_prob, 4), "base_p": base, "base_src": src,
        "decision": a.decision, "side": a.side if a.decision == "bet" else None, "stake": stake,
        "rationale": a.rationale, "note": a.note, "strategy_rev": git_rev(),
    }
    append_jsonl(ledger.FORECASTS, row)
    out({"ok": True, "id": fid, "baseline_p_up": base, "baseline_src": src,
         "your_edge_vs_baseline": round(a.est_prob - base, 4), "book": ledger.book(m)})


if __name__ == "__main__":
    main()
