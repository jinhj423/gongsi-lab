"""Daemon / notify / watch / loop.sh reporting tests (no network, Telegram dry-run).

    python3 tests/test_daemon.py
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
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "lab"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "data", "reports", "__pycache__"))
    for f in (root / "journal").glob("*.jsonl"):
        f.unlink()
    data = tmp / "data"
    home = tmp / "home"  # no telegram.json -> dry-run
    home.mkdir()
    env = {**os.environ, "LAB_DATA": str(data), "HOME": str(home), "LAB_TELEGRAM_DRYRUN": "1"}
    for k in ("LAB_OPERATOR", "LAB_AGENT"):
        env.pop(k, None)
    os.environ.update({"LAB_DATA": str(data), "HOME": str(home), "LAB_TELEGRAM_DRYRUN": "1"})
    sys.path.insert(0, str(root / "core"))
    import daemon
    import notify
    import watch
    dry = data / "notify-dryrun.log"

    # ---- schedule logic
    at = {"name": "f", "run": "forecast", "days": [0, 1, 2, 3, 4], "at": "16:30"}
    mon = dt.datetime(2026, 10, 5, 16, 31, tzinfo=KST)  # Monday
    assert daemon.due(at, mon, None, 8)
    assert not daemon.due(at, mon.replace(hour=16, minute=29), None, 8)
    assert not daemon.due(at, mon, mon.replace(minute=30, second=5), 8)          # already ran today
    assert daemon.due(at, mon, mon - dt.timedelta(days=1), 8)                    # ran yesterday
    assert daemon.due(at, mon.replace(hour=23), None, 8)                         # catch-up same evening
    assert not daemon.due(at, mon.replace(hour=23), None, 4)                     # too stale
    assert not daemon.due(at, dt.datetime(2026, 10, 10, 16, 31, tzinfo=KST), None, 8)  # Saturday
    ev = {"name": "w", "run": "watch kr", "days": [0, 1, 2, 3, 4], "every_min": 20, "between": ["07:30", "19:00"]}
    assert daemon.due(ev, mon, None, 8)
    assert not daemon.due(ev, mon, mon - dt.timedelta(minutes=10), 8)
    assert daemon.due(ev, mon, mon - dt.timedelta(minutes=21), 8)
    assert not daemon.due(ev, mon.replace(hour=19, minute=30), None, 8)
    print("schedule ok")

    # ---- commands
    w = daemon.Worker()  # not started: submit only queues
    assert "메모" in daemon.handle_command("/note 2차전지 공시는 보수적으로", w)
    assert "2차전지" in (root / "journal" / "operator-notes.md").read_text(encoding="utf-8")
    daemon.handle_command("/note 두번째", w)
    notes = (root / "journal" / "operator-notes.md").read_text(encoding="utf-8")
    today = dt.datetime.now(KST).strftime("%Y-%m-%d")
    assert notes.count(f"## {today}") == 1 and "두번째" in notes
    assert "시작" in daemon.handle_command("/run forecast", w)
    assert "이미" in daemon.handle_command("/run forecast", w)
    assert "일시정지" in daemon.handle_command("/pause@my_bot", w)
    assert daemon.tick(w, mon) == [] or all(n == "morning-status" for n in daemon.tick(w, mon))
    daemon.handle_command("/resume", w)
    assert "상태 보고" in daemon.handle_command("/status", w)
    assert daemon.handle_command("hello", w) == ""
    print("commands ok")

    # ---- watcher (no pull): watchlist hit + no duplicate
    feed = root / "journal" / "feed.jsonl"
    d = dt.date.today().isoformat()
    evs = [
        {"event_id": "kr:w1", "market": "kr", "ticker": "005930", "name": "삼성전자", "board": "Y", "type": "buyback",
         "corr": False, "title": "주요사항보고서(자기주식취득결정)", "filed_date": d, "filed_at": None, "source_id": "w1",
         "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=w1"},
        {"event_id": "kr:w2", "market": "kr", "ticker": "123456", "name": "남의회사", "board": "K", "type": "cb",
         "corr": False, "title": "전환사채권발행결정", "filed_date": d, "filed_at": None, "source_id": "w2", "url": ""},
    ]
    feed.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
    r = watch.run("kr", pull=False)
    assert r == {"alerts": 1, "sent": True}, r
    log = dry.read_text(encoding="utf-8")
    assert "삼성전자(005930)" in log and "남의회사" not in log
    assert watch.run("kr", pull=False)["alerts"] == 0
    print("watch ok")

    # ---- notify refuses the agent
    p = subprocess.run([sys.executable, "core/notify.py", "text", "hi"], cwd=root,
                       env={**env, "LAB_AGENT": "1"}, capture_output=True, text=True)
    assert p.returncode != 0 and "에이전트" in p.stdout
    p = subprocess.run([sys.executable, "core/daemon.py", "status"], cwd=root,
                       env={**env, "LAB_AGENT": "1"}, capture_output=True, text=True)
    assert p.returncode != 0
    p = subprocess.run([sys.executable, "core/backtest.py", "--market", "kr", "--holdout"], cwd=root,
                       env={**env, "LAB_AGENT": "1", "LAB_OPERATOR": "1"}, capture_output=True, text=True)
    assert p.returncode != 0 and "운영자" in p.stdout
    print("agent isolation ok")

    # ---- loop.sh with a fake claude that writes a report and tampers
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "config", k, v], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "operator: init"], cwd=root, check=True)
    bindir = tmp / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text("""#!/usr/bin/env bash
