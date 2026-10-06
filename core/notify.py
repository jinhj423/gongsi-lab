"""Telegram reporting. PROTECTED. The agent can never run this (LAB_AGENT=1 is refused).

설정: ~/.config/gongsi-lab/telegram.json
  {"token": "123:ABC...", "chat_id": -1001234567890, "thread_id": 42, "allowed_user_ids": [11111111]}
  thread_id 는 그룹 토픽을 쓸 때만. allowed_user_ids 는 /명령을 받아들일 사람.

  python3 core/notify.py whoami          # 봇에게 메시지 보낸 뒤 실행 -> chat_id/thread_id/user_id 확인
  python3 core/notify.py test            # 테스트 메시지
  python3 core/notify.py text "내용"
  python3 core/notify.py send-outbox     # journal/outbox/*.md 를 보내고 sent/ 로 이동

LAB_TELEGRAM_DRYRUN=1 이면 실제 발송 대신 $LAB_DATA/notify-dryrun.log 에 기록.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from common import DATA, JOURNAL, die, is_agent

OUTBOX = JOURNAL / "outbox"
CONF = Path.home() / ".config" / "gongsi-lab" / "telegram.json"
LIMIT = 3900


def conf() -> dict | None:
    if CONF.exists():
        return json.loads(CONF.read_text(encoding="utf-8"))
    return None


def _api(method: str, payload: dict) -> dict:
    import requests
    c = conf()
    r = requests.post(f"https://api.telegram.org/bot{c['token']}/{method}", json=payload, timeout=40)
    j = r.json()
    if not j.get("ok"):
        # 토큰이 들어간 URL 은 절대 출력하지 않는다
        raise RuntimeError(f"telegram {method} 실패: {j.get('description')}")
    return j


def _chunks(text: str) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > LIMIT:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:LIMIT])
            line = line[LIMIT:]
        if len(cur) + len(line) > LIMIT:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out


def send(text: str, silent: bool = False) -> bool:
    """Returns True if delivered (or dry-run logged)."""
    if is_agent():
        die("에이전트는 직접 알림을 보낼 수 없습니다. journal/outbox/ 에 보고서를 쓰세요.")
    if os.environ.get("LAB_TELEGRAM_DRYRUN") == "1" or conf() is None:
        p = DATA / "notify-dryrun.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"----- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
        if conf() is None and os.environ.get("LAB_TELEGRAM_DRYRUN") != "1":
            print("[notify] telegram.json 없음 -> notify-dryrun.log 에만 기록", file=sys.stderr)
        return True
    c = conf()
    try:
        for part in _chunks(text):
            payload = {"chat_id": c["chat_id"], "text": part, "disable_web_page_preview": True,
                       "disable_notification": silent}
            if c.get("thread_id"):
                payload["message_thread_id"] = c["thread_id"]
            _api("sendMessage", payload)
            time.sleep(0.4)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notify] 발송 실패: {e}", file=sys.stderr)
        return False


def footer() -> str:
    """Numbers computed by core, appended to every agent report so they cannot be misreported."""
    import ledger
    import statistics
    res = ledger.resolutions()
    fc = ledger.forecasts()
    done = [(f, res[f["id"]]) for f in fc if f["id"] in res and res[f["id"]]["status"] == "ok"]
    lines = ["", "── 장부 (core 계산) ──"]
    if done:
        d = statistics.fmean([r["brier"] for _, r in done]) - statistics.fmean([r["base_brier"] for _, r in done])
        lines.append(f"정산 {len(done)}건 · brier_delta {d:+.4f} (음수=기저율보다 나음)" + (" · 표본 작음" if len(done) < 30 else ""))
    else:
        lines.append("정산된 예측 없음")
    lines.append(f"대기 {sum(1 for f in fc if f['id'] not in res)}건")
    for m, unit in (("kr", "원"), ("us", "$")):
        b = ledger.book(m)
        if b["open_bets"] or b["settled_bets"]:
            lines.append(f"{m.upper()} 페이퍼: 실현 {b['realized_pnl']:+,.0f}{unit} · 보유 {b['open_bets']}건 · 승 {b['wins']}/{b['settled_bets']}")
    return "\n".join(lines)


def send_outbox() -> int:
    if not OUTBOX.exists():
        return 0
    sent_dir = OUTBOX / "sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(OUTBOX.glob("*.md")):
        body = p.read_text(encoding="utf-8").strip()
        if not body:
            p.unlink()
            continue
        extra = ""
        if "research" in p.name:
            import subprocess
            log = subprocess.run(["git", "log", "--since=7.days", "--pretty=- %s", "--", "strategy/"],
                                 cwd=JOURNAL.parent, capture_output=True, text=True).stdout.strip()
            extra = "\n\n── 최근 7일 전략 변경 (git) ──\n" + (log or "없음")
        if send(body + extra + "\n" + footer()):
            p.rename(sent_dir / p.name)
            n += 1
    return n


def whoami() -> None:
    c = conf()
    if not c:
        die(f"{CONF} 에 최소한 token 을 넣으세요")
    j = _api("getUpdates", {"timeout": 0})
    seen = set()
    for u in j.get("result", []):
        m = u.get("message") or u.get("channel_post") or {}
        key = (m.get("chat", {}).get("id"), m.get("message_thread_id"), (m.get("from") or {}).get("id"))
        if key in seen:
            continue
        seen.add(key)
        print(json.dumps({"chat_id": key[0], "chat_title": m.get("chat", {}).get("title"),
                          "thread_id": key[1], "user_id": key[2], "text": (m.get("text") or "")[:40]},
                         ensure_ascii=False))
    if not seen:
        print("업데이트 없음: 봇이 있는 방(원하는 토픽)에서 아무 메시지나 보낸 뒤 다시 실행하세요. "
              "그룹이면 BotFather 에서 /setprivacy -> Disable 도 필요할 수 있습니다.")


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: notify.py whoami|test|text <msg>|send-outbox")
    cmd = sys.argv[1]
    if is_agent():
        die("에이전트는 notify 를 실행할 수 없습니다")
    if cmd == "whoami":
        whoami()
    elif cmd == "test":
        print(send("✅ gongsi-lab 알림 테스트"))
    elif cmd == "text":
        print(send(" ".join(sys.argv[2:])))
    elif cmd == "send-outbox":
        print(json.dumps({"sent": send_outbox()}))
    else:
        die("unknown command")


if __name__ == "__main__":
    main()
