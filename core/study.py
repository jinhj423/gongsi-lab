"""Shared event-metric computation, rule filtering, trial logging. PROTECTED."""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import statistics

import yaml

from common import JOURNAL, STRATEGY, append_jsonl, cfg, git_rev, iso, now_utc, read_jsonl
from ingest import load_events
import prices

_cache: dict[tuple[str, int], list[dict]] = {}


def rules_path(market: str):
    return STRATEGY / f"rules_{market}.yaml"


def load_rules(market: str) -> dict:
    return yaml.safe_load(rules_path(market).read_text(encoding="utf-8")) or {}


def rules_hash(market: str) -> str:
    return hashlib.sha256(rules_path(market).read_bytes()).hexdigest()[:10]


def period_of(filed_date: str) -> str:
    c = cfg()
    if filed_date < c["in_sample_start"]:
        return "pre"
    return "holdout" if filed_date >= c["holdout_start"] else "in"


def metric_rows(market: str, h: int, period: str) -> list[dict]:
    """Every event of the market with its h-day window. period: in | holdout."""
    key = (market, h)
    if key not in _cache:
        rows = []
        for e in load_events(market):
            p = period_of(e["filed_date"])
            if p == "pre":
                continue
            t = prices.actionable_local(market, e["filed_date"], e.get("filed_at"), None)
            w = prices.event_window(market, e["ticker"], prices.bench_ticker(market, e.get("board")), t, h)
            if p == "in" and w.get("status") == "ok" and w["exit_date"] >= cfg()["holdout_start"]:
                w = {"status": "crosses_holdout"}  # 인샘플 결과가 홀드아웃 가격을 보지 않도록
            rows.append({**e, **w, "h": h, "period": p})
        _cache[key] = rows
    return [r for r in _cache[key] if r["period"] == period]


def apply_rules(rows: list[dict], rules: dict, market: str) -> list[dict]:
    types = rules.get("types") or []
    out = []
    for r in rows:
        if r["status"] != "ok":
            continue
        if types and r["type"] not in types:
            continue
        if r.get("corr") and not rules.get("include_corrections", False):
            continue
        if r["entry_px"] < float(rules.get("min_price", 0)):
            continue
        tv = r.get("traded_value20")
        if rules.get("min_traded_value") and (tv is None or tv < float(rules["min_traded_value"])):
            continue
        pr = r.get("pre_ret20")
        if rules.get("pre_ret20_min") is not None and (pr is None or pr < float(rules["pre_ret20_min"])):
            continue
        if rules.get("pre_ret20_max") is not None and (pr is None or pr > float(rules["pre_ret20_max"])):
            continue
        out.append(r)
    return out


def summarize(ars: list[float], dates: list[str]) -> dict:
    n = len(ars)
    if n == 0:
        return {"n": 0}
    by_date: dict[str, list[float]] = {}
    for a, d in zip(ars, dates):
        by_date.setdefault(d, []).append(a)
    dm = [statistics.fmean(v) for v in by_date.values()]
    t = None
    if len(dm) >= 3 and statistics.pstdev(dm) > 0:
        t = statistics.fmean(dm) / (statistics.stdev(dm) / math.sqrt(len(dm)))
    return {
        "n": n,
        "n_dates": len(dm),
        "mean_ar": round(statistics.fmean(ars), 5),
        "median_ar": round(statistics.median(ars), 5),
        "hit": round(sum(a > 0 for a in ars) / n, 4),
        "t_date_clustered": round(t, 2) if t is not None else None,
    }


def shrunk_p_up(k_up: int, n: int) -> float:
    k = cfg()["baseline_shrink_k"]
    return round((k_up + 0.5 * k) / (n + k), 4)


def log_trial(kind: str, market: str, period: str, extra: dict) -> int:
    row = {
        "ts": iso(now_utc()), "kind": kind, "market": market, "period": period,
        "rules_hash": rules_hash(market), "git_rev": git_rev(), **extra,
    }
    path = JOURNAL / ("holdout-log.jsonl" if period == "holdout" else "trials.jsonl")
    append_jsonl(path, row)
    return len(read_jsonl(JOURNAL / "trials.jsonl"))


def backtests_last_24h() -> int:
    since = now_utc() - dt.timedelta(hours=24)
    n = 0
    for r in read_jsonl(JOURNAL / "trials.jsonl"):
        if r.get("kind") == "backtest":
            if dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) >= since:
                n += 1
    return n
