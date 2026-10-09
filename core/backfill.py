"""과거 데이터 조금씩 채우기 (매일 밤 1회, 시간 예산 안에서). PROTECTED.

  python3 core/backfill.py [--minutes 40]

순서: KR 공시(DART, 2018~) -> KR 가격 -> US 8-K(SEC) -> US 가격 -> 피드 정리.
DART 일일 한도·시간 예산에 걸리면 저장하고 멈췄다가 다음 날 이어간다.
처음 며칠은 과거 수집, 그 뒤로는 하루치만 채우므로 금방 끝난다.
모두 끝나면 data/backfill.json 에 complete 가 기록되고, 리서치 사이클이 이벤트 스터디를 돌릴 수 있다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

import pandas as pd

from common import DATA, STRATEGY, cfg, out, secret
import feed
import ingest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=40)
    a = ap.parse_args()
    t0 = time.time()
    left = lambda: max(0.0, a.minutes - (time.time() - t0) / 60)  # noqa: E731
    start = cfg()["in_sample_start"]
    price_start = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    today = dt.date.today().isoformat()
    res: dict = {}
    if secret("DART_API_KEY", "dart-key"):
        res["kr_events"] = ingest.ingest_kr(start, today, ["B", "I"], max_minutes=left() * 0.6)
        if left() > 1:
            res["kr_prices"] = ingest.ingest_prices("kr", price_start, max_minutes=left() * 0.6)
    else:
        res["kr"] = "DART_API_KEY 없음 - 건너뜀"
    if secret("SEC_USER_AGENT", "sec-user-agent"):
        if left() > 1:
            tickers = [ln.split("#")[0].strip() for ln in (STRATEGY / "universe_us.txt").read_text(encoding="utf-8").splitlines()]
            ingest.ingest_us([t for t in tickers if t], start)
            res["us_events"] = "ok"
        if left() > 1:
            res["us_prices"] = ingest.ingest_prices("us", price_start, max_minutes=left())
    else:
        res["us"] = "SEC_USER_AGENT 없음 - 건너뜀"
    res["feed_pruned"] = feed.prune()
    done = all(isinstance(v, dict) and v.get("complete", True) for k, v in res.items() if k.endswith(("events", "prices")))
    res["complete"] = done
    p = DATA / "backfill.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": dt.datetime.now().isoformat(timespec="minutes"), **res}, ensure_ascii=False, default=str))
    (DATA / ".dirty").touch()
    out(res)


if __name__ == "__main__":
    main()
