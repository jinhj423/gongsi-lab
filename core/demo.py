"""Synthetic data for trying the pipeline without API keys. PROTECTED.

  LAB_DATA=demo-data python3 core/demo.py
  LAB_DATA=demo-data python3 core/eventstudy.py --market kr

가상의 종목·지수·공시를 만든다. 일부 유형에 일부러 드리프트를 심어 두었으니
(예: buyback +, rights_offering -) 이벤트 스터디가 그걸 찾아내는지 확인하는 용도.
실제 데이터와 섞이지 않게 반드시 LAB_DATA 를 별도 폴더로 지정할 것.
"""
from __future__ import annotations

import json
import os
import random
import shutil

import numpy as np
import pandas as pd

from common import DATA, ROOT

DRIFT = {  # 20거래일 누적 초과수익 (가상)
    "kr": {"buyback": 0.03, "supply_contract": 0.015, "rights_offering": -0.04, "cb": -0.02,
           "prelim_earnings": 0.0, "dividend": 0.005, "other": 0.0},
    "us": {"earnings": 0.0, "material_agreement": 0.01, "exec_change": -0.01, "restructuring": -0.02,
           "other_event": 0.0},
}


def make(market: str, n_tickers: int, n_events: int, rng: np.random.Generator) -> None:
    days = pd.bdate_range("2017-06-01", "2026-09-30")
    nd = len(days)
    benches = ["KS11", "KQ11"] if market == "kr" else ["SPY"]
    bret = {b: rng.normal(0.0003, 0.011, nd) for b in benches}
    pdir = DATA / "prices" / market
    pdir.mkdir(parents=True, exist_ok=True)

    def write(name: str, rets: np.ndarray, p0: float) -> None:
        close = p0 * np.cumprod(1 + rets)
        gap = rng.normal(0, 0.003, nd)
        open_ = np.r_[p0, close[:-1]] * (1 + gap)
        df = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.005,
                           "Low": np.minimum(open_, close) * 0.995, "Close": close,
                           "Volume": rng.integers(50_000, 2_000_000, nd)}, index=days)
        df.index.name = "Date"
        df.to_csv(pdir / f"{name}.csv")

    for b in benches:
        write(b, bret[b], 2500.0 if market == "kr" else 300.0)

    tickers = [f"{i:06d}" if market == "kr" else f"T{i:03d}" for i in range(1, n_tickers + 1)]
    boards = {t: ("Y" if i % 2 else "K") for i, t in enumerate(tickers)}
    idio = {t: rng.normal(0, 0.02, nd) for t in tickers}
    types = list(DRIFT[market])
    events = []
    for k in range(n_events):
        t = random.choice(tickers)
        i = int(rng.integers(30, nd - 70))
        typ = random.choice(types)
        d = DRIFT[market][typ]
        # 공시 다음 날부터 20일에 걸쳐 드리프트 (진입은 다음 날 시가 이후이므로 잡을 수 있음)
        idio[t][i + 1: i + 21] += d / 20
        filed = days[i]
        ev = {
            "event_id": f"{market}:demo{k:06d}", "market": market, "ticker": t,
            "name": f"데모{t}", "board": boards[t] if market == "kr" else None,
            "type": typ, "corr": random.random() < 0.05, "title": f"[DEMO] {typ}",
            "filed_date": filed.strftime("%Y-%m-%d"),
            "filed_at": None if market == "kr" else f"{filed.strftime('%Y-%m-%d')}T{random.choice(['07:05:00', '16:10:00', '17:30:00'])}",
            "source_id": f"demo{k:06d}", "url": "",
        }
        events.append(ev)
    for t in tickers:
        bb = bret["KS11" if boards[t] == "Y" else "KQ11"] if market == "kr" else bret["SPY"]
        write(t, bb + idio[t], float(rng.uniform(2000, 80000) if market == "kr" else rng.uniform(10, 300)))
    edir = DATA / "events"
    edir.mkdir(parents=True, exist_ok=True)
    with (edir / f"{market}.jsonl").open("w", encoding="utf-8") as f:
        for e in sorted(events, key=lambda e: e["filed_date"]):
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def main() -> None:
    if "LAB_DATA" not in os.environ or DATA.resolve() == (ROOT / "data").resolve():
        raise SystemExit("LAB_DATA 를 실제 데이터와 다른 폴더로 지정하세요. 예: LAB_DATA=demo-data python3 core/demo.py")
    if DATA.exists():
        shutil.rmtree(DATA)
    random.seed(7)
    rng = np.random.default_rng(7)
    make("kr", 120, 4000, rng)
    make("us", 60, 2000, rng)
    print(json.dumps({"ok": True, "data_dir": str(DATA)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
