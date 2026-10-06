#!/usr/bin/env bash
# gongsi-lab 서버 설치 (Ubuntu 22.04/24.04, x86_64 또는 ARM64)
#
#   git clone git@github.com:<you>/gongsi-lab.git ~/gongsi-lab   (또는 zip 업로드)
#   cd ~/gongsi-lab && bash deploy/server-setup.sh          # 설치 (여러 번 실행해도 안전)
#   nano ~/.config/gongsi-lab/env                           # 키 채우기
#   bash deploy/server-setup.sh check                       # 키·인증·텔레그램 점검
#   systemctl --user enable --now gongsi-lab                # 시작
#
# 하는 일: 패키지 설치, 메모리 4GB 미만이면 스왑 생성, 파이썬 venv, Claude Code 설치,
#          비밀값 파일 템플릿, GitHub 백업용 배포키, systemd 사용자 서비스(재부팅 후 자동 시작).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
CONF="$HOME/.config/gongsi-lab"
ENVF="$CONF/env"
UNIT="$HOME/.config/systemd/user/gongsi-lab.service"
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

if [ "${1:-}" = "check" ]; then
  set -a; [ -f "$ENVF" ] && . "$ENVF"; set +a
  export PATH="$REPO/.venv/bin:$HOME/.local/bin:$PATH"
  ok=1
  say "Claude Code"
  if claude --version && [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    if out="$(claude -p "Reply with exactly: pong" --max-turns 1 2>&1)" && echo "$out" | grep -qi pong; then
      echo "인증 OK"
    else echo "인증 실패: $out"; ok=0; fi
  else echo "claude 없음 또는 CLAUDE_CODE_OAUTH_TOKEN 비어 있음"; ok=0; fi
  say "DART"
  if [ -n "${DART_API_KEY:-}" ]; then
    python3 - <<'PY' || ok=0
import os, requests
j = requests.get("https://opendart.fss.or.kr/api/list.json",
                 params={"crtfc_key": os.environ["DART_API_KEY"], "page_count": 1}, timeout=20).json()
print("status", j.get("status"), j.get("message"))
raise SystemExit(0 if j.get("status") in ("000", "013") else 1)
PY
  else echo "DART_API_KEY 비어 있음"; ok=0; fi
  say "SEC"
  if [ -n "${SEC_USER_AGENT:-}" ]; then
    curl -fsS -o /dev/null -w "HTTP %{http_code}\n" -A "$SEC_USER_AGENT" \
      https://data.sec.gov/submissions/CIK0000320193.json || ok=0
  else echo "SEC_USER_AGENT 비어 있음"; ok=0; fi
  say "가격 데이터 (FinanceDataReader / yfinance)"
  python3 -c "import FinanceDataReader as f; print('KR', len(f.DataReader('005930','2026-01-01')), 'rows')" || ok=0
  python3 -c "import yfinance as y; print('US', len(y.Ticker('SPY').history(period='1mo')), 'rows')" || ok=0
  say "Telegram"
  (cd "$REPO" && python3 core/notify.py test) || ok=0
  say "GitHub 백업"
  if git -C "$REPO" remote get-url origin >/dev/null 2>&1; then
    git -C "$REPO" ls-remote -q origin >/dev/null && echo "origin 접근 OK" || { echo "origin 접근 실패 (배포키 등록 확인)"; ok=0; }
  else echo "origin 없음 - 백업 안 됨 (선택)"; fi
  [ "$ok" = 1 ] && echo -e "\n모두 통과. systemctl --user enable --now gongsi-lab" || echo -e "\n실패 항목을 고친 뒤 다시 check"
  exit 0
fi

say "패키지"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git curl tzdata ca-certificates >/dev/null

say "메모리/스왑 (Claude Code 권장 4GB+)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
echo "RAM ${MEM_MB}MB, swap ${SWAP_MB}MB"
if [ "$MEM_MB" -lt 3800 ] && [ "$SWAP_MB" -lt 1000 ]; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "4GB 스왑 생성"
fi

say "파이썬 가상환경"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

say "Claude Code"
if ! command -v claude >/dev/null && [ ! -x "$HOME/.local/bin/claude" ]; then
  curl -fsSL https://claude.ai/install.sh | bash
fi
"$HOME/.local/bin/claude" --version 2>/dev/null || claude --version

say "비밀값 파일 $ENVF"
mkdir -p "$CONF" && chmod 700 "$CONF"
if [ ! -f "$ENVF" ]; then
  cat > "$ENVF" <<EOF
# gongsi-lab 서버 비밀값. 이 파일은 git 에 넣지 말 것.
# PC 에서 'claude setup-token' 실행 -> 출력된 토큰 (Pro 구독, 1년 유효)
CLAUDE_CODE_OAUTH_TOKEN=
CLAUDE_TOKEN_CREATED=$(date +%F)
DART_API_KEY=
SEC_USER_AGENT="Hong Gildong you@example.com"
# 1 이면 사이클마다 GitHub 로 백업 push
LAB_PUSH=1
LAB_MODEL=sonnet
LAB_MAX_TURNS=80
# 선택: healthchecks.io 무료 체크 URL (서버가 죽으면 그쪽에서 알림)
HEALTHCHECK_URL=
EOF
  chmod 600 "$ENVF"
  echo "템플릿 생성 -> nano $ENVF 로 채우세요"
else
  echo "이미 있음 (유지)"
fi
[ -f "$CONF/telegram.json" ] || echo "텔레그램: $CONF/telegram.json 필요 (README 5번 참고, PC에 만든 파일 복사 가능)"

say "git"
git config user.name >/dev/null || git config user.name "gongsi-lab-server"
git config user.email >/dev/null || git config user.email "gongsi-lab@localhost"
KEY="$HOME/.ssh/gongsi_lab_deploy"
if [ ! -f "$KEY" ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  ssh-keygen -q -t ed25519 -N "" -C "gongsi-lab-server" -f "$KEY"
  if ! grep -q gongsi_lab_deploy "$HOME/.ssh/config" 2>/dev/null; then
    printf 'Host github.com\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$KEY" >> "$HOME/.ssh/config"
    chmod 600 "$HOME/.ssh/config"
  fi
  ssh-keyscan -q github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
fi
echo "GitHub 백업용 배포키 (저장소 Settings -> Deploy keys -> Add, 'Allow write access' 체크):"
cat "$KEY.pub"
git remote get-url origin >/dev/null 2>&1 && echo "origin: $(git remote get-url origin)  (https 면: git remote set-url origin git@github.com:<you>/gongsi-lab.git)"

say "systemd 사용자 서비스"
mkdir -p "$(dirname "$UNIT")" data
cat > "$UNIT" <<EOF
[Unit]
Description=gongsi-lab daemon (disclosure research employee)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$REPO
EnvironmentFile=$ENVF
Environment=PATH=$REPO/.venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$REPO/.venv/bin/python core/daemon.py
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"   # 로그아웃/재부팅 후에도 실행
echo "서비스 등록 완료 (아직 시작 안 함)"

cat <<EOF

다음 단계:
  1) nano $ENVF          # 토큰·DART 키·SEC 이름/이메일
  2) $CONF/telegram.json 배치
  3) bash deploy/server-setup.sh check
  4) 과거 데이터 수집 (오래 걸림, 끊겨도 이어받음):
       tmux new -s ingest
       set -a; . $ENVF; set +a; source .venv/bin/activate
       python3 core/ingest.py kr --from 2018-01-01 && python3 core/ingest.py prices kr
       python3 core/ingest.py us --tickers-file strategy/universe_us.txt --from 2018-01-01 && python3 core/ingest.py prices us
       python3 core/eventstudy.py --market kr && python3 core/eventstudy.py --market us   # 기저율
  5) systemctl --user enable --now gongsi-lab
     journalctl --user -u gongsi-lab -f        # 로그 (텔레그램에서는 /log)
EOF
