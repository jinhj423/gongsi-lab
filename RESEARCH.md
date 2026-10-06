# 리서치 사이클 절차 (research cycle)

너는 공시 기반 매매 규칙을 연구하는 에이전트다. 주 1회 정도 실행된다.
이 절차를 한 번 수행하고 멈춘다. CYCLE.md 의 절대 규칙이 모두 적용된다.

## 이 사이클의 목적과 위험

목적: `strategy/rules_kr.yaml`, `strategy/rules_us.yaml` 을 증거에 따라 개선하고,
이벤트 스터디 결과로 `watch.yaml` 과 `playbook.md` 를 갱신한다.

가장 큰 위험은 **과최적화**다. 백테스트를 여러 번 돌려 가장 좋은 숫자를 고르면
과거에만 맞는 규칙이 나온다. 그래서:
- 백테스트는 24시간에 `max_backtests_per_day` 회로 제한된다 (core가 강제).
  모든 실행은 `journal/trials.jsonl` 에 기록되며 지워지지 않는다.
- 최근 구간(홀드아웃)은 너에게 보이지 않는다. 운영자가 분기마다 확인한다.
- 이 사이클에서 규칙 변경은 **시장당 최대 1개 가설**만 시험한다.

## 절차

1. `journal/operator-notes.md`, 최근 `journal/research/` 메모 3개, `python3 core/score.py`
   (실시간 예측 성적)를 읽는다.
2. **이벤트 스터디**: `python3 core/eventstudy.py --market kr --by-year` 와 `--market us`
   (필요하면 `--h 60`). 이 실행은 `journal/baselines.json` 도 갱신한다.
   읽을 것: 유형별 n, mean_ar, hit, t_date_clustered, 연도별 부호의 일관성,
   window_status 의 제외 사유(no_price 가 많으면 생존편향 경고).
3. **가설 작성 먼저**: `journal/research/R-<UTC YYYYMMDD>-<market>.md` 에
   - 현재 규칙의 기준 성과 (직전 리서치 메모의 숫자, 없으면 이번에 1회 실행)
   - 바꿀 것 하나와, 왜 나아질 것으로 보는지 (이벤트 스터디 근거 인용)
   - 채택 기준을 **실행 전에** 적는다. 기본 기준:
     (a) 거래 수 ≥ 50, (b) 연도별로 기준 규칙보다 나은 해가 2/3 이상,
     (c) Sharpe 개선, MDD 악화 5%p 이내.
4. **규칙 수정 → 실행**: `strategy/rules_<market>.yaml` 수정 후
   `python3 core/backtest.py --market <market>`
5. **판정**: 미리 적은 기준으로만 판정한다. 기준 미달이면
   `git checkout -- strategy/rules_<market>.yaml` 로 되돌리고 "기각"으로 기록.
   기각된 가설도 메모에 남긴다 (같은 실패를 반복하지 않도록).
6. **watch.yaml / playbook 갱신**: 이벤트 스터디에서 n이 충분하고 연도별로 일관된
   유형은 예측 대상 유지·추가, 실시간 brier_delta 가 30건 이상에서 꾸준히 나쁜
   유형은 제외 후보. 바꿨다면 근거를 playbook 에 적는다.
7. **건의**: 분류 규칙 부족, 필요한 피처(예: 계약금액/매출 비율 파싱), 데이터 문제
   등 core 변경이 필요한 것은 `journal/proposals.md` 에 증거와 함께 적는다.
8. 로그 한 줄 `journal/cycles.log`:
   `<UTC ISO> research cycle: kr <채택|기각|무변경> ..., us ..., backtests used N`
9. **주간 보고**: `journal/outbox/<UTC YYYYMMDD-HHMM>-research.md` (평문, 30줄 이내). 텔레그램으로 전송되며
   최근 7일 전략 변경(git)과 장부가 자동으로 붙는다.
   ```
   🔬 주간 리서치 보고 10/11
   결론: <이번 주 가장 중요한 발견 한 줄>

   이번 주 시험한 가설
   - KR: <가설> → 채택/기각 (<기준 대비 결과 한 줄>)
   - US: ...

   이벤트 스터디에서 눈에 띈 것
   - <유형>: 평균 초과수익 <x%>, t=<t>, 연도별 <일관/불안정>

   다음 주 계획 / 확인 부탁
   - ...
   ```
10. 커밋: `git add -A && git commit -m "research: <한 줄 요약>"`
