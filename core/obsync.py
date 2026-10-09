"""Obsidian Headless 를 GitHub Actions(또는 서버)에서 비대화식으로 쓰기 위한 준비. PROTECTED.

필요한 비밀값 (GitHub Secrets):
  OBSIDIAN_EMAIL, OBSIDIAN_PASSWORD   옵시디언 계정
  OBSIDIAN_VAULT                      동기화할 원격 보관함 이름 (ob sync-list-remote 에 나오는 이름)
  OBSIDIAN_E2E_PASSWORD               보관함이 종단간 암호화일 때만
  OBSIDIAN_MFA                        2단계 인증 쓰는 경우, 최초 1회 로그인 때만 (obsidian-login 워크플로 입력)

로그인 토큰은 OBSIDIAN_PASSWORD 로 암호화해서 $LAB_DATA/obsidian-auth.enc 에 둔다 (캐시로 보존).
그래서 2단계 인증 계정도 최초 1회만 코드를 넣으면 된다. 첨부파일·설정은 받지 않고 노트(.md)만 동기화.

  python3 core/obsync.py prepare      # 로그인 + 임시 폴더에 보관함 연결 -> VAULT_DIR 출력
  python3 core/obsync.py check        # 상태 점검 (자가점검용)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import DATA, out

ENC = DATA / "obsidian-auth.enc"
AUTH_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "obsidian-headless" / "auth_token"
DEFAULT_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "obsidian-vault"


class ObError(Exception):
    pass


def ob(*args: str, timeout: int = 600, env: dict | None = None) -> subprocess.CompletedProcess:
    exe = shutil.which("ob")
    if not exe:
        raise ObError("ob(Obsidian Headless) 미설치")
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, env=env or os.environ.copy())


def _openssl(mode: str, data: bytes) -> bytes:
    pw = os.environ.get("OBSIDIAN_PASSWORD")
    if not pw:
        raise ObError("OBSIDIAN_PASSWORD 없음")
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:OBS_PW"]
    if mode == "d":
        args.insert(2, "-d")
    p = subprocess.run(args, input=data, capture_output=True, env={**os.environ, "OBS_PW": pw})
    if p.returncode != 0:
        raise ObError("토큰 암복호화 실패")
    return p.stdout


def _token_ok(env: dict) -> bool:
    try:
        p = ob("sync-list-remote", "--json", timeout=60, env=env)
        return p.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def ensure_auth() -> str:
    """Sets OBSIDIAN_AUTH_TOKEN in os.environ. Returns how ('env'|'cache'|'login')."""
    if os.environ.get("OBSIDIAN_AUTH_TOKEN") and _token_ok(os.environ.copy()):
        return "env"
    if ENC.exists():
        try:
            tok = _openssl("d", ENC.read_bytes()).decode().strip()
            env = {**os.environ, "OBSIDIAN_AUTH_TOKEN": tok}
            if tok and _token_ok(env):
                os.environ["OBSIDIAN_AUTH_TOKEN"] = tok
                return "cache"
        except ObError:
            pass
    email, pw = os.environ.get("OBSIDIAN_EMAIL"), os.environ.get("OBSIDIAN_PASSWORD")
    if not (email and pw):
        raise ObError("OBSIDIAN_EMAIL / OBSIDIAN_PASSWORD 시크릿이 없습니다")
    args = ["login", "--email", email, "--password", pw]
    if os.environ.get("OBSIDIAN_MFA"):
        args += ["--mfa", os.environ["OBSIDIAN_MFA"]]
    env = {k: v for k, v in os.environ.items() if k != "OBSIDIAN_AUTH_TOKEN"}
    p = ob(*args, timeout=120, env=env)
    if p.returncode != 0 or not AUTH_FILE.exists():
        msg = (p.stdout + p.stderr).lower()
        if "mfa" in msg or "2fa" in msg or "two-factor" in msg or "code" in msg:
            raise ObError("옵시디언 2단계 인증 코드가 필요합니다 - GitHub Actions 의 'obsidian-login' 을 실행하고 인증 코드를 입력하세요")
        raise ObError("옵시디언 로그인 실패 (이메일/비밀번호 시크릿 확인)")
    tok = AUTH_FILE.read_text(encoding="utf-8").strip()
    ENC.parent.mkdir(parents=True, exist_ok=True)
    ENC.write_bytes(_openssl("e", tok.encode()))
    (DATA / ".dirty").touch()  # 캐시 저장 신호
    os.environ["OBSIDIAN_AUTH_TOKEN"] = tok
    return "login"


def prepare(path: Path = DEFAULT_DIR) -> Path:
    vault = os.environ.get("OBSIDIAN_VAULT")
    if not vault:
        raise ObError("OBSIDIAN_VAULT 시크릿(원격 보관함 이름)이 없습니다")
    ensure_auth()
    path.mkdir(parents=True, exist_ok=True)
    if ob("sync-config", "--path", str(path), timeout=60).returncode != 0:  # 아직 연결 안 됨
        args = ["sync-setup", "--vault", vault, "--path", str(path), "--device-name", "gongsi-lab-actions", "--json"]
        if os.environ.get("OBSIDIAN_E2E_PASSWORD"):
            args += ["--password", os.environ["OBSIDIAN_E2E_PASSWORD"]]
        p = ob(*args, timeout=120)
        if p.returncode != 0:
            raise ObError("보관함 연결 실패: " + (p.stderr or p.stdout)[-300:].replace(os.environ.get("OBSIDIAN_E2E_PASSWORD", "\0"), "***"))
        # 노트만: 첨부파일·앱 설정은 받지도 올리지도 않는다
        ob("sync-config", "--path", str(path), "--file-types", "", "--configs", "", timeout=60)
    os.environ["VAULT_DIR"] = str(path)
    return path


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "prepare":
            p = prepare()
            out({"ok": True, "vault_dir": str(p)})
        elif cmd == "check":
            how = ensure_auth()
            p = ob("sync-list-remote", "--json", timeout=60)
            names = []
            try:
                names = [v.get("name") for v in json.loads(p.stdout or "[]")]
            except Exception:  # noqa: BLE001
                pass
            want = os.environ.get("OBSIDIAN_VAULT")
            out({"ok": want in names if names else p.returncode == 0, "auth": how,
                 "vault": want, "vault_found": (want in names) if names else None})
        else:
            sys.exit(__doc__)
    except (ObError, subprocess.TimeoutExpired) as e:
        out({"ok": False, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
