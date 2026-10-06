"""Historical ingestion (run by the operator, not the agent). PROTECTED.

  python3 core/ingest.py kr --from 2018-01-01 [--to 2026-09-30] [--types B,I]
  python3 core/ingest.py us --tickers-file strategy/universe_us.txt --from 2018-01-01
  python3 core/ingest.py prices kr      # 이벤트에 등장한 모든 종목 + 벤치마크 가격 캐시
  python3 core/ingest.py prices us

DART: 하루 단위로 조회, 중단돼도 $LAB_DATA/events/kr.state.json 에서 이어감.
      일일 호출 한도(약 2만 건)에 걸리면 상태 저장 후 종료 -> 다음 날 다시 실행.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from common import DATA, append_jsonl, cfg, read_jsonl
import prices
import sources


def events_path(market: str):
    return DATA / "events" / f"{market}.jsonl"


def load_events(market: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for e in read_jsonl(events_path(market)):
        seen[e["event_id"]] = e
    return sorted(seen.values(), key=lambda e: (e["filed_date"], e.get("filed_at") or "", e["event_id"]))


def ingest_kr(start: str, end: str, types: list[str]) -> None:
    state_p = DATA / "events" / "kr.state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    d = dt.date.fromisoformat(start)
    if state.get("done_through") and state.get("types") == types:
        d = max(d, dt.date.fromisoformat(state["done_through"]) + dt.timedelta(days=1))
    last = dt.date.fromisoformat(end)
    n_total = 0
    while d <= last:
        if d.weekday() < 5:
            try:
                raw = sources.dart_list_day(d.strftime("%Y%m%d"), types)
            except sources.SourceError as e:
                print(f"중단 ({d}): {e}. 상태 저장됨, 나중에 다시 실행하세요.", file=sys.stderr)
                break
            n = 0
            for r in raw:
                ev = sources.dart_event(r)
                if ev:
                    append_jsonl(events_path("kr"), ev)
                    n += 1
            n_total += n
            print(f"{d} {len(raw)}건 조회, 상장사 {n}건 저장", file=sys.stderr)
        state = {"done_through": d.isoformat(), "types": types}
        state_p.parent.mkdir(parents=True, exist_ok=True)
        state_p.write_text(json.dumps(state))
        d += dt.timedelta(days=1)
    print(json.dumps({"saved": n_total, "done_through": state.get("done_through")}))


def ingest_us(tickers: list[str], since: str) -> None:
    tmap = sources.sec_ticker_map()
    have = {e["event_id"] for e in read_jsonl(events_path("us"))}
    n_total, missing = 0, []
    for t in tickers:
        info = tmap.get(t.upper().replace(".", "-")) or tmap.get(t.upper())
        if not info:
            missing.append(t)
            continue
        try:
            rows = sources.sec_filings(info["cik"], since)
        except Exception as e:  # noqa: BLE001
            print(f"{t}: 실패 {e}", file=sys.stderr)
            continue
        n = 0
        for ev in sources.sec_events(t, info["cik"], rows, since):
            if ev["event_id"] not in have:
                ev["name"] = info["title"]
                append_jsonl(events_path("us"), ev)
                have.add(ev["event_id"])
                n += 1
        n_total += n
        print(f"{t}: 8-K {n}건", file=sys.stderr)
    print(json.dumps({"saved": n_total, "unknown_tickers": missing}))


def ingest_prices(market: str, start: str) -> None:
    evs = load_events(market)
    tickers = sorted({e["ticker"] for e in evs})
    benches = sorted(set(v for k, v in cfg()["benchmarks"][market].items() if k != "_comment"))
    ok = 0
    for i, t in enumerate(benches + tickers):
        df = prices.update(market, t, start=start, max_age_hours=12)
        ok += df is not None
        if i % 50 == 0:
            print(f"{i}/{len(benches) + len(tickers)}", file=sys.stderr)
    print(json.dumps({"tickers": len(tickers), "with_prices": ok - len(benches)}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["kr", "us", "prices"])
    ap.add_argument("market", nargs="?")
    ap.add_argument("--from", dest="start", default="2018-01-01")
    ap.add_argument("--to", dest="end", default=dt.date.today().isoformat())
    ap.add_argument("--types", default="B,I", help="DART 공시유형 코드 (B=주요사항보고, I=거래소공시, D=지분공시 ...)")
    ap.add_argument("--tickers-file")
    a = ap.parse_args()
    if a.what == "kr":
        ingest_kr(a.start, a.end, a.types.split(","))
    elif a.what == "us":
        if not a.tickers_file:
            sys.exit("--tickers-file 필요")
        tickers = [ln.split("#")[0].strip() for ln in open(a.tickers_file, encoding="utf-8")]
        ingest_us([t for t in tickers if t], a.start)
    else:
        if a.market not in ("kr", "us"):
            sys.exit("prices kr|us")
        price_start = (pd.Timestamp(a.start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        ingest_prices(a.market, price_start)


if __name__ == "__main__":
    main()
