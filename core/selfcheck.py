"""자가점검: 키·데이터원·텔레그램·옵시디언이 실제로 되는지 확인하고 운영 토픽으로 보고. PROTECTED.

  python3 core/selfcheck.py

GitHub Actions 의 'selfcheck' 를 처음 한 번 실행하면 된다. 실패 항목은 이유와 함께 알려준다.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
import traceback

from common import ROOT, out, secret
import notify


def check(name: str, fn) -> tuple[str, bool, str]:
    try:
        msg = fn()
        return name, True, msg or "OK"
    except Exception as e:  # noqa: BLE001
        return name, False, (str(e) or e.__class__.__name__)[:200]


def dart() -> str:
    import requests
    k = secret("DART_API_KEY", "dart-key")
    if not k:
        raise RuntimeError("DART_API_KEY 시크릿이 없음 (opendart.fss.or.kr 에서 발급)")
    day = dt.date.today() - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    j = requests.get("https://opendart.fss.or.kr/api/list.json",
                     params={"crtfc_key": k, "bgn_de": day.strftime("%Y%m%d"), "end_de": day.strftime("%Y%m%d"),
                             "page_count": 1}, timeout=30).json()
    if j.get("status") not in ("000", "013"):
        raise RuntimeError(f"DART 응답 {j.get('status')}: {j.get('message')}")
    return f"{day} 공시 {j.get('total_count', 0)}건 조회됨"


def sec() -> str:
    import sources
    if not secret("SEC_USER_AGENT", "sec-user-agent"):
        raise RuntimeError("SEC_USER_AGENT 시크릿이 없음 (미국 공시는 꺼진 상태로 동작)")
    j = sources.sec_get("https://data.sec.gov/submissions/CIK0000320193.json").json()
    return f"SEC 접속 OK ({j.get('name')})"


def price(market: str, ticker: str):
    def f() -> str:
        import prices
        df = prices._fetch(market, ticker, (dt.date.today() - dt.timedelta(days=20)).isoformat())
        import json
        src = json.loads((prices.DATA / "prices" / "sources.json").read_text()).get(f"{market}:{ticker}", "?")
        return f"{len(df)}일치, 출처 {src}"
    return f


def sectors_kr() -> str:
    import sectors
    n = sectors.refresh_kr()
    return f"KRX 업종표 {n}개 종목"


def telegram() -> str:
    c = notify.conf()
    if not c:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 시크릿이 없음")
    me = notify._api("getMe", {})["result"]
    if not c.get("chat_id"):
        return f"봇 @{me.get('username')} OK - 아직 방 미등록: 그룹(토픽)에서 /setup 을 보내 주세요"
    th = c.get("threads") or {}
    return f"봇 @{me.get('username')} OK · 방 등록됨 · 토픽 {', '.join(sorted(th)) or '없음(방 하나로 받음)'}"


def claude() -> str:
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and os.environ.get("GITHUB_ACTIONS"):
        raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN 시크릿이 없음")
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(exe):
        raise RuntimeError("claude CLI 미설치")
    p = subprocess.run([exe, "-p", "Reply with exactly: pong", "--max-turns", "1"],
                       capture_output=True, text=True, timeout=180, env={k: v for k, v in os.environ.items() if k != "LAB_AGENT"})
    if "pong" not in p.stdout.lower():
        raise RuntimeError("응답 이상: " + (p.stdout + p.stderr)[-200:])
    return "구독 토큰으로 응답 OK"


def obsidian() -> str:
    if not os.environ.get("OBSIDIAN_VAULT"):
        raise RuntimeError("OBSIDIAN_VAULT 시크릿이 없음 (옵시디언 기록은 꺼진 상태로 동작)")
    import obsync
    obsync.prepare()
    p = obsync.ob("sync", "--path", os.environ["VAULT_DIR"], timeout=900)
    if p.returncode != 0:
        raise RuntimeError("동기화 실패: " + (p.stdout + p.stderr)[-200:])
    n = sum(1 for _ in __import__("pathlib").Path(os.environ["VAULT_DIR"]).rglob("*.md"))
    return f"보관함 '{os.environ['OBSIDIAN_VAULT']}' 연결 OK (노트 {n}개 확인)"


def main() -> None:
    sys.path.insert(0, str(ROOT / "core"))
    items = [
        check("텔레그램", telegram),
        check("Claude", claude),
        check("DART (한국 공시)", dart),
        check("SEC (미국 공시)", sec),
        check("국내 주가 (삼성전자)", price("kr", "005930")),
        check("코스피 지수", price("kr", "KS11")),
        check("미국 주가 (SPY)", price("us", "SPY")),
        check("국내 업종 분류 (KRX)", sectors_kr),
        check("옵시디언", obsidian),
    ]
    lines = [f"🩺 자가점검 {dt.datetime.now():%m/%d %H:%M}"]
    for name, ok, msg in items:
        lines.append(f"{'✅' if ok else '❌'} {name}: {msg}")
    bad = [n for n, ok, _ in items if not ok]
    lines.append("")
    lines.append("모두 정상입니다. 이제 알아서 일합니다." if not bad else
                 "❌ 항목은 시크릿을 넣거나 고친 뒤 selfcheck 를 다시 실행하세요. 나머지 기능은 그대로 동작합니다.")
    text = "\n".join(lines)
    print(text)
    try:
        notify.send(text, kind="ops")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    out({"ok": not bad, "failed": bad})


if __name__ == "__main__":
    main()
