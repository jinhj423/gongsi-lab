"""시총 상위 비교·후보 선정 파이프라인 (네트워크 없이, 합성 데이터).

    python3 tests/test_screen.py
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "lab"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "data", "reports", "__pycache__"))
    for f in list((root / "journal").glob("*.jsonl")) + list((root / "state").glob("*.json*")):
        f.unlink()
    data, home, vault = tmp / "data", tmp / "home", tmp / "vault"
    home.mkdir()
    vault.mkdir()
    for k in ("LAB_OPERATOR", "LAB_AGENT", "OBSIDIAN_VAULT", "CHART_IMG_API_KEY", "TELEGRAM_BOT_TOKEN"):
        os.environ.pop(k, None)
    env = {**os.environ, "LAB_DATA": str(data), "HOME": str(home), "LAB_TELEGRAM_DRYRUN": "1", "VAULT_DIR": str(vault)}
    os.environ.update({k: env[k] for k in ("LAB_DATA", "HOME", "LAB_TELEGRAM_DRYRUN", "VAULT_DIR")})
    subprocess.run([sys.executable, "core/demo.py"], cwd=root, env=env, check=True, capture_output=True)
    sys.path.insert(0, str(root / "core"))
    import fundamentals as F

    # ---- DART quarterly reconstruction: 3-month amounts, cumulative guard, Q4 = FY - 9M cumulative
    acc: dict = {}
    rows = lambda q_amt, q_add: [  # noqa: E731
        {"stock_code": "005930", "account_nm": "매출액", "fs_div": "CFS", "thstrm_amount": q_amt, "thstrm_add_amount": q_add},
        {"stock_code": "005930", "account_nm": "매출액", "fs_div": "OFS", "thstrm_amount": "1", "thstrm_add_amount": "1"},
        {"stock_code": "005930", "account_nm": "영업이익", "fs_div": "CFS", "thstrm_amount": str(int(q_amt.replace(",", "")) // 10), "thstrm_add_amount": (str(int(q_add.replace(",", "")) // 10) if q_add else "")},
    ]
    F.parse_multi(rows("100", "100"), 2025, "Q1", acc)
    F.parse_multi(rows("120", "220"), 2025, "Q2", acc)            # 당기=3개월, 누적=220
    F.parse_multi(rows("350", "350"), 2025, "Q3", acc)            # 당기가 누적으로 온 경우 -> 350-220=130
    F.parse_multi(rows("500", ""), 2025, "Q4", acc)               # 사업보고서 연간 500 -> Q4 = 500-350 = 150
    F.parse_multi(rows("80", "80"), 2024, "Q1", acc)
    q = F.kr_quarters(acc, "005930", [2024, 2025])
    assert [q[f"2025Q{i}"]["rev"] for i in (1, 2, 3, 4)] == [100, 120, 130, 150], q
    assert q["2025Q2"]["op"] == 12 and q["2025Q4"]["op"] == 15, q
    m = F.metrics({**q, "2024Q4": {"rev": 100, "op": -5, "ni": 1}}, None)
    assert m["latest_q"] == "2025Q4" and abs(m["rev_yoy"] - 0.5) < 1e-9 and m["turnaround"] is True, m
    ttm = F.metrics({"2025Q1": {"rev": 1, "op": 1, "ni": 10}, "2025Q2": {"rev": 1, "op": 1, "ni": 10},
                     "2025Q3": {"rev": 1, "op": 1, "ni": 10}, "2025Q4": {"rev": 1, "op": 1, "ni": 10},
                     "_equity": 200}, 1000)
    assert ttm["ttm_ni"] == 40 and ttm["per"] == 25 and ttm["pbr"] == 5
    gap = F.metrics({"2025Q1": {"rev": 1, "ni": 1}, "2025Q2": {"rev": 1, "ni": 1}, "2025Q4": {"rev": 1, "ni": 1},
                     "2026Q1": {"rev": 1, "ni": 1}}, 1000)
    assert "ttm_ni" not in gap  # 분기 누락 시 TTM 계산 안 함
    assert F.recent_cal_quarters(3, __import__("datetime").date(2026, 2, 10)) == ["2025Q4", "2025Q3", "2025Q2"]
    print("fundamentals ok")

    # ---- synthetic universe + fundamentals from demo tickers
    import prices
    import universe
    import screen
    import candidates
    rnd = random.Random(3)
    tickers = sorted(p.stem for p in (data / "prices" / "kr").glob("0*.csv"))[:60]
    uni = []
    for i, t in enumerate(tickers):
        df = prices.load("kr", t)
        uni.append({"ticker": t, "name": f"회사{t[-3:]}", "board": "Y" if i % 2 else "K",
                    "market_cap": float(df["Close"].iloc[-1]) * 1e7 * (60 - i), "close": float(df["Close"].iloc[-1]),
                    "cap_rank": i + 1, "sector": ["반도체", "2차전지", "자동차", "금융"][i % 4], "industry": ""})
    universe.save("kr", uni, "test")
    comps = {}
    for u in uni:
        qd = {}
        for y in (2024, 2025):
            for qq in (1, 2, 3, 4):
                qd[f"{y}Q{qq}"] = {"rev": 100 + rnd.uniform(-20, 40), "op": rnd.uniform(-5, 20), "ni": rnd.uniform(1, 10)}
        comps[u["ticker"]] = {"quarters": qd, **F.metrics(qd, u["market_cap"])}
    F.save("kr", comps)

    r = screen.run("kr", refresh=False)
    res = screen.load("kr")
    assert res["scored"] > 30 and 0 < len(res["candidates"]) <= 10, r
    for c in res["candidates"]:
        assert not c["gate_fails"] and c["above_sma200"] and c["sma200_slope20"] > 0
        assert (c["op_yoy"] is not None and c["op_yoy"] >= 0) or c["turnaround"]
        assert c["is_new"] is True
    ranks = [c["rank"] for c in res["candidates"]]
    assert ranks == sorted(ranks)
    led = [json.loads(x) for x in (root / "journal" / "candidates.jsonl").read_text().splitlines()]
    assert len(led) == len(res["candidates"])
    screen.run("kr", refresh=False)  # 같은 날 다시: 원장 중복 없음, 이번엔 신규 아님
    led2 = (root / "journal" / "candidates.jsonl").read_text().splitlines()
    assert len(led2) == len(led) and not any(c["is_new"] for c in screen.load("kr")["candidates"])
    # 가중치 변경이 순위에 반영
    y = (root / "strategy" / "screen.yaml").read_text(encoding="utf-8")
    (root / "strategy" / "screen.yaml").write_text(y.replace("rs_126: 20", "rs_126: 0").replace("op_yoy: 25", "op_yoy: 80"), encoding="utf-8")
    top_op = screen.load("kr")["candidates"][0]["ticker"]
    screen.run("kr", refresh=False)
    assert screen.load("kr")["weights_rev"] is not None
    (root / "strategy" / "screen.yaml").write_text(y, encoding="utf-8")
    print("screen ok")

    # ---- card + show + run (dry-run telegram), no chart key -> no photo
    card = candidates.card(res)
    assert "📈 매매 후보 · 국내" in card and "🆕" in card and "점수" in card and "영업이익" in card
    dry = data / "notify-dryrun.log"
    dry.unlink(missing_ok=True)
    # 새 후보가 생기도록 원장 기준 상태를 지움
    (root / "state" / "screen-kr.json").unlink()
    out = candidates.run("kr", refresh=False)
    assert out["charts"] == 0 and out["new"], out
    assert "[report]\n📈 매매 후보" in dry.read_text(encoding="utf-8")
    sh = candidates.show("kr")
    assert 1 <= len(sh["new_candidates"]) <= 3 and sh["new_candidates"][0]["quarters"]
    assert sh["new_candidates"][0]["tradingview"].startswith("https://www.tradingview.com/chart/?symbol=KRX:")
    # 차트 키가 있으면 사진 발송 (chart-img 응답 모의)
    os.environ["CHART_IMG_API_KEY"] = "k"
    fake = mock.Mock(status_code=200, headers={"content-type": "image/png"}, content=b"\x89PNG...")
    with mock.patch("requests.post", return_value=fake) as post:
        (root / "state" / "screen-kr.json").unlink()
        (root / "journal" / "candidates.jsonl").write_text("")
        out = candidates.run("kr", refresh=False)
        body = post.call_args.kwargs["json"]
        assert body["symbol"].startswith("KRX:") and post.call_args.kwargs["headers"]["x-api-key"] == "k"
    assert out["charts"] >= 1 and "[photo" in dry.read_text(encoding="utf-8")
    os.environ.pop("CHART_IMG_API_KEY")
    print("candidate card/show/chart ok")

    # ---- candscore: a past new-entry resolves against the benchmark
    import candscore
    with (root / "journal" / "candidates.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": "2026-03-02", "recorded_at": "2026-03-02T07:10:00Z", "market": "kr",
                            "ticker": tickers[0], "name": "x", "board": "Y", "sector": "반도체", "rank": 1,
                            "score": 90, "parts": {"op_yoy": 80}, "is_new": True}) + "\n")
    r = candscore.resolve(offline=True)
    assert r["new_results"] >= 2, r  # h=20, h=60
    rs = [json.loads(x) for x in (root / "journal" / "candidate-results.jsonl").read_text().splitlines()]
    assert {x["h"] for x in rs} == {20, 60} and rs[0]["entry_date"] == "2026-03-03"
    assert candscore.resolve(offline=True)["new_results"] == 0
    assert "h20" in candscore.summary()
    print("candscore ok")

    # ---- vault: candidate companies get notes with comparison table + report; dashboard lists candidates
    rep = root / "journal" / "reports"
    rep.mkdir(exist_ok=True)
    c0 = screen.load("kr")["candidates"][0]
    (rep / f"kr-{c0['ticker']}.md").write_text(f"# {c0['name']} 후보 리포트\n\n## 0) 한 줄 결론\n테스트 결론\n", encoding="utf-8")
    sent = root / "journal" / "outbox" / "sent"
    sent.mkdir(parents=True, exist_ok=True)
    (sent / "20261009-0710-candidates-kr.md").write_text(f"📝 신규 후보 리포트\n▶ {c0['name']}({c0['ticker']})\n", encoding="utf-8")
    p = subprocess.run([sys.executable, "core/vault.py", "export"], cwd=root, env={**env, "PATH": "/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    note = next((vault / "공시랩" / "섹터").rglob(f"*({c0['ticker']}).md"))
    t = note.read_text(encoding="utf-8")
    assert "## 시총 상위 비교" in t and "🟢 현재 매매 후보" in t and "### 0) 한 줄 결론" in t and "테스트 결론" in t, t[:800]
    dash = (vault / "공시랩" / "대시보드.md").read_text(encoding="utf-8")
    assert "## 매매 후보 · 국내" in dash and f"[[{note.stem}]]" in dash
    assert list((vault / "공시랩" / "보고").rglob("*후보 리포트 (국내).md"))
    print("vault candidates ok")

    # ---- daemon: no new candidates -> Claude not invoked
    import daemon
    calls = []
    real = subprocess.run

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        if "candidates.py" in " ".join(map(str, cmd)):
            return mock.Mock(returncode=0, stdout=json.dumps({"new": []}), stderr="")
        return real(cmd, *a, **k)
    with mock.patch.object(daemon.subprocess, "run", side_effect=fake_run):
        ok, msg = daemon.run_job("candidates kr", 5)
    assert ok and "생략" in msg and not any("loop.sh" in " ".join(map(str, c)) for c in calls)
    print("daemon skip ok")

    shutil.rmtree(tmp)
    print("ALL SCREEN TESTS PASSED")


if __name__ == "__main__":
    main()
