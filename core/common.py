"""Shared helpers: paths, config, jsonl, market clocks. PROTECTED."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("LAB_DATA", ROOT / "data"))
JOURNAL = ROOT / "journal"
STRATEGY = ROOT / "strategy"

MARKETS = {
    "kr": {"tz": ZoneInfo("Asia/Seoul"), "open": dt.time(9, 0)},
    "us": {"tz": ZoneInfo("America/New_York"), "open": dt.time(9, 30)},
}


def cfg() -> dict:
    return json.loads((ROOT / "config" / "protected.json").read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "nogit"


def is_agent() -> bool:
    """loop.sh sets LAB_AGENT=1 for the headless Claude process."""
    return os.environ.get("LAB_AGENT") == "1"


def is_operator() -> bool:
    return os.environ.get("LAB_OPERATOR") == "1" and not is_agent()


def die(msg: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def secret(name: str, filename: str) -> str | None:
    """Env var first, then ~/.config/gongsi-lab/<filename>. Never print the value."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    p = Path.home() / ".config" / "gongsi-lab" / filename
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return None
