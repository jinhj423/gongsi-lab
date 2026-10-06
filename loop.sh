#!/usr/bin/env bash
# 헤드리스 Claude Code 로 사이클 실행.
#   ./loop.sh forecast [반복횟수] [간격(분)]    # CYCLE.md (예측 사이클)
#   ./loop.sh research                          # RESEARCH.md (리서치 사이클)
# 환경변수:
#   LAB_MODEL      모델 (기본 sonnet)        LAB_MAX_TURNS  턴 상한 (기본 80)
#   DART_API_KEY / SEC_USER_AGENT           (~/.config/gongsi-lab/ 파일로도 가능)
#   LAB_PUSH=1     사이클 후 git push origin main
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-forecast}"
CYCLES="${2:-1}"
SLEEP_MIN="${3:-60}"
case "$MODE" in
  forecast) PROMPT_FILE=CYCLE.md ;;
  research) PROMPT_FILE=RESEARCH.md ;;
  *) echo "usage: ./loop.sh forecast|research [cycles] [sleep_min]" >&2; exit 2 ;;
esac

LOCK=".loop.pid"
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "ERROR: 이미 실행 중 (pid $(cat "$LOCK"))" >&2; exit 1
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

if [ -n "${LAB_OPERATOR:-}" ]; then
  echo "ERROR: LAB_OPERATOR 가 설정된 셸에서 에이전트를 돌리지 마세요 (홀드아웃 잠금이 풀립니다)" >&2; exit 1
fi
command -v claude >/dev/null || { echo "ERROR: claude CLI 가 PATH 에 없습니다" >&2; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "ERROR: git 저장소가 아닙니다 (git init 먼저)" >&2; exit 1; }

PROTECTED=(core/ config/ deploy/ CYCLE.md RESEARCH.md loop.sh CLAUDE.md README.md .gitignore tests/ requirements.txt)
APPEND_ONLY=(journal/feed.jsonl journal/forecasts.jsonl journal/resolutions.jsonl journal/trials.jsonl journal/holdout-log.jsonl)

holdout_lines() { if [ -f journal/holdout-log.jsonl ]; then wc -l < journal/holdout-log.jsonl; else echo 0; fi; }
WARNS=()
warn() { echo "WARNING: $*" >&2; WARNS+=("$*"); }
FAILED=0

for i in $(seq 1 "$CYCLES"); do
  echo "=== $MODE cycle $i/$CYCLES $(date -u +%FT%TZ) ==="
  WARNS=()
  BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
  HOLDOUT_BEFORE="$(holdout_lines)"

  # LAB_AGENT=1: core 가 이 프로세스를 에이전트로 인식 -> 알림 발송·홀드아웃·데몬 실행 거부
  if ! LAB_AGENT=1 claude -p "$(cat "$PROMPT_FILE")" \
    --model "${LAB_MODEL:-sonnet}" \
    --max-turns "${LAB_MAX_TURNS:-80}" \
    --permission-mode acceptEdits \
    --allowedTools "Read" "Glob" "Grep" "WebSearch" "WebFetch" "Edit" "Write" \
      "Bash(python3 core/*)" "Bash(git add:*)" "Bash(git commit:*)" "Bash(git status:*)" \
      "Bash(git diff:*)" "Bash(git log:*)" "Bash(git rev-parse:*)" \
      "Bash(git checkout -- strategy/*)"; then
    warn "$MODE 사이클이 비정상 종료됨 (사용량 한도·네트워크·턴 상한 확인)"
    FAILED=1
  fi

  # 1) 보호 경로: 작업트리 변경 되돌리기 + 이번 사이클 커밋에 섞였는지 검사
  if ! git diff --quiet HEAD -- "${PROTECTED[@]}" 2>/dev/null; then
    warn "에이전트가 보호 파일을 수정 -> 되돌림"
    git checkout HEAD -- "${PROTECTED[@]}"
  fi
  if [ "$BEFORE" != "none" ] && [ -n "$(git diff --name-only "$BEFORE" HEAD -- "${PROTECTED[@]}")" ]; then
    warn "사이클 커밋이 보호 파일을 건드림 -> 복원 커밋: $(git diff --name-only "$BEFORE" HEAD -- "${PROTECTED[@]}" | tr '\n' ' ')"
    for p in "${PROTECTED[@]}"; do
      if git cat-file -e "$BEFORE:$p" 2>/dev/null; then git checkout "$BEFORE" -- "$p"; else git rm -rq --ignore-unmatch -- "$p"; fi
    done
    git commit -qm "operator: revert agent edits to protected paths (loop.sh)" || true
  fi
  # 2) append-only 원장: 삭제된 줄이 있으면 경고
  if [ "$BEFORE" != "none" ]; then
    for f in "${APPEND_ONLY[@]}"; do
      DEL="$(git diff --numstat "$BEFORE" HEAD -- "$f" | awk '{s+=$2} END {print s+0}')"
      if [ "$DEL" -gt 0 ]; then warn "$f 에서 ${DEL}줄 삭제됨 - 원장 변조 의심"; fi
    done
  fi
  # 3) 홀드아웃 접근
  HOLDOUT_AFTER="$(holdout_lines)"
  if [ "$HOLDOUT_AFTER" != "$HOLDOUT_BEFORE" ]; then warn "사이클 중 홀드아웃 실행 기록이 늘었음"; fi
  # 4) 무결성
  python3 core/validate.py >/dev/null || warn "core/validate.py 실패"

  # 5) 보고: 에이전트가 journal/outbox/ 에 쓴 보고서 발송 (+ core 계산 장부 꼬리말)
  python3 core/notify.py send-outbox >/dev/null || warn "보고서 발송 실패"
  if [ ${#WARNS[@]} -gt 0 ]; then
    python3 core/notify.py text "⚠️ [$MODE] 운영 경고
$(printf -- '- %s\n' "${WARNS[@]}")" >/dev/null || true
  fi
  if [ -n "$(git status --porcelain -- journal/outbox)" ]; then
    git add journal/outbox && git commit -qm "operator: reports sent" || true
  fi

  if [ "${LAB_PUSH:-0}" = "1" ] && git remote get-url origin >/dev/null 2>&1; then
    git push origin HEAD:main || echo "WARNING: push 실패" >&2
  fi
  if [ "$i" -lt "$CYCLES" ]; then sleep $((SLEEP_MIN * 60)); fi
done
exit "$FAILED"
