"""상주 데몬: 일정대로 직원(에이전트)을 깨우고, 결과를 보고하고, 텔레그램 지시를 받는다. PROTECTED.

  python3 core/daemon.py              # 계속 실행 (systemd / nohup / WSL 작업 스케줄러로 띄움)
  python3 core/daemon.py once <run>   # 작업 하나 즉시 실행: forecast | research | "watch kr" | status
  python3 core/daemon.py status       # 상태 보고 텍스트 출력
  python3 core/daemon.py plan         # 지금 시각 기준 due 판정 (점검용)

일정: config/schedule.json. 텔레그램 명령 (allowed_user_ids 만):
  /status            현재 상태·장부
  /note <내용>       운영자 노트에 기록 -> 다음 사이클 에이전트가 읽음
  /run forecast|research|watch
  /pause, /resume    정기 작업 일시정지/재개
  /help
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

from common import DATA, JOURNAL, ROOT, die, is_agent, read_jsonl
import notify

STATE_P = DATA / "daemon-state.json"
PAUSE_P = DATA / "PAUSED"
_lock = threading.Lock()


def schedule() -> dict:
    return json.loads((ROOT / "config" / "schedule.json").read_text(encoding="utf-8"))


def load_state() -> dict:
    if STATE_P.exists():
        return json.loads(STATE_P.read_text(encoding="utf-8"))
    return {"last_run": {}, "tg_offset": 0, "fail": {}}


def save_state(s: dict) -> None:
    STATE_P.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_P.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_P)


def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def due(job: dict, now: dt.datetime, last: dt.datetime | None, catch_up_hours: float) -> bool:
    """now / last are tz-aware local datetimes."""
    if now.weekday() not in job.get("days", range(7)):
        return False
    if "at" in job:
        at = now.replace(hour=_hm(job["at"]).hour, minute=_hm(job["at"]).minute, second=0, microsecond=0)
        if now < at or now - at > dt.timedelta(hours=catch_up_hours):
            return False
        return last is None or last < at
    if "every_min" in job:
        a, b = (_hm(x) for x in job.get("between", ["00:00", "23:59"]))
        if not a <= now.time() <= b:
            return False
        return last is None or now - last >= dt.timedelta(minutes=job["every_min"])
    return False


# ---------------------------------------------------------------- status

def status_text() -> str:
    tz = ZoneInfo(schedule()["timezone"])
    now = dt.datetime.now(tz)
    lines = [f"🧑‍💼 상태 보고 {now:%m/%d %H:%M}" + ("  ⏸ 일시정지 중" if PAUSE_P.exists() else "")]
    log = (JOURNAL / "cycles.log")
    cyc = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()] if log.exists() else []
    if cyc:
        last = cyc[-1]
        try:
            t = dt.datetime.fromisoformat(last.split()[0].replace("Z", "+00:00"))
            ago = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
            lines.append(f"마지막 사이클 {ago:.0f}시간 전: {last[len(last.split()[0]) + 1:][:160]}")
            if ago > 36:
                lines.append("⚠️ 36시간 넘게 사이클이 없습니다 - PC/데몬/키를 확인하세요")
        except ValueError:
            lines.append(f"마지막 로그: {last[:160]}")
    else:
        lines.append("아직 사이클 기록 없음")
    st = load_state()
    for j in schedule()["jobs"]:
        if "at" in j:
            lr = st["last_run"].get(j["name"])
            lines.append(f"· {j['name']} {j['at']} (최근 {lr[5:16].replace('T', ' ') if lr else '-'})")
    prop = JOURNAL / "proposals.md"
    if prop.exists():
        n = prop.read_text(encoding="utf-8").count("\n## ")
        if n:
            lines.append(f"📮 건의사항 {n}건 (journal/proposals.md)")
    return "\n".join(lines) + "\n" + notify.footer()


# ---------------------------------------------------------------- jobs

def run_job(run: str, timeout_min: int) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("LAB_OPERATOR", "LAB_AGENT")}
    parts = run.split()
    if parts[0] in ("forecast", "research"):
        cmd = ["bash", str(ROOT / "loop.sh"), parts[0]]
    elif parts[0] == "watch":
        cmd = [sys.executable, str(ROOT / "core" / "watch.py"), parts[1]]
    elif parts[0] == "status":
        return notify.send(status_text(), silent=True), ""
    else:
        return False, f"unknown job {run}"
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout_min * 60)
        tail = (p.stdout[-600:] + "\n" + p.stderr[-900:]).strip()
        return p.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, f"{timeout_min}분 초과로 중단"


class Worker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.q: queue.Queue = queue.Queue()
        self.pending: set[str] = set()

    def submit(self, name: str, run: str) -> bool:
        with _lock:
            if name in self.pending:
                return False
            self.pending.add(name)
        self.q.put((name, run))
        return True

    def run(self) -> None:  # noqa: D401
        while True:
            name, run = self.q.get()
            started = dt.datetime.now(dt.timezone.utc)
            ok, tail = run_job(run, schedule().get("max_job_minutes", 60))
            with _lock:
                st = load_state()
                st["last_run"][name] = started.isoformat()
                f = st["fail"].setdefault(name, {"count": 0, "last_alert": None})
                if ok:
                    f["count"] = 0
                else:
                    f["count"] += 1
                save_state(st)
                self.pending.discard(name)
            print(f"[daemon] {name} {'ok' if ok else 'FAIL'}", flush=True)
            if not ok and (f["count"] in (1, 3) or f["count"] % 10 == 0):  # 같은 실패로 도배하지 않기
                notify.send(f"⚠️ 작업 실패: {name} (연속 {f['count']}회)\n{tail[-1200:]}")
            elif ok and f.get("was_failing"):
                notify.send(f"✅ {name} 복구됨", silent=True)
            with _lock:
                st = load_state()
                st["fail"].setdefault(name, {})["was_failing"] = not ok
                save_state(st)


# ---------------------------------------------------------------- telegram commands

HELP = ("명령: /status · /note <메모> · /run forecast|research|watch · /pause · /resume\n"
        "메모는 journal/operator-notes.md 에 기록되어 다음 사이클 직원이 읽습니다.")


def add_note(text: str) -> None:
    p = JOURNAL / "operator-notes.md"
    body = p.read_text(encoding="utf-8") if p.exists() else "# 운영자 노트\n"
    head, _, rest = body.partition("\n## ")
    today = dt.datetime.now(ZoneInfo(schedule()["timezone"])).strftime("%Y-%m-%d")
    entry = f"- (텔레그램 {dt.datetime.now(ZoneInfo(schedule()['timezone'])):%H:%M}) {text.strip()}\n"
    if rest.startswith(today):
        first, _, others = rest.partition("\n")
        new = f"{head}\n## {first}\n{entry}{others}"
    else:
        new = f"{head}\n## {today}\n{entry}" + (f"\n## {rest}" if rest else "")
    p.write_text(new, encoding="utf-8")


def handle_command(text: str, worker: Worker) -> str:
    t = text.strip()
    cmd, _, arg = t.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/status":
        return status_text()
    if cmd == "/note":
        if not arg.strip():
            return "사용법: /note 이번 주는 2차전지 공시는 보수적으로"
        add_note(arg)
        return "📝 메모 기록. 다음 사이클에서 반영합니다."
    if cmd == "/run":
        what = arg.strip() or "forecast"
        if what == "watch":
            worker.submit("manual-watch-kr", "watch kr")
            worker.submit("manual-watch-us", "watch us")
            return "👀 공시 감시 실행"
        if what not in ("forecast", "research"):
            return "사용법: /run forecast|research|watch"
        return f"▶️ {what} 사이클 시작" if worker.submit(f"manual-{what}", what) else "이미 대기/실행 중"
    if cmd == "/pause":
        PAUSE_P.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_P.write_text("1")
        return "⏸ 정기 작업 일시정지 (/resume 으로 재개, /run 은 계속 가능)"
    if cmd == "/resume":
        PAUSE_P.unlink(missing_ok=True)
        return "▶️ 정기 작업 재개"
    if cmd in ("/help", "/start"):
        return HELP
    return ""


def poll_telegram(worker: Worker) -> None:
    c = notify.conf()
    if not c or os.environ.get("LAB_TELEGRAM_DRYRUN") == "1":
        time.sleep(30)
        return
    st = load_state()
    try:
        j = notify._api("getUpdates", {"offset": st.get("tg_offset", 0), "timeout": 25,
                                       "allowed_updates": ["message"]})
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] getUpdates: {e}", flush=True)
        time.sleep(15)
        return
    allowed = set(c.get("allowed_user_ids", []))
    for u in j.get("result", []):
        with _lock:
            st = load_state()
            st["tg_offset"] = u["update_id"] + 1
            save_state(st)
        m = u.get("message") or {}
        if m.get("chat", {}).get("id") != c["chat_id"] or (m.get("from") or {}).get("id") not in allowed:
            continue
        if not (m.get("text") or "").startswith("/"):
            continue
        reply = handle_command(m["text"], worker)
        if reply:
            notify.send(reply)


# ---------------------------------------------------------------- main loop

def tick(worker: Worker, now: dt.datetime) -> list[str]:
    sch = schedule()
    st = load_state()
    fired = []
    paused = PAUSE_P.exists()
    for j in sch["jobs"]:
        if paused and j["run"] != "status":
            continue
        lr = st["last_run"].get(j["name"])
        last = dt.datetime.fromisoformat(lr).astimezone(now.tzinfo) if lr else None
        if due(j, now, last, sch.get("catch_up_hours", 8)) and worker.submit(j["name"], j["run"]):
            fired.append(j["name"])
    return fired


def main() -> None:
    if is_agent():
        die("에이전트는 데몬을 실행할 수 없습니다")
    args = sys.argv[1:]
    if args[:1] == ["status"]:
        print(status_text())
        return
    if args[:1] == ["plan"]:
        tz = ZoneInfo(schedule()["timezone"])
        now = dt.datetime.now(tz)
        st = load_state()
        for j in schedule()["jobs"]:
            lr = st["last_run"].get(j["name"])
            last = dt.datetime.fromisoformat(lr).astimezone(tz) if lr else None
            print(j["name"], "DUE" if due(j, now, last, schedule().get("catch_up_hours", 8)) else "-", lr or "")
        return
    if args[:1] == ["once"]:
        ok, tail = run_job(" ".join(args[1:]), schedule().get("max_job_minutes", 60))
        print("ok" if ok else "FAIL", tail)
        return

    pid_p = DATA / "daemon.pid"
    if pid_p.exists():
        try:
            os.kill(int(pid_p.read_text()), 0)
            die(f"데몬이 이미 실행 중 (pid {pid_p.read_text()})")
        except (OSError, ValueError):
            pass
    pid_p.parent.mkdir(parents=True, exist_ok=True)
    pid_p.write_text(str(os.getpid()))
    worker = Worker()
    worker.start()
    notify.send("🟢 출근했습니다. 일정대로 공시 감시·예측·리서치를 시작합니다. /help", silent=True)
    tz = ZoneInfo(schedule()["timezone"])
    try:
        while True:
            for name in tick(worker, dt.datetime.now(tz)):
                print(f"[daemon] queued {name}", flush=True)
            poll_telegram(worker)  # 최대 25초 대기 (long poll) 또는 30초 sleep
    except KeyboardInterrupt:
        pass
    finally:
        pid_p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
