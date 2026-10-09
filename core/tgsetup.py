"""Telegram auto-setup and command intake. PROTECTED.

사용자가 할 일: 봇을 그룹에 넣고(관리자), 토픽마다 /setup 을 한 번씩 보낸다.
  - 처음 /setup 을 보낸 사람이 주인(owner)이 되고, 그 방이 보고 받을 방이 된다.
  - 토픽 이름에 "공시/알림", "보고/리포트", "운영/경고/오류" 가 들어 있으면 자동으로 용도가 정해진다.
    이름이 애매하면 그 토픽에서 "/setup 공시" "/setup 보고" "/setup 운영" 처럼 직접 지정.
  - 토픽 없는 일반 그룹이나 1:1 대화도 된다 (모든 메시지가 그 방으로).
등록 결과는 state/telegram.json (토큰은 저장하지 않는다).

poll(handler): getUpdates 를 한 번 읽어 /setup 을 처리하고, 주인이 보낸 다른 /명령은
handler(text, thread_id) 의 반환 문자열로 답한다.
"""
from __future__ import annotations

import notify

KEYWORDS = {
    "alert": ("공시", "알림", "alert"),
    "report": ("보고", "리포트", "report"),
    "ops": ("운영", "경고", "오류", "에러", "ops"),
}
NAMES = {"alert": "📌 공시 알림", "report": "📊 보고", "ops": "⚠️ 운영"}


def kind_from(text: str) -> str | None:
    t = (text or "").lower()
    for k, words in KEYWORDS.items():
        if any(w in t for w in words):
            return k
    return None


def topic_name(m: dict) -> str:
    r = m.get("reply_to_message") or {}
    return ((r.get("forum_topic_created") or {}).get("name")
            or (m.get("forum_topic_created") or {}).get("name") or "")


def _reply(chat_id: int, thread: int | None, text: str) -> None:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if thread:
        payload["message_thread_id"] = thread
    try:
        notify._api("sendMessage", payload)
    except Exception as e:  # noqa: BLE001
        print(f"[tgsetup] reply 실패: {e}")


def handle_setup(m: dict, st: dict) -> str | None:
    """Mutates st. Returns reply text (or None to ignore)."""
    uid = (m.get("from") or {}).get("id")
    chat = m.get("chat") or {}
    owners = st.setdefault("owner_ids", [])
    if owners and uid not in owners:
        return None  # 남이 보낸 /setup 은 무시
    if not owners:
        owners.append(uid)
    if st.get("chat_id") != chat.get("id"):
        st["chat_id"] = chat.get("id")
        st["threads"] = {}
    arg = (m.get("text") or "").partition(" ")[2]
    thread = m.get("message_thread_id") if m.get("is_topic_message") else None
    lines = []
    if thread:
        kind = kind_from(arg) or kind_from(topic_name(m))
        if not kind:
            return ("이 토픽의 용도를 알려주세요: /setup 공시 · /setup 보고 · /setup 운영")
        st.setdefault("threads", {})[kind] = thread
        lines.append(f"✅ 이 토픽은 [{NAMES[kind]}] 용으로 등록했습니다.")
    else:
        lines.append(f"✅ 이 방({chat.get('title') or '1:1 대화'})을 보고 받을 방으로 등록했습니다.")
    missing = [NAMES[k] for k in NAMES if k not in st.get("threads", {})]
    if chat.get("is_forum") and missing:
        lines.append("남은 토픽: " + ", ".join(missing) + " - 각 토픽에서 /setup 을 보내 주세요.")
    elif chat.get("is_forum"):
        lines.append("토픽 3개 등록 완료. 이제 알아서 일하고 보고합니다. 명령어: /help")
    else:
        lines.append("모든 알림이 이 방으로 옵니다. 명령어: /help")
    return "\n".join(lines)


def poll(handler) -> dict:
    c = notify.conf()
    if not c:
        return {"skipped": "no token"}
    st = notify.auto()
    try:
        j = notify._api("getUpdates", {"offset": st.get("offset", 0), "timeout": 0,
                                       "allowed_updates": ["message"]})
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    handled = 0
    for u in j.get("result", []):
        st["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd = text.split()[0].split("@")[0].lower()
        thread = m.get("message_thread_id") if m.get("is_topic_message") else None
        if cmd == "/setup":
            reply = handle_setup(m, st)
            notify.save_auto(st)
            if reply:
                _reply(m["chat"]["id"], thread, reply)
                handled += 1
            continue
        if (m.get("from") or {}).get("id") not in st.get("owner_ids", []):
            continue
        if st.get("chat_id") and m.get("chat", {}).get("id") != st["chat_id"] and m["chat"].get("type") != "private":
            continue
        reply = handler(text, thread)
        if reply:
            _reply(m["chat"]["id"], thread, reply)
            handled += 1
    notify.save_auto(st)
    return {"handled": handled, "offset": st.get("offset", 0)}
