"""GitHub Actions mode: Telegram auto-setup, tick plan/run, feed prune, price fallback (no network).

    python3 tests/test_actions.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
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
    data, home = tmp / "data", tmp / "home"
    home.mkdir()
    for k in ("LAB_OPERATOR", "LAB_AGENT", "LAB_TELEGRAM_DRYRUN", "VAULT_DIR", "OBSIDIAN_VAULT", "GITHUB_ACTIONS"):
        os.environ.pop(k, None)
    os.environ.update({"LAB_DATA": str(data), "HOME": str(home), "TELEGRAM_BOT_TOKEN": "123:TEST",
                       "DART_API_KEY": "x", "GITHUB_OUTPUT": str(tmp / "gh_out")})
    os.environ.pop("SEC_USER_AGENT", None)
    sys.path.insert(0, str(root / "core"))
    import notify
    import tgsetup
    import tick
    import daemon

    sent: list[tuple[str, dict]] = []
    updates: list[dict] = []

    def fake_api(method, payload):
        if method == "getUpdates":
            res = [u for u in updates if u["update_id"] >= payload.get("offset", 0)]
            return {"ok": True, "result": res}
        sent.append((method, payload))
        return {"ok": True, "result": {"username": "testbot"}}

    def msg(uid, text, chat=-100, thread=None, topic=None, forum=True, n=[0]):
        n[0] += 1
        m = {"message_id": n[0], "from": {"id": uid}, "chat": {"id": chat, "type": "supergroup", "is_forum": forum,
                                                              "title": "공시랩"}, "text": text}
        if thread:
            m.update({"message_thread_id": thread, "is_topic_message": True})
            if topic:
                m["reply_to_message"] = {"message_id": thread, "forum_topic_created": {"name": topic}}
        updates.append({"update_id": n[0], "message": m})

    with mock.patch.object(notify, "_api", side_effect=fake_api):
        # before /setup: sends are held, not lost
        assert notify.send("hello", kind="ops") is False
        assert (data / "notify-pending.log").exists()

        # owner sets up three topics by name; a stranger is ignored
        msg(111, "/setup", thread=11, topic="📌 공시 알림")
        msg(222, "/setup", thread=12, topic="해킹")
        msg(111, "/setup@testbot", thread=12, topic="📊 보고")
        msg(111, "/setup", thread=13, topic="잡담")          # ambiguous name -> asks
        msg(111, "/setup 운영", thread=13, topic="잡담")      # explicit
        r = tgsetup.poll(tick.handle)
        st = notify.auto()
        assert st["owner_ids"] == [111] and st["chat_id"] == -100, st
        assert st["threads"] == {"alert": 11, "report": 12, "ops": 13}, st
        assert "token" not in json.dumps(st)
        replies = [p["text"] for m, p in sent if m == "sendMessage"]
        assert any("용도를 알려주세요" in t for t in replies) and any("토픽 3개 등록 완료" in t for t in replies), replies
        assert not any(p.get("message_thread_id") == 12 and "해킹" in p["text"] for _, p in sent)
        assert notify.thread_for("alert", notify.conf()) == 11
        print("telegram auto-setup ok")

        # commands: only owner, answered in the asking topic; /run queues work
        sent.clear()
        msg(222, "/status", thread=12)
        msg(111, "/note 2차전지는 보수적으로", thread=12)
        msg(111, "/run forecast", thread=12)
        msg(111, "/pause", thread=13)
        tgsetup.poll(tick.handle)
        texts = [(p.get("message_thread_id"), p["text"]) for m, p in sent if m == "sendMessage"]
        assert len(texts) == 3 and texts[0][0] == 12 and "메모" in texts[0][1], texts
        assert "2차전지" in (root / "journal" / "operator-notes.md").read_text(encoding="utf-8")
        assert daemon.load_state()["pending_runs"] == {"manual-forecast": "forecast"}
        assert daemon.PAUSE_P.exists()
        # offset advanced: polling again handles nothing
        sent.clear()
        assert tgsetup.poll(tick.handle)["handled"] == 0 and not sent
        print("telegram commands ok")

        # plan: paused -> scheduled work skipped, but manual /run still due; needs claude
        res = tick.plan()
        assert "manual-forecast" in res["due"] and res["need_claude"] is True, res
        assert all(d in ("manual-forecast", "morning-status") for d in res["due"]), res  # 일시정지: 상태 보고만 허용
        out = (tmp / "gh_out").read_text()
        assert "need_claude=true" in out and "need_ob=false" in out
        daemon.PAUSE_P.unlink()
        # run without claude CLI: manual forecast is blocked -> recorded as failure + ops alert, queue cleared
        sent.clear()
        with mock.patch.object(tick, "features", return_value={"kr": None, "us": "SEC_USER_AGENT 없음", "claude": None,
                                                                 "claude_cli": "claude CLI 미설치", "obsidian": "x"}):
            with mock.patch.object(tick, "due_jobs", return_value=[{"name": "manual-forecast", "run": "forecast", "manual": True},
                                                                   {"name": "watch-us", "run": "watch us"}]):
                r = tick.run()
        assert r["done"] == [] and len(r["skipped"]) == 2, r
        assert "manual-forecast" not in daemon.load_state().get("pending_runs", {})
        assert any(p.get("message_thread_id") == 13 and "실행 불가" in p["text"] for _, p in sent), sent
        print("tick plan/run ok")

    # feed prune: old untracked dropped, old tracked (watchlist / forecast / notes) kept
    import feed
    import ledger
    old = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    new = dt.date.today().isoformat()
    rows = [
        {"event_id": "kr:a", "market": "kr", "ticker": "005930", "type": "buyback", "filed_date": old},   # watchlist
        {"event_id": "kr:b", "market": "kr", "ticker": "123456", "type": "cb", "filed_date": old},       # drop
        {"event_id": "kr:c", "market": "kr", "ticker": "123456", "type": "cb", "filed_date": new},       # recent
        {"event_id": "kr:d", "market": "kr", "ticker": "777777", "type": "cb", "filed_date": old},       # has notes
    ]
    ledger.FEED.parent.mkdir(parents=True, exist_ok=True)
    ledger.FEED.write_text("".join(json.dumps(e) + "\n" for e in rows))
    (root / "journal" / "notes").mkdir(exist_ok=True)
    (root / "journal" / "notes" / "kr-777777.md").write_text("# x\n")
    assert feed.prune() == 1 and set(ledger.feed_events()) == {"kr:a", "kr:c", "kr:d"}
    print("feed prune ok")

    # price fallback: KR primary fails -> yfinance used and recorded
    import pandas as pd
    import prices
    df = pd.DataFrame({"Open": [1.0, 2.0], "High": [1, 2], "Low": [1, 2], "Close": [1.5, 2.5], "Volume": [10, 20]},
                      index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    with mock.patch.object(prices, "_fdr", side_effect=RuntimeError("blocked")), \
         mock.patch.object(prices, "_yf_kr", return_value=df):
        got = prices._fetch("kr", "005930", "2026-01-01")
    assert len(got) == 2
    assert json.loads((data / "prices" / "sources.json").read_text())["kr:005930"] == "yfinance"
    with mock.patch.object(prices, "_yf", side_effect=RuntimeError("429")), \
         mock.patch.object(prices, "_fdr", side_effect=RuntimeError("down")):
        try:
            prices._fetch("us", "SPY", "2026-01-01")
            raise AssertionError("should fail")
        except RuntimeError as e:
            assert "yfinance: 429" in str(e) and "fdr: down" in str(e)
    print("price fallback ok")

    # obsync: encrypt/decrypt round trip with password, wrong password fails
    import obsync
    os.environ["OBSIDIAN_PASSWORD"] = "pw-1"
    enc = obsync._openssl("e", b"secret-token")
    assert b"secret-token" not in enc and obsync._openssl("d", enc) == b"secret-token"
    os.environ["OBSIDIAN_PASSWORD"] = "pw-2"
    try:
        obsync._openssl("d", enc)
        raise AssertionError("wrong password decrypted")
    except obsync.ObError:
        pass
    print("obsidian token crypto ok")

    shutil.rmtree(tmp)
    print("ALL ACTIONS TESTS PASSED")


if __name__ == "__main__":
    main()
