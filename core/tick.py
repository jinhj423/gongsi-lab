"""GitHub Actions 용 한 번 실행 (30분마다). 상주 데몬(core/daemon.py)의 서버 없는 버전. PROTECTED.

  python3 core/tick.py plan    # 텔레그램 명령 받기 + 지금 할 일 판단 -> GITHUB_OUTPUT (claude/ob 설치 필요 여부)
  python3 core/tick.py run     # 할 일 실행: 공시 감시, 상태 보고, 과거 데이터 수집, 예측/리서치, 옵시디언

일정은 config/schedule.json (daemon 과 같은 형식). 상태는 state/daemon-state.json.
텔레그램 명령은 30분 이내에 처리된다 (Actions 가 상시 켜져 있지 않으므로).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
from zoneinfo import ZoneInfo

from common import DATA, ROOT, die, is_agent, secret
import daemon
import notify
import tgsetup

HEAVY = ("forecast", "research", "candidates")


def features() -> dict[str, str | None]:
    """기능별로 꺼진 이유 (None 이면 사용 가능)."""
    claude = shutil.which("claude") or os.path.exists(os.path.expanduser("~/.local/bin/claude"))
    return {
        "kr": None if secret("DART_API_KEY", "dart-key") else "DART_API_KEY 없음",
        "us": None if secret("SEC_USER_AGENT", "sec-user-agent") else "SEC_USER_AGENT 없음",
        "claude": None if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or not os.environ.get("GITHUB_ACTIONS") else "CLAUDE_CODE_OAUTH_TOKEN 없음",
        "claude_cli": None if claude else "claude CLI 미설치",
        "obsidian": None if (os.environ.get("OBSIDIAN_VAULT") or os.environ.get("VAULT_DIR")) else "OBSIDIAN_VAULT 없음",
    }


def blocked(run: str, f: dict, for_plan: bool = False) -> str | None:
    kind = run.split()[0]
    if kind == "watch":
        return f["kr"] if run.endswith("kr") else f["us"]
    if kind == "candidates":
        mk = run.split()[1] if len(run.split()) > 1 else "kr"
        return f[mk] or f["claude"] or (None if for_plan else f["claude_cli"])
    if kind in HEAVY:
        return f["claude"] or f["kr"] or (None if for_plan else f["claude_cli"])
    if kind == "vault":
        return f["obsidian"]
    if kind == "ingest":
        return None if not (f["kr"] and f["us"]) else "DART_API_KEY/SEC_USER_AGENT 둘 다 없음"
    return None


def due_jobs(now: dt.datetime) -> list[dict]:
    sch = daemon.schedule()
    st = daemon.load_state()
    paused = daemon.PAUSE_P.exists()
    jobs = []
    for j in sch["jobs"]:
        if paused and j["run"] != "status":
            continue
        lr = st["last_run"].get(j["name"])
        last = dt.datetime.fromisoformat(lr).astimezone(now.tzinfo) if lr else None
        if daemon.due(j, now, last, sch.get("catch_up_hours", 8)):
            jobs.append(j)
    for name, run in st.get("pending_runs", {}).items():
        jobs.append({"name": name, "run": run, "manual": True})
    return jobs


# ---------------------------------------------------------------- telegram commands

HELP = ("명령: /status 상태 · /top (또는 /후보) 오늘 후보 · /note <메모> · /run 후보|kr|us|forecast|research · /pause · /resume · /log\n"
        "/note 메모는 다음 사이클 직원이 읽고 반영합니다. 명령은 30분 이내에 처리됩니다.")


def actions_url() -> str:
    s, r = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
    return f"{s}/{r}/actions" if s and r else "(GitHub Actions 탭)"


def status_text() -> str:
    t = daemon.status_text()
    off = {k: v for k, v in features().items() if v and k != "claude_cli"}
    if off:
        t += "\n\n꺼진 기능: " + " · ".join(f"{k}({v})" for k, v in off.items())
    return t


def handle(text: str, thread: int | None) -> str:
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/status":
        return status_text()
    if cmd in ("/후보", "/top", "/candidates"):
        import candidates
        import screen
        parts = []
        for m in ("kr", "us"):
            res = screen.load(m)
            if res:
                parts.append(candidates.card(res))
        return "\n\n".join(parts) or "아직 후보 스크리닝 결과가 없습니다. /run 후보 로 지금 실행할 수 있습니다."
    if cmd == "/note":
        if not arg.strip():
            return "사용법: /note 이번 주는 2차전지 공시는 보수적으로"
        daemon.add_note(arg)
        return "📝 메모 기록. 다음 사이클에서 반영합니다."
    if cmd == "/run":
        what = arg.strip() or "forecast"
        runs = {"forecast": ["forecast"], "research": ["research"], "vault": ["vault"], "ingest": ["ingest"],
                "watch": ["watch kr", "watch us"], "candidates": ["candidates kr", "candidates us"],
                "후보": ["candidates kr", "candidates us"], "kr": ["candidates kr"], "us": ["candidates us"]}.get(what)
        if not runs:
            return "사용법: /run 후보 | kr | us | forecast | research | watch | vault | ingest"
        st = daemon.load_state()
        for r in runs:
            st.setdefault("pending_runs", {})[f"manual-{r.replace(' ', '-')}"] = r
        daemon.save_state(st)
        return f"▶️ {what} 곧 시작합니다 (결과는 해당 토픽으로)"
    if cmd == "/pause":
        daemon.PAUSE_P.parent.mkdir(parents=True, exist_ok=True)
        daemon.PAUSE_P.write_text("1")
        return "⏸ 정기 작업 일시정지 (/resume 으로 재개, /run 은 계속 가능)"
    if cmd == "/resume":
        daemon.PAUSE_P.unlink(missing_ok=True)
        return "▶️ 정기 작업 재개"
    if cmd == "/log":
        return f"📜 실행 기록: {actions_url()}"
    if cmd == "/update":
        return "코드는 GitHub 에 올리면 다음 실행부터 자동 반영됩니다."
    if cmd in ("/help", "/start"):
        return HELP
    return ""


# ---------------------------------------------------------------- run

def run_one(job: dict) -> tuple[bool, str]:
    run = job["run"]
    if run == "ingest":
        mins = daemon.schedule().get("ingest_minutes", 40)
        import subprocess
        p = subprocess.run([sys.executable, str(ROOT / "core" / "backfill.py"), "--minutes", str(mins)],
                           cwd=ROOT, capture_output=True, text=True, timeout=(mins + 20) * 60)
        return p.returncode == 0, (p.stdout[-600:] + "\n" + p.stderr[-900:]).strip()
    if run.split()[0] in HEAVY:
        (DATA / ".dirty").parent.mkdir(parents=True, exist_ok=True)
        (DATA / ".dirty").touch()  # 가격·토큰 캐시가 바뀌므로 저장
    return daemon.run_job(run, daemon.schedule().get("max_job_minutes", 60))


def record(name: str, ok: bool, tail: str, started: dt.datetime) -> None:
    st = daemon.load_state()
    st["last_run"][name] = started.isoformat()
    st.get("pending_runs", {}).pop(name, None)
    f = st["fail"].setdefault(name, {"count": 0})
    was = f.get("was_failing", False)
    f["count"] = 0 if ok else f.get("count", 0) + 1
    f["was_failing"] = not ok
    daemon.save_state(st)
    daemon.log(f"{name} {'ok' if ok else 'FAIL'}")
    if not ok and (f["count"] in (1, 3) or f["count"] % 10 == 0):
        notify.send(f"⚠️ 작업 실패: {name} (연속 {f['count']}회)\n{tail[-1200:]}\n\n기록: {actions_url()}", kind="ops")
    elif ok and was:
        notify.send(f"✅ {name} 복구됨", silent=True, kind="ops")


def gh_output(**kv) -> None:
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def plan() -> dict:
    r = tgsetup.poll(handle)
    tz = ZoneInfo(daemon.schedule()["timezone"])
    f = features()
    jobs = [j for j in due_jobs(dt.datetime.now(tz)) if not blocked(j["run"], f, for_plan=True)]
    kinds = {j["run"].split()[0] for j in jobs}
    need_claude = bool(kinds & set(HEAVY))
    need_ob = bool(kinds & ({"vault"} | set(HEAVY))) and not f["obsidian"]
    gh_output(need_claude=str(need_claude).lower(), need_ob=str(need_ob).lower(),
              heavy=str(bool(kinds & (set(HEAVY) | {"ingest", "vault"}))).lower())
    res = {"telegram": r, "due": [j["name"] for j in jobs], "need_claude": need_claude, "need_ob": need_ob}
    print(json.dumps(res, ensure_ascii=False))
    return res


def run() -> dict:
    tz = ZoneInfo(daemon.schedule()["timezone"])
    f = features()
    order = {"watch": 0, "status": 1, "ingest": 2, "candidates": 3, "forecast": 4, "research": 5, "vault": 6}
    jobs = sorted(due_jobs(dt.datetime.now(tz)), key=lambda j: order.get(j["run"].split()[0], 9))
    done, skipped = [], []
    ran_cycle = False
    for j in jobs:
        why = blocked(j["run"], f)
        if why:
            skipped.append(f"{j['name']}({why})")
            if j.get("manual"):
                record(j["name"], False, f"실행 불가: {why}", dt.datetime.now(dt.timezone.utc))
            continue
        if j["run"] == "vault" and ran_cycle:  # 예측/리서치 사이클이 이미 옵시디언을 갱신함
            record(j["name"], True, "", dt.datetime.now(dt.timezone.utc))
            continue
        started = dt.datetime.now(dt.timezone.utc)
        ok, tail = run_one(j)
        record(j["name"], ok, tail, started)
        done.append(f"{j['name']}:{'ok' if ok else 'FAIL'}")
        ran_cycle |= j["run"].split()[0] in HEAVY
    sent = notify.send_outbox()  # 방 등록 전에 쌓인 보고도 여기서 나간다
    res = {"done": done, "skipped": skipped, "outbox_sent": sent}
    print(json.dumps(res, ensure_ascii=False))
    return res


def main() -> None:
    if is_agent():
        die("에이전트는 tick 을 실행할 수 없습니다")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        plan()
    elif cmd == "run":
        run()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
