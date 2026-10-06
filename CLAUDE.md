# gongsi-lab

공시·뉴스 기반 주식 매매 연구 실험. 두 층:
1. 기계적 이벤트 스터디/백테스트 (`core/eventstudy.py`, `core/backtest.py`) - 과거 검증 가능한 기준선.
2. 헤드리스 Claude 의 실시간 확률 예측 (`CYCLE.md`) - LLM 판단은 학습 데이터 오염 때문에
   과거 백테스트가 불가능하므로 전진 테스트로만 평가. 기준선 대비 brier_delta 로 채점.

## 경계
- `core/`, `config/`, `tests/`, 최상위 문서/스크립트: 운영자 전용. 운영자 커밋은 `operator:` 접두어.
- `strategy/`: 에이전트가 증거에 따라 수정. git 히스토리가 곧 실험 결과물.
- `journal/*.jsonl`: core 스크립트만 append. 사람도 손으로 고치지 않는다.
- 홀드아웃 구간 결과는 `LAB_OPERATOR=1` 로만 열람 (운영자가 분기 1회).

## 절차서
- `CYCLE.md` 예측 사이클 (매일), `RESEARCH.md` 리서치 사이클 (매주). `loop.sh` 가 실행.

## 상주 운영
- `core/daemon.py` 가 `config/schedule.json` 대로 `loop.sh`·`core/watch.py` 를 실행하고 텔레그램으로 보고.
- 에이전트 프로세스는 `LAB_AGENT=1` 로 실행되며 core 가 알림 발송·홀드아웃·데몬 실행을 거부한다.
- 에이전트의 보고는 `journal/outbox/*.md` 로만. 발송과 장부 숫자는 core 담당.
