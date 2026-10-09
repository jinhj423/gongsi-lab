"""Disclosure watcher: no LLM, cheap, runs every 20-60 minutes. PROTECTED.

  python3 core/watch.py kr|us [--no-pull]

새 공시를 피드에 쌓고(예측 사이클과 같은 state/feed.jsonl), 그중
  - config/my_watchlist.txt 의 관심종목 공시 (모든 유형)
  - config/schedule.json market_wide_alert_types 의 유형 (시장 전체)
을 텔레그램으로 즉시 알린다. 같은 공시는 한 번만. 공시일 2일 이내만.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from common import ROOT, STATE, out
import feed
import ledger
from classify import label
import notify

STATE_P = STATE / "watch-state.json"


def watchlist() -> dict[tuple[str, str], str]:
    wl = {}
    p = ROOT / "config" / "my_watchlist.txt"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            parts = ln.split("#")[0].split()
            if len(parts) >= 2 and parts[0] in ("kr", "us"):
                wl[(parts[0], parts[1].upper())] = " ".join(parts[2:])
    return wl


def market_wide(market: str) -> set[str]:
    s = json.loads((ROOT / "config" / "schedule.json").read_text(encoding="utf-8"))
    return set(s.get("market_wide_alert_types", {}).get(market, []))


def fmt(e: dict, why: str) -> str:
    corr = " (정정)" if e.get("corr") else ""
    when = e["filed_date"][5:].replace("-", "/") + (f" {e['filed_at'][11:16]}ET" if e.get("filed_at") else "")
    name = e.get("name") or ""
    return (f"[{e['market'].upper()}] {name}({e['ticker']}) · {e['title']}{corr}\n"
            f"   {why} · {when} · {e.get('url', '')}")


def run(market: str, pull: bool) -> dict:
    wl = watchlist()
    if pull:
        if market == "kr":
            feed.pull_kr(2)
        else:
            feed.pull_us(2, extra=[t for (m, t) in wl if m == "us"])
    state = json.loads(STATE_P.read_text()) if STATE_P.exists() else {"alerted": []}
    alerted = set(state["alerted"])
    cutoff = (dt.date.today() - dt.timedelta(days=2)).isoformat()
    wide = market_wide(market)
    hits = []
    for e in ledger.feed_events().values():
        if e["market"] != market or e["event_id"] in alerted or e["filed_date"] < cutoff:
            continue
        why = None
        if (market, e["ticker"].upper()) in wl:
            memo = wl[(market, e["ticker"].upper())]
            why = "관심종목" + (f"({memo})" if memo and memo != e.get("name") else "")
        elif e["type"] in wide:
            why = f"시장전체:{label(e['type'])}"
        if why:
            hits.append(fmt(e, why))
            alerted.add(e["event_id"])
    if hits:
        ok = notify.send(f"📌 공시 알림 {len(hits)}건\n\n" + "\n\n".join(hits), kind="alert")
        if not ok:
            return {"alerts": len(hits), "sent": False}
    STATE_P.parent.mkdir(parents=True, exist_ok=True)
    STATE_P.write_text(json.dumps({"alerted": sorted(alerted)[-5000:]}))
    return {"alerts": len(hits), "sent": True}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("market", choices=["kr", "us"])
    ap.add_argument("--no-pull", action="store_true")
    a = ap.parse_args()
    out(run(a.market, not a.no_pull))


if __name__ == "__main__":
    main()