[ "$LAB_AGENT" = "1" ] || { echo "LAB_AGENT not set"; exit 9; }
python3 core/notify.py text "agent spam" && echo "SPAM SENT"
mkdir -p journal/outbox
printf '📊 예측 보고 테스트\\n한 줄 요약: 특이사항 없음\\n' > journal/outbox/20261007-0740-forecast.md
echo "# x" >> core/score.py
git add -A && git commit -qm "cycle: test"
""", encoding="utf-8")
    (bindir / "claude").chmod(0o755)
    before = dry.read_text(encoding="utf-8") if dry.exists() else ""
    p = subprocess.run(["bash", "loop.sh", "forecast"], cwd=root,
                       env={**env, "PATH": f"{bindir}:{env['PATH']}"}, capture_output=True, text=True)
    assert "SPAM SENT" not in p.stdout, p.stdout
    sent = dry.read_text(encoding="utf-8")[len(before):]
    assert "예측 보고 테스트" in sent and "장부 (core 계산)" in sent, sent
    assert "운영 경고" in sent and "보호 파일" in sent, sent
    assert (root / "journal" / "outbox" / "sent" / "20261007-0740-forecast.md").exists()
    assert "# x" not in (root / "core" / "score.py").read_text(encoding="utf-8")
    st = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout
    assert "journal/outbox" not in st, st
    print("loop reporting ok")

    # ---- server ops: cycle log, auth-failure alert, push retry on diverged remote, /log, /update
    assert list((data / "logs").glob("cycle-*-forecast.log")), "cycle log not written"
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=root, check=True)
    pc = tmp / "pc"  # the operator's PC clone
    subprocess.run(["git", "clone", "-q", str(remote), str(pc)], check=True)
    for k, v in (("user.email", "pc@pc"), ("user.name", "pc")):
        subprocess.run(["git", "config", k, v], cwd=pc, check=True)
    (pc / "config" / "my_watchlist.txt").write_text("kr 005930 삼성전자\nkr 035420 NAVER\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "operator: add NAVER"], cwd=pc, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=pc, check=True)
    (bindir / "claude").write_text("""#!/usr/bin/env bash
mkdir -p journal/retros && echo r > journal/retros/R1.md && git add -A && git commit -qm "cycle: r"
echo "API Error: 401 authentication_error · Login expired · Please run /login"
exit 1
""", encoding="utf-8")
    before = dry.read_text(encoding="utf-8")
    p = subprocess.run(["bash", "loop.sh", "forecast"], cwd=root,
                       env={**env, "PATH": f"{bindir}:{env['PATH']}", "LAB_PUSH": "1"}, capture_output=True, text=True)
    assert p.returncode == 1
    sent = dry.read_text(encoding="utf-8")[len(before):]
    assert "setup-token" in sent, sent
    head_remote = subprocess.run(["git", "--git-dir", str(remote), "log", "--oneline", "-3"], capture_output=True, text=True).stdout
    assert "cycle: r" in head_remote and "add NAVER" in head_remote, head_remote  # rebased onto PC push, then pushed
    print("auth alert + push retry ok")

    w2 = daemon.Worker()
    assert "데몬 로그" in daemon.handle_command("/log", w2) and "최근 사이클" in daemon.handle_command("/log", w2)
    (pc / "config" / "schedule.json").write_text(
        (pc / "config" / "schedule.json").read_text(encoding="utf-8").replace('"16:30"', '"16:40"'), encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "operator: move forecast to 16:40"], cwd=pc, check=True)
    subprocess.run(["git", "pull", "-q", "--rebase", "origin", "main"], cwd=pc, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=pc, check=True)
    w2.pending.add("forecast-kr")
    assert "보류" in daemon.handle_command("/update", w2)
    w2.pending.clear()
    r = daemon.handle_command("/update", w2)
    assert "재시작" in r and "schedule.json" in r and daemon._restart.is_set(), r
    assert '"16:40"' in (root / "config" / "schedule.json").read_text(encoding="utf-8")
    daemon._restart.clear()
    assert "업데이트 없음" in daemon.handle_command("/update", w2)
    os.environ["CLAUDE_TOKEN_CREATED"] = (dt.date.today() - dt.timedelta(days=350)).isoformat()
    assert "만료 15일 전" in daemon.status_text()
    os.environ["CLAUDE_TOKEN_CREATED"] = dt.date.today().isoformat()
    assert "토큰" not in daemon.status_text()
    print("server ops ok")

    shutil.rmtree(tmp)
    print("ALL DAEMON TESTS PASSED")


if __name__ == "__main__":
    main()
