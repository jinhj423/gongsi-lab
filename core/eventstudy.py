"""Event study: market-adjusted abnormal return by disclosure type. PROTECTED.

  python3 core/eventstudy.py --market kr                 # 전 유형, 인샘플, h=primary
  python3 core/eventstudy.py --market us --h 60 --by-year
  python3 core/eventstudy.py --market kr --apply-rules   # strategy/rules_kr.yaml 필터 적용
  LAB_OPERATOR=1 python3 core/eventstudy.py --market kr --holdout   # 운영자 전용

AR = (종목 시가진입 -> h거래일 후 종가) - (같은 구간 벤치마크). 진입은 공시를 실제로
알 수 있었던 시점 이후 첫 시가 (KR: 공시일 다음 거래일 시가).
t 는 같은 날짜 이벤트를 평균한 뒤 계산 (날짜 군집 보정).
인샘플 전체 유형 실행 시 journal/baselines.json (유형별 상승확률 기저율) 갱신.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from common import JOURNAL, cfg, die, is_operator, out
import study


def main() -> None:
    c = cfg()
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--h", type=int, default=c["primary_horizon"])
    ap.add_argument("--types", help="콤마 구분 유형 필터")
    ap.add_argument("--apply-rules", action="store_true")
    ap.add_argument("--by-year", action="store_true")
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    if a.h not in c["horizons"]:
        die(f"h 는 {c['horizons']} 중 하나")
    period = "holdout" if a.holdout else "in"
    if a.holdout and not is_operator():
        die("홀드아웃은 운영자 전용입니다 (LAB_OPERATOR=1).")

    rows = study.metric_rows(a.market, a.h, period)
    status = Counter(r["status"] for r in rows)
    if a.apply_rules:
        rules = study.load_rules(a.market)
        ok = study.apply_rules(rows, rules, a.market)
    else:
        ok = [r for r in rows if r["status"] == "ok" and not r.get("corr")]
    if a.types:
        want = set(a.types.split(","))
        ok = [r for r in ok if r["type"] in want]

    groups: dict[str, list[dict]] = {}
    for r in ok:
        groups.setdefault(r["type"], []).append(r)
    table = []
    for typ, rs in groups.items():
        s = study.summarize([r["ar"] for r in rs], [r["entry_date"] for r in rs])
        s["type"] = typ
        s["p_up_shrunk"] = study.shrunk_p_up(sum(r["ar"] > 0 for r in rs), len(rs))
        if a.by_year:
            yr: dict[str, list[float]] = {}
            for r in rs:
                yr.setdefault(r["entry_date"][:4], []).append(r["ar"])
            s["by_year"] = {y: {"n": len(v), "mean_ar": round(sum(v) / len(v), 4)} for y, v in sorted(yr.items())}
        table.append(s)
    table.sort(key=lambda s: -s["n"])
    shown = [s for s in table if s["n"] >= a.min_n]

    if period == "in" and not a.apply_rules and not a.types:
        bp = JOURNAL / "baselines.json"
        base = json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else {}
        base.setdefault(a.market, {})[str(a.h)] = {
            s["type"]: {"n": s["n"], "p_up": s["p_up_shrunk"], "mean_ar": s["mean_ar"]} for s in table
        }
        bp.write_text(json.dumps(base, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    ntrials = study.log_trial("eventstudy", a.market, period, {
        "h": a.h, "apply_rules": a.apply_rules, "types": a.types, "n_events": len(ok),
    })
    out({
        "market": a.market, "h": a.h, "period": period,
        "window_status": dict(status),
        "note": "no_price/suspect_adjust/halted 는 제외됨. no_price 가 많으면 상장폐지 종목 누락(생존편향) 가능성.",
        "types_below_min_n": len(table) - len(shown),
        "table": shown,
        "trials_logged_total": ntrials,
    })


if __name__ == "__main__":
    main()
