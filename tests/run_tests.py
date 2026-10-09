"""Self-contained tests (no network). Run from repo root:

    python3 tests/run_tests.py

Copies the repo to a temp dir, builds demo data there, and checks:
  - entry timing never peeks (KR next session; US pre-open same day; forecast record time)
  - classifier on real-looking DART titles / 8-K items
  - event study + backtest + holdout guard
  - forward ledger: forecast caps, resolve, score, validate
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(cwd, env, *args, ok=True):
    p = subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True)
    if ok and p.returncode != 0:
        raise AssertionError(f"{args} failed:\n{p.stdout}\n{p.stderr}")
    return p


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "lab"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "data", "reports", "__pycache__"))
    for f in (root / "journal").glob("*.jsonl"):
        f.unlink()
    (root / "journal" / "baselines.json").unlink(missing_ok=True)
    env = {**os.environ, "LAB_DATA": str(tmp / "demo-data")}
    env.pop("LAB_OPERATOR", None)
    sys.path.insert(0, str(root / "core"))
    os.environ["LAB_DATA"] = env["LAB_DATA"]

    # ---- classifier
    from classify import kr_type, us_type
    assert kr_type("단일판매ㆍ공급계약체결") == ("supply_contract", False)
    assert kr_type("[기재정정]단일판매ㆍ공급계약체결(자율공시)") == ("supply_contract", True)
    assert kr_type("주요사항보고서(자기주식취득신탁계약체결결정)")[0] == "buyback_trust"
    assert kr_type("주요사항보고서(유상증자결정)")[0] == "rights_offering"
    assert kr_type("주요사항보고서(유무상증자결정)")[0] == "rights_offering"
    assert kr_type("주요사항보고서(무상증자결정)")[0] == "bonus_issue"
    assert kr_type("연결재무제표기준영업(잠정)실적(공정공시)")[0] == "prelim_earnings"
    assert us_type("2.02,9.01") == "earnings"
    assert us_type("7.01,9.01") == "reg_fd"
    assert us_type("1.01,2.03,9.01") == "material_agreement"
    print("classifier ok")

    # ---- source parsers on API-shaped payloads (no network)
    import sources
    raw = {"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930", "corp_cls": "Y",
           "report_nm": "[기재정정]주요사항보고서(자기주식취득결정)", "rcept_no": "20240304000123",
           "flr_nm": "삼성전자", "rcept_dt": "20240304", "rm": "유"}
    e = sources.dart_event(raw)
    assert e["event_id"] == "kr:20240304000123" and e["filed_date"] == "2024-03-04"
    assert e["type"] == "buyback" and e["corr"] and e["filed_at"] is None and e["board"] == "Y"
    assert sources.dart_event({**raw, "stock_code": " ", "corp_cls": "E"}) is None
    block = {"accessionNumber": ["0000320193-24-000006", "0000320193-24-000005"],
             "filingDate": ["2024-02-01", "2024-01-20"], "acceptanceDateTime": ["2024-02-01T16:30:12.000Z", "2024-01-20T08:00:00.000Z"],
             "form": ["8-K", "10-Q"], "items": ["2.02,9.01", ""], "primaryDocument": ["aapl-20240201.htm", "x.htm"]}
    evs = sources.sec_events("AAPL", 320193, sources._columnar(block), "2024-01-01")
    assert len(evs) == 1 and evs[0]["type"] == "earnings" and evs[0]["filed_at"] == "2024-02-01T16:30:12"
    assert evs[0]["url"].endswith("/320193/000032019324000006/aapl-20240201.htm")
    assert "본문" in sources.strip_markup("<html><style>x{}</style><p>본문&amp;</p></html>")
    print("parsers ok")

    # ---- demo data + timing
    run(root, env, "core/demo.py")
    import pandas as pd
    import prices
    sess = prices.load("kr", "KS11").index
    # KR, date-only filing on a session day -> next session
    t = prices.actionable_local("kr", "2024-03-04", None, None)
    assert prices.entry_session("kr", sess, t) == pd.Timestamp("2024-03-05")
    # KR forecast recorded 08:30 KST on 03-06 (after filing on 03-04) -> 03-06 open
    rec = dt.datetime(2024, 3, 5, 23, 30, tzinfo=dt.timezone.utc)
    t = prices.actionable_local("kr", "2024-03-04", None, rec)
    assert prices.entry_session("kr", sess, t) == pd.Timestamp("2024-03-06")
    # ...recorded 09:30 KST -> next session
    rec = dt.datetime(2024, 3, 6, 0, 30, tzinfo=dt.timezone.utc)
    t = prices.actionable_local("kr", "2024-03-04", None, rec)
    assert prices.entry_session("kr", sess, t) == pd.Timestamp("2024-03-07")
    usess = prices.load("us", "SPY").index
    # US pre-market 8-K -> same-day open; after 09:30 -> next day
    assert prices.entry_session("us", usess, prices.actionable_local("us", "2024-03-04", "2024-03-04T07:05:00", None)) == pd.Timestamp("2024-03-04")
    assert prices.entry_session("us", usess, prices.actionable_local("us", "2024-03-04", "2024-03-04T09:30:00", None)) == pd.Timestamp("2024-03-05")
    assert prices.entry_session("us", usess, prices.actionable_local("us", "2024-03-01", "2024-03-01T16:10:00", None)) == pd.Timestamp("2024-03-04")
    # Window return: entry open -> close h-1 sessions later
    w = prices.event_window("kr", "000001", "KS11", prices.actionable_local("kr", "2024-03-04", None, None), 20)
    df = prices.load("kr", "000001")
    i = df.index.get_loc(pd.Timestamp("2024-03-05"))
    assert w["entry_date"] == "2024-03-05" and w["exit_date"] == df.index[i + 19].strftime("%Y-%m-%d")
    assert abs(w["r"] - (df["Close"].iloc[i + 19] / df["Open"].iloc[i] - 1)) < 1e-12
    assert w["pre_ret20"] is not None  # uses only bars before entry
    print("timing ok")

    # ---- event study / backtest / holdout guard
    es = json.loads(run(root, env, "core/eventstudy.py", "--market", "kr").stdout)
    bb = {r["type"]: r for r in es["table"]}
    assert bb["buyback"]["mean_ar"] > 0.02 and bb["rights_offering"]["mean_ar"] < -0.03, "planted drift not recovered"
    assert "crosses_holdout" in es["window_status"]
    assert (root / "journal" / "baselines.json").exists()
    bt = json.loads(run(root, env, "core/backtest.py", "--market", "kr").stdout)
    assert bt["n_trades"] > 100 and bt["period"].split(" ~ ")[1] < "2025-10-01"
    p = run(root, env, "core/backtest.py", "--market", "kr", "--holdout", ok=False)
    assert p.returncode != 0 and "운영자" in p.stdout
    print("study ok")

    # ---- forward ledger
    feed = root / "state" / "feed.jsonl"
    ev = {"event_id": "kr:test1", "market": "kr", "ticker": "000002", "name": "데모", "board": "K",
          "type": "buyback", "corr": False, "title": "자기주식취득결정", "filed_date": "2024-03-04",
          "filed_at": None, "source_id": "test1", "url": ""}
    feed.write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")
    r = run(root, env, "core/forecast.py", "record", "--event-id", "kr:test1", "--est-prob", "0.6",
            "--decision", "bet", "--stake", "9999999", "--rationale", "x", ok=False)
    assert "stake" in r.stdout
    r = run(root, env, "core/forecast.py", "record", "--event-id", "kr:test1", "--est-prob", "0.6",
            "--decision", "bet", "--side", "short", "--stake", "100000", "--rationale", "x", ok=False)
    assert "공매도" in r.stdout
    r = json.loads(run(root, env, "core/forecast.py", "record", "--event-id", "kr:test1", "--est-prob", "0.6",
                       "--decision", "bet", "--stake", "300000", "--rationale", "test").stdout)
    assert r["book"]["open_stake"] == 300000 and r["baseline_p_up"] != 0.5
    r = run(root, env, "core/forecast.py", "record", "--event-id", "kr:test1", "--est-prob", "0.6",
            "--decision", "no-edge", "--rationale", "dup", ok=False)
    assert "이미" in r.stdout
    # backdate the forecast so demo prices cover its window
    fpath = root / "journal" / "forecasts.jsonl"
    row = json.loads(fpath.read_text(encoding="utf-8"))
    row["recorded_at"] = "2024-03-05T01:00:00Z"  # 10:00 KST 03-05 -> entry 03-06 open
    fpath.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    rs = json.loads(run(root, env, "core/resolve.py", "--offline").stdout)
    assert rs["settled"] == 1
    res = json.loads((root / "journal" / "resolutions.jsonl").read_text(encoding="utf-8"))
    assert res["entry_date"] == "2024-03-06"
    sc = json.loads(run(root, env, "core/score.py").stdout)
    assert sc["resolved"] == 1 and sc["paper_book"]["kr"]["open_bets"] == 0
    assert json.loads(run(root, env, "core/resolve.py", "--offline").stdout)["settled"] == 0  # idempotent
    run(root, env, "core/validate.py")
    print("forward ledger ok")
    shutil.rmtree(tmp)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
