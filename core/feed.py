"""Live disclosure feed -> candidates for forecasting. PROTECTED.

  python3 core/feed.py kr [--days 1]      # DART: 최근 N일 공시 수집 (공시유형 B, I)
  python3 core/feed.py us [--days 3]      # SEC: strategy/universe_us.txt 종목들의 최근 8-K
  python3 core/feed.py list [--offline]   # 아직 예측 기록이 없는 관심 유형 후보

새 이벤트는 state/feed.jsonl 에 쌓인다(중복 제거). 후보는 strategy/watch.yaml 의
유형만, 정정공시 제외, 공시 후 7일 이내. 기저율은 일부러 보여주지 않는다 -
추정치를 먼저 세우고 기록하라 (기록 후 출력에 나온다).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import yaml

from common import MARKETS, STRATEGY, append_jsonl, cfg, out
import ledger
import prices
import sectors
import sources


def watch() -> dict:
    return yaml.safe_load((STRATEGY / "watch.yaml").read_text(encoding="utf-8")) or {}


def pull_kr(days: int) -> list[dict]:
    today = dt.datetime.now(MARKETS["kr"]["tz"]).date()
    have = ledger.feed_events()
    new: list[dict] = []
    for k in range(days):
        d = today - dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        for raw in sources.dart_list_day(d.strftime("%Y%m%d"), ["B", "I"]):
            ev = sources.dart_event(raw)
            if ev and ev["event_id"] not in have:
                append_jsonl(ledger.FEED, ev)
                have[ev["event_id"]] = ev
                new.append(ev)
    return new


def pull_us(days: int, extra: list[str] | None = None) -> list[dict]:
    since = (dt.datetime.now(MARKETS["us"]["tz"]).date() - dt.timedelta(days=days)).isoformat()
    tickers = [ln.split("#")[0].strip() for ln in (STRATEGY / "universe_us.txt").read_text(encoding="utf-8").splitlines()]
    tickers = list(dict.fromkeys([t.upper() for t in tickers if t] + [t.upper() for t in (extra or [])]))
    tmap = sources.sec_ticker_map()
    have = ledger.feed_events()
    new: list[dict] = []
    for t in tickers:
        info = tmap.get(t.upper().replace(".", "-")) or tmap.get(t.upper())
        if not info:
            print(f"{t}: CIK 없음", file=sys.stderr)
            continue
        try:
            j = sources.sec_get(f"https://data.sec.gov/submissions/CIK{info['cik']:010d}.json").json()
        except Exception as e:  # noqa: BLE001
            print(f"{t}: {e}", file=sys.stderr)
            continue
        for ev in sources.sec_events(t, info["cik"], sources._columnar(j["filings"]["recent"]), since):
            if ev["event_id"] not in have:
                ev["name"] = info["title"]
                append_jsonl(ledger.FEED, ev)
                have[ev["event_id"]] = ev
                new.append(ev)
    return new


def tracked() -> set[tuple[str, str]]:
    """Companies whose disclosures we keep forever: watchlist, forecasted, or with agent notes."""
    from common import JOURNAL, ROOT, read_jsonl
    keep: set[tuple[str, str]] = set()
    p = ROOT / "config" / "my_watchlist.txt"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            parts = ln.split("#")[0].split()
            if len(parts) >= 2 and parts[0] in ("kr", "us"):
                keep.add((parts[0], parts[1].upper()))
    for f in ledger.forecasts():
        keep.add((f["market"], f["ticker"].upper()))
    for c in read_jsonl(JOURNAL / "candidates.jsonl"):
        keep.add((c["market"], c["ticker"].upper()))
    for d in (JOURNAL / "notes", JOURNAL / "reports"):
        if not d.exists():
            continue
        for n in d.glob("*.md"):
            if "-" in n.stem:
                m, t = n.stem.split("-", 1)
                keep.add((m, t.upper()))
    return keep


def prune(keep_days: int = 14) -> int:
    """Drop old feed events of untracked companies. Returns removed count."""
    if not ledger.FEED.exists():
        return 0
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    keep = tracked()
    rows = list(ledger.feed_events().values())
    kept = [e for e in rows if e["filed_date"] >= cutoff or (e["market"], e["ticker"].upper()) in keep]
    if len(kept) != len(rows):
        tmp = ledger.FEED.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in sorted(kept, key=lambda e: (e["filed_date"], e["event_id"])):
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(ledger.FEED)
    return len(rows) - len(kept)


def candidates(offline: bool) -> list[dict]:
    w = watch()
    done = {f["event_id"] for f in ledger.forecasts()}
    cutoff = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    cands = []
    for e in ledger.feed_events().values():
        if e["event_id"] in done or e.get("corr") or e["filed_date"] < cutoff:
            continue
        if e["type"] not in (w.get(e["market"]) or []):
            continue
        df = prices.load(e["market"], e["ticker"]) if offline else prices.update(e["market"], e["ticker"])
        tv = None
        last = None
        if df is not None and len(df) >= 20:
            tail = df.iloc[-20:]
            tv = float((tail["Close"] * tail["Volume"]).mean())
            last = float(df["Close"].iloc[-1])
        cands.append({k: e.get(k) for k in ("event_id", "market", "ticker", "name", "type", "title",
                                              "filed_date", "filed_at", "url")}
                     | {"sector": sectors.lookup(e["market"], e["ticker"])["sector"],
                        "last_close": last, "traded_value20": tv})
    cands.sort(key=lambda c: (c["filed_date"], c["event_id"]), reverse=True)
    return cands


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["kr", "us", "list"])
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if a.what == "kr":
        out({"new_events": len(pull_kr(a.days or 1))})
    elif a.what == "us":
        out({"new_events": len(pull_us(a.days or 3))})
    else:
        c = candidates(a.offline)
        lim = int(watch().get("max_candidates_per_cycle", 10))
        out({"n_candidates": len(c), "shown": min(len(c), lim), "primary_horizon": cfg()["primary_horizon"],
             "candidates": c[:lim]})


if __name__ == "__main__":
    main()
