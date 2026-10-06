"""Obsidian export, sector lookup, Telegram topic routing (no network).

    python3 tests/test_vault.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "lab"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "data", "reports", "__pycache__"))
    for f in (root / "journal").glob("*.jsonl"):
        f.unlink()
    data, home, vault = tmp / "data", tmp / "home", tmp / "vault"
    home.mkdir()
    vault.mkdir()
    env = {**os.environ, "LAB_DATA": str(data), "HOME": str(home), "LAB_TELEGRAM_DRYRUN": "1",
           "VAULT_DIR": str(vault), "PATH": "/usr/bin:/bin"}  # no `ob` on PATH -> local only
    for k in ("LAB_OPERATOR", "LAB_AGENT"):
        env.pop(k, None)
    os.environ.update({k: env[k] for k in ("LAB_DATA", "HOME", "LAB_TELEGRAM_DRYRUN", "VAULT_DIR")})
    sys.path.insert(0, str(root / "core"))

    # ---- sectors: cache + operator overrides (rename + per-ticker)
    data.mkdir()
    (data / "sectors.json").write_text(json.dumps({
        "kr": {"005930": {"sector": "통신 및 방송 장비 제조업", "industry": "반도체, 휴대폰", "name": "삼성전자"},
               "247540": {"sector": "일차전지 및 축전지 제조업", "industry": "양극재", "name": "에코프로비엠"},
               "035420": {"sector": "자료처리, 호스팅, 포털 및 기타 인터넷 정보매개 서비스업", "industry": "포털", "name": "NAVER"}},
        "us": {"NVDA": {"sector": "Semiconductors & Related Devices", "industry": "3674", "name": "NVIDIA CORP"}},
        "kr_refreshed": 9e12}, ensure_ascii=False), encoding="utf-8")
    import sectors
    assert sectors.lookup("kr", "005930")["sector"] == "반도체"            # per-ticker override wins
    assert sectors.lookup("kr", "247540")["sector"] == "2차전지"           # rename
    assert sectors.lookup("us", "nvda")["sector"] == "Semiconductors"
    assert sectors.lookup("kr", "999999")["sector"] == "미분류"
    print("sectors ok")

    # ---- fixtures: feed, forecasts, resolutions, agent notes, a sent report
    j = root / "journal"
    feed = [
        {"event_id": "kr:1", "market": "kr", "ticker": "247540", "name": "에코프로비엠", "board": "K", "type": "supply_contract",
         "corr": False, "title": "단일판매ㆍ공급계약체결", "filed_date": "2026-09-01", "filed_at": None, "source_id": "1", "url": "https://dart.fss.or.kr/x?rcpNo=1"},
        {"event_id": "kr:2", "market": "kr", "ticker": "247540", "name": "에코프로비엠", "board": "K", "type": "cb",
         "corr": False, "title": "전환사채권발행결정", "filed_date": "2026-09-20", "filed_at": None, "source_id": "2", "url": ""},
        {"event_id": "kr:3", "market": "kr", "ticker": "005930", "name": "삼성전자", "board": "Y", "type": "buyback",
         "corr": False, "title": "자기주식취득결정", "filed_date": "2026-09-10", "filed_at": None, "source_id": "3", "url": ""},
        {"event_id": "kr:4", "market": "kr", "ticker": "111111", "name": "관계없는회사", "board": "K", "type": "cb",
         "corr": False, "title": "전환사채권발행결정", "filed_date": "2026-09-10", "filed_at": None, "source_id": "4", "url": ""},
    ]
    (j / "feed.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in feed), encoding="utf-8")
    fcs = [
        {"id": "f1", "recorded_at": "2026-09-01T08:00:00Z", "event_id": "kr:1", "market": "kr", "ticker": "247540",
         "name": "에코프로비엠", "board": "K", "type": "supply_contract", "title": "단일판매ㆍ공급계약체결", "filed_date": "2026-09-01",
         "filed_at": None, "h": 20, "est_prob": 0.68, "base_p": 0.55, "base_src": "x", "decision": "bet", "side": "long",
         "stake": 300000, "rationale": "계약금액이 매출의 41%", "note": "", "strategy_rev": "abc"},
        {"id": "f2", "recorded_at": "2026-09-20T08:00:00Z", "event_id": "kr:2", "market": "kr", "ticker": "247540",
         "name": "에코프로비엠", "board": "K", "type": "cb", "title": "전환사채권발행결정", "filed_date": "2026-09-20",
         "filed_at": None, "h": 20, "est_prob": 0.35, "base_p": 0.42, "base_src": "x", "decision": "no-edge", "side": None,
         "stake": 0, "rationale": "리픽싱 조건 불리", "note": "", "strategy_rev": "abc"},
    ]
    (j / "forecasts.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in fcs), encoding="utf-8")
    (j / "resolutions.jsonl").write_text(json.dumps({
        "id": "f1", "resolved_at": "2026-09-30T08:00:00Z", "status": "ok", "entry_date": "2026-09-02", "exit_date": "2026-09-29",
        "entry_px": 100, "exit_px": 108, "r": 0.08, "rb": 0.01, "ar": 0.07, "up": 1.0, "brier": 0.1024,
        "base_brier": 0.2025, "pnl": 22950.0}) + "\n", encoding="utf-8")
    (j / "notes").mkdir(exist_ok=True)
    (j / "notes" / "kr-247540.md").write_text(
        "# 에코프로비엠 (247540)\n\n## 2026-09-01 supply_contract - 큰 계약\n- 공시 핵심: 매출 대비 41%\n", encoding="utf-8")
    sent = j / "outbox" / "sent"
    sent.mkdir(parents=True, exist_ok=True)
    (sent / "20260901-0740-forecast.md").write_text(
        "📊 예측 보고\n★ 베팅\n- 에코프로비엠(247540) 단일판매공급계약: 0.68\n", encoding="utf-8")

    # ---- export (no ob -> local only)
    p = subprocess.run([sys.executable, "core/vault.py", "export"], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    r = json.loads(p.stdout)
    assert r["companies"] == 4 and "ob 없음" in r["pull"], r   # watchlist 2 (+NVDA) + forecast/notes company
    base = vault / "공시랩"
    eco = base / "섹터" / "2차전지" / "에코프로비엠 (247540).md"
    sam = base / "섹터" / "반도체" / "삼성전자 (005930).md"
    assert eco.exists() and sam.exists() and (base / "섹터" / "2차전지" / "섹터 - 2차전지.md").exists()
    assert (base / "섹터" / "Semiconductors" / "NVIDIA CORP (NVDA).md").exists()
    assert not list(base.rglob("관계없는회사*")), "non-tracked company got a note"
    t = eco.read_text(encoding="utf-8")
    assert "[[섹터 - 2차전지]]" in t and "+7.0%" in t and "✅" in t and "+22,950" in t and "단일판매·공급계약" in t and "대기" in t
    assert "### 2026-09-01 supply_contract" in t and "$10,000" in (base / "대시보드.md").read_text(encoding="utf-8") and "# 에코프로비엠 (247540)\n\n섹터" in t  # agent H1 stripped, H2->H3
    assert t.index("전환사채권발행결정") < t.index("단일판매ㆍ공급계약체결")  # newest event first
    assert "tags: [공시랩, 섹터/2차전지]" in t
    rep = base / "보고" / "2026-09" / "2026-09-01 1640 예측 보고.md"
    assert rep.exists() and "[[에코프로비엠 (247540)|에코프로비엠(247540)]]" in rep.read_text(encoding="utf-8")
    dash = (base / "대시보드.md").read_text(encoding="utf-8")
    assert "[[섹터 - 2차전지\\|2차전지]]" in dash and "[[2026-09-01 1640 예측 보고]]" in dash
    print("export ok")

    # ---- user's memo survives regeneration and a sector move
    eco.write_text(eco.read_text(encoding="utf-8") + "\n폰에서 쓴 내 생각: 증설 사이클 확인\n", encoding="utf-8")
    ov = root / "config" / "sector_overrides.yaml"
    ov.write_text(ov.read_text(encoding="utf-8").replace('  "kr:000660": 반도체', '  "kr:000660": 반도체\n  "kr:247540": 소재'),
                  encoding="utf-8")
    p = subprocess.run([sys.executable, "core/vault.py", "export"], cwd=root, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    moved = base / "섹터" / "소재" / "에코프로비엠 (247540).md"
    assert moved.exists() and not eco.exists()
    mt = moved.read_text(encoding="utf-8")
    assert "폰에서 쓴 내 생각" in mt and mt.count("## 내 메모") == 1
    print("memo preserved across sector move ok")

    # ---- agent cannot run export; disabled config skips cleanly
    p = subprocess.run([sys.executable, "core/vault.py", "export"], cwd=root, env={**env, "LAB_AGENT": "1"},
                       capture_output=True, text=True)
    assert p.returncode != 0 and "에이전트" in p.stdout
    e2 = {k: v for k, v in env.items() if k != "VAULT_DIR"}
    p = subprocess.run([sys.executable, "core/vault.py", "export"], cwd=root, env=e2, capture_output=True, text=True)
    assert p.returncode == 0 and "skipped" in p.stdout
    print("guards ok")

    # ---- topic routing (dry-run log records the kind)
    import watch
    import daemon
    (data / "notify-dryrun.log").unlink(missing_ok=True)
    import datetime as dt
    today = dt.date.today().isoformat()
    with (j / "feed.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({**feed[2], "event_id": "kr:today", "filed_date": today}, ensure_ascii=False) + "\n")
    watch.run("kr", pull=False)
    daemon.Worker()
    import notify
    notify.send("fail!", kind="ops")
    log = (data / "notify-dryrun.log").read_text(encoding="utf-8")
    assert "[alert]\n📌 공시 알림" in log and "[ops]\nfail!" in log, log
    assert notify.thread_for("alert", {"threads": {"alert": 11, "report": 12}, "thread_id": 99}) == 11
    assert notify.thread_for("ops", {"threads": {"alert": 11, "report": 12}, "thread_id": 99}) == 99
    assert notify.thread_for("ops", {}) is None
    print("topics ok")

    shutil.rmtree(tmp)
    print("ALL VAULT TESTS PASSED")


if __name__ == "__main__":
    main()
