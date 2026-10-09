"""후보 원장 채점: 추천했던 후보가 이후 지수 대비 어땠는지. PROTECTED.

  python3 core/candscore.py [--offline]

기준: 후보 선정일 다음 거래일 시가 진입 -> 20·60거래일 후 종가, 같은 기간 벤치마크(KOSPI/KOSDAQ/SPY) 대비 초과수익.
주 성적은 '신규 진입' 시점만 센다 (같은 종목이 며칠 연속 후보로 남아도 한 번).
결과는 journal/candidate-results.jsonl 에 쌓이고, 요약을 출력한다 (리서치 사이클이 가중치 조정 근거로 사용).
"""
from __future__ import annotations

import argparse
import statistics

from common import JOURNAL, append_jsonl, iso, now_utc, out, parse_utc, read_jsonl
import prices
from screen import CANDIDATES

RESULTS = JOURNAL / "candidate-results.jsonl"


def resolve(offline: bool) -> dict:
    done = {(r["date"], r["market"], r["ticker"], r["h"]) for r in read_jsonl(RESULTS)}
    new, pending = 0, 0
    refreshed: set = set()
    for c in read_jsonl(CANDIDATES):
        if not c.get("is_new"):
            continue
        for h in (20, 60):
            key = (c["date"], c["market"], c["ticker"], h)
            if key in done:
                continue
            m = c["market"]
            bench = prices.bench_ticker(m, c.get("board"))
            if not offline:
                for t in (c["ticker"], bench):
                    if (m, t) not in refreshed:
                        prices.update(m, t, max_age_hours=12)
                        refreshed.add((m, t))
            t_local = prices.actionable_local(m, c["date"], None, parse_utc(c["recorded_at"]))
            w = prices.event_window(m, c["ticker"], bench, t_local, h)
            if w["status"] != "ok":
                pending += 1
                continue
            append_jsonl(RESULTS, {"date": c["date"], "market": m, "ticker": c["ticker"], "name": c.get("name"),
                                   "sector": c.get("sector"), "h": h, "score": c["score"], "rank": c["rank"],
                                   "parts": c.get("parts", {}), "ar": round(w["ar"], 5), "r": round(w["r"], 5),
                                   "entry_date": w["entry_date"], "exit_date": w["exit_date"], "resolved_at": iso(now_utc())})
            new += 1
    return {"new_results": new, "pending": pending}


def summary() -> dict:
    rs = read_jsonl(RESULTS)
    res: dict = {}
    for h in (20, 60):
        rows = [r for r in rs if r["h"] == h]
        if not rows:
            continue
        ars = [r["ar"] for r in rows]
        d = {"n": len(rows), "mean_ar": round(statistics.fmean(ars), 4), "hit": round(sum(a > 0 for a in ars) / len(ars), 3)}
        top = [r["ar"] for r in rows if r["rank"] <= 3]
        rest = [r["ar"] for r in rows if r["rank"] > 3]
        if top:
            d["rank_top3_mean_ar"] = round(statistics.fmean(top), 4)
        if rest:
            d["rank_rest_mean_ar"] = round(statistics.fmean(rest), 4)
        # 항목별: 백분위 상위 절반 vs 하위 절반의 초과수익 차이 (가중치 조정 근거)
        comp = {}
        keys = {k for r in rows for k in r.get("parts", {})}
        for k in sorted(keys):
            hi = [r["ar"] for r in rows if r["parts"].get(k, 0) >= 50]
            lo = [r["ar"] for r in rows if k in r["parts"] and r["parts"][k] < 50]
            if len(hi) >= 5 and len(lo) >= 5:
                comp[k] = {"hi_mean_ar": round(statistics.fmean(hi), 4), "lo_mean_ar": round(statistics.fmean(lo), 4),
                           "n_hi": len(hi), "n_lo": len(lo)}
        d["by_component"] = comp
        by_m = {}
        for m in ("kr", "us"):
            mr = [r["ar"] for r in rows if r["market"] == m]
            if mr:
                by_m[m] = {"n": len(mr), "mean_ar": round(statistics.fmean(mr), 4), "hit": round(sum(a > 0 for a in mr) / len(mr), 3)}
        d["by_market"] = by_m
        d["caution"] = "30건 미만은 결론 금지" if len(rows) < 30 else ""
        res[f"h{h}"] = d
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    r = resolve(a.offline)
    out({**r, "summary": summary()})


if __name__ == "__main__":
    main()
