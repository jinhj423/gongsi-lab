# gongsi-lab — 공시 기반 매매 연구 실험

> **처음이면 [START.md](START.md) 부터.** 사장님이 직접 하실 6단계만 정리되어 있습니다.
> 운영은 GitHub Actions(권장, 서버·PC 불필요)로 돌아가고, 아래 6번의 서버 방식은 대안입니다.

phil(Polymarket 자기개선 트레이더)의 구조를 한국(DART)·미국(SEC 8-K) 공시 매매에 옮긴 것.

```
            ┌──────────── 과거 검증 (기계적) ─────────────┐
DART/SEC ─► 이벤트 DB ─► eventstudy.py ─► 유형별 기저율 ─┐  backtest.py ◄─ strategy/rules_*.yaml
                                                         │       ▲              (에이전트가 수정,
            ┌──────────── 전진 테스트 (LLM 판단) ───────┐ │       │               하루 횟수 제한,
새 공시 ─► feed.py ─► doc.py 원문 ─► Claude 확률 기록 ─┼─┴► score.py: brier_delta   홀드아웃 잠금)
                       forecast.py ─► resolve.py(20일 후 자동 채점, 페이퍼 손익)
```

- **이벤트 스터디**: 공시 유형별로 "다음 시가 진입 → 20/60거래일 후 종가"의 벤치마크 대비 초과수익(AR).
- **백테스트**: `rules_*.yaml` 규칙을 슬롯 포트폴리오로 시뮬레이션. 비용, 연도별 일관성, MDD, 시도 횟수 기록.
- **실시간 예측**: 새 공시마다 Claude 가 "h거래일 동안 벤치마크를 이길 확률"을 기록하고, 기한이 지나면
  자동 채점. 비교 대상은 이벤트 스터디 기저율. `brier_delta < 0` 이 꾸준하면 AI 판단이 실제로 가치가 있다는 뜻.

## 왜 두 층인가

LLM 에게 과거 공시를 보여주고 맞히게 하면, 모델이 그 뒤 주가를 학습 데이터로 이미 알고 있을 수 있다.
그래서 LLM 판단은 앞으로 나올 공시로만 평가하고, 과거 백테스트는 규칙(숫자)으로만 한다.

## 설치

```bash
python3 -m pip install -r requirements.txt     # Python 3.10+
git init && git add -A && git commit -m "operator: init"    # loop.sh 는 git 저장소 필요
```
Windows 라면 WSL(Ubuntu) 에서 돌리는 것을 권장 (`loop.sh`, `python3` 명령 기준으로 작성됨).
Claude Code CLI(`claude`)가 PATH 에 있어야 에이전트 루프가 동작한다.

**키** (값은 절대 저장소에 넣지 말 것):
```bash
# DART (무료): https://opendart.fss.or.kr
#   상단 메뉴 [인증키 신청/관리] -> [인증키 신청] -> 개인 회원 정보·이메일 입력, 사용 용도(예: 개인 투자 연구) 작성
#   -> 이메일 인증 링크 클릭 -> [인증키 관리] 에서 40자리 키 확인. 일일 호출 한도가 있다 (과거 수집은 며칠에 나눠짐).
mkdir -p ~/.config/gongsi-lab
echo "발급받은키" > ~/.config/gongsi-lab/dart-key        # 서버에서는 ~/.config/gongsi-lab/env 의 DART_API_KEY=
# SEC: 키 없음. 대신 이름+이메일 User-Agent 가 필수
echo "Hong Gildong you@example.com" > ~/.config/gongsi-lab/sec-user-agent
```
(환경변수 `DART_API_KEY`, `SEC_USER_AGENT` 로도 가능)

## 0. 키 없이 데모로 먼저 돌려보기

```bash
LAB_DATA=demo-data python3 core/demo.py                      # 가상 종목/공시 생성 (일부 유형에 드리프트 심어둠)
LAB_DATA=demo-data python3 core/eventstudy.py --market kr --by-year
LAB_DATA=demo-data python3 core/backtest.py --market kr --trades
python3 tests/run_tests.py && python3 tests/test_daemon.py  # 룩어헤드·원장·데몬·보고 테스트
```
데모를 돌리면 `journal/trials.jsonl`, `journal/baselines.json` 이 생긴다. 실제 데이터로 시작하기 전에 지울 것.

## 1. 과거 데이터 수집 (운영자, 1회)

```bash
python3 core/ingest.py kr --from 2018-01-01        # DART 주요사항(B)+거래소공시(I), 하루 단위, 중단 시 이어받기
python3 core/ingest.py prices kr                   # 등장 종목 + KOSPI/KOSDAQ 지수 일봉
python3 core/ingest.py us --tickers-file strategy/universe_us.txt --from 2018-01-01
python3 core/ingest.py prices us
```
DART 는 일일 호출 한도(약 2만 건)가 있어 2018년부터 받으면 하루~이틀 걸릴 수 있다. 한도에 걸리면
상태를 저장하고 멈추니 다음 날 같은 명령을 다시 실행하면 된다. 데이터는 `data/` (git 제외).

## 2. 직접 분석

```bash
python3 core/eventstudy.py --market kr --by-year          # 유형별 AR 표 + journal/baselines.json 갱신
python3 core/eventstudy.py --market kr --h 60 --types supply_contract,buyback
python3 core/backtest.py --market kr --trades              # reports/trades_kr_in.csv
```
읽는 법: `mean_ar` 크기보다 **`t_date_clustered`(|t|>2 이상)와 연도별 부호 일관성**을 먼저 본다.
`window_status` 에 `no_price` 가 많으면 상장폐지 종목 가격이 빠진 것 → 결과가 낙관적으로 치우친다.

## 3. 에이전트 루프

```bash
./loop.sh forecast            # 예측 사이클 1회 (CYCLE.md): 정산→채점→회고→피드→원문 리서치→확률 기록
./loop.sh research            # 리서치 사이클 1회 (RESEARCH.md): 이벤트 스터디→가설 1개→백테스트→채택/기각
LAB_MODEL=opus ./loop.sh forecast    # 모델 변경 (기본 sonnet, Pro 사용량 절약)
```
권장 주기 (cron 또는 Windows 작업 스케줄러 → WSL):
```
30 16 * * 1-5   cd ~/gongsi-lab && ./loop.sh forecast   # KST 16:30, 한국 장 마감 후
30  7 * * 2-6   cd ~/gongsi-lab && ./loop.sh forecast   # KST 07:30, 미국 장 마감 후
0  10 * * 6     cd ~/gongsi-lab && ./loop.sh research   # 토요일
```
`loop.sh` 는 사이클이 끝날 때마다 보호 경로 변경을 되돌리고, 원장 삭제·홀드아웃 접근·캡 위반을 경고한다.

## 4. 운영자(나)의 역할

- `journal/operator-notes.md` 로 맥락 전달, `journal/proposals.md` 의 건의 처리.
- 분기 1회 홀드아웃 확인: `LAB_OPERATOR=1 python3 core/backtest.py --market kr --holdout`
  - 인샘플에서 좋았던 규칙이 홀드아웃에서 무너지면 과최적화. 이때 `holdout_start` 를 옮겨 새 홀드아웃을 잡는다.
- `config/protected.json`: 비용 가정(세율 확인), 지평, 페이퍼 캡, 홀드아웃 날짜. 수정 커밋은 `operator:` 로 시작.

## 5. 상주 직원 모드: 계속 일하고 텔레그램으로 보고

`core/daemon.py` 를 켜두면 일정대로 일하고 보고한다. 24시간 운영은 서버를 권장 (6번).

| 시각 (KST) | 하는 일 | LLM 사용 |
|---|---|---|
| 평일 08:20 | 출근 상태 보고 (마지막 사이클, 장부, 건의사항 수) | X |
| 평일 07:30~19:00, 20분마다 | 관심종목 공시 감시 → 즉시 알림 | X |
| 매일 1시간마다 | 미국 관심종목 8-K 감시 | X |
| 평일 16:30 | 예측 사이클 (국내 장 마감 후) → 예측 보고 | O |
| 화~토 07:30 | 예측 사이클 (미국 장 마감 후) → 예측 보고 | O |
| 토 10:00 | 리서치 사이클 → 주간 리서치 보고 (+ 이번 주 전략 변경 git 기록) | O |
| 1시간마다 + 사이클 직후 | 옵시디언 보관함 갱신 (7번) | X |

일정은 `config/schedule.json`, 관심종목은 `config/my_watchlist.txt` 에서 바꾼다.
PC가 꺼져 있다 켜지면 8시간 이내에 놓친 정기 작업은 따라잡는다.

**텔레그램 설정** (한 번) - 그룹 토픽 3개로 나눠 받는 것을 권장:
1. 텔레그램 @BotFather → `/newbot` → 토큰 받기. 그룹을 만들고 설정에서 "토픽" 켜기, 봇을 관리자로 초대.
   토픽 3개 생성: `📌 공시 알림` / `📊 보고` / `⚠️ 운영`. BotFather `/setprivacy` → Disable.
2. `~/.config/gongsi-lab/telegram.json` 에 `{"token": "..."}` 만 넣고, **각 토픽에 아무 말이나 한 번씩** 보낸 뒤
   `python3 core/notify.py whoami` → 나온 chat_id, 토픽별 thread_id, 내 user_id 로 파일 완성:
   ```json
   {"token": "123:ABC", "chat_id": -1001234567890,
    "threads": {"alert": 11, "report": 12, "ops": 13},
    "allowed_user_ids": [11111111]}
   ```
   - alert: 관심종목 공시 즉시 알림 (소리 켜기) · report: 예측·주간 보고, 아침 상태 · ops: 실패·경고·출근 (평소엔 조용해야 정상)
   - 명령(/status 등)의 답은 명령을 보낸 토픽으로 온다
3. `python3 core/notify.py test` → 세 토픽에 각각 테스트 메시지가 오면 끝.

**실행**:
```bash
python3 core/daemon.py                 # 포그라운드 (테스트)
# 서버: 6번 참고 (deploy/server-setup.sh 가 systemd 서비스까지 등록)
# Windows: deploy/start-daemon-wsl.bat 을 작업 스케줄러 "로그온할 때" 로 등록
python3 core/daemon.py once forecast   # 지금 바로 한 번
```

**텔레그램으로 지시하기** (allowed_user_ids 만):
- `/status` 현재 상태와 장부 · `/run forecast|research|watch` 즉시 실행
- `/note 이번 주 2차전지 공시는 보수적으로` → 운영자 노트에 기록, 다음 사이클 직원이 읽고 반영
- `/pause` / `/resume` 정기 작업 일시정지·재개

**보고 내용이 정직한 이유**: 직원(에이전트)은 텔레그램을 직접 못 쓴다. `journal/outbox/` 에 보고서를
쓰면 사이클이 끝난 뒤 core 가 보내고, 누적 성적(brier_delta, 페이퍼 손익)은 core 가 직접 계산해서 꼬리말로 붙인다.
보호 파일 수정·원장 삭제·사이클 실패는 별도 "⚠️ 운영 경고"로 온다. 같은 작업이 계속 실패하면 1·3·10회째에만 알린다.

**사용량**: 예측 사이클 하루 2회 + 리서치 주 1회 (기본 sonnet, 사이클당 최대 80턴). 공시 감시는 LLM 을 쓰지 않는다.
Pro 한도에 걸리면 사이클이 실패로 보고되니 `LAB_MAX_TURNS` 를 줄이거나 오후 사이클만 남기면 된다.

## 6. (대안) 내 서버에서 24시간

기본은 GitHub Actions(START.md)다. 서버를 따로 운영하고 싶을 때만 아래를 따른다.

**서버 고르기** (Claude Code 는 RAM 4GB 이상 권장, ARM64 지원):

| 선택지 | 비용 | 메모 |
|---|---|---|
| Oracle Cloud Always Free (Ampere A1 ARM) | 무료 | 2026년 중반부터 무료 한도 2코어/12GB. 춘천·서울 리전. 무료 계정은 오래 놀고 있는 인스턴스를 회수할 수 있으니 종량제(PAYG)로 업그레이드 권장 (무료 한도 안이면 과금 없음). |
| 저가 VPS (4GB RAM, 국내/일본 리전) | 월 1만원 안팎 | 가입·용량 문제 없이 바로 됨 |

어느 쪽이든 Ubuntu 24.04 를 고른다. 이 시스템은 대부분 시간 대기 상태라 CPU 는 거의 안 쓴다.

**설치** (PC 에서 한 번):
```bash
# 0) GitHub 에 비공개 저장소 gongsi-lab 생성 (백업·코드 배포용)
git remote add origin git@github.com:<you>/gongsi-lab.git && git push -u origin main
claude setup-token                       # Pro 구독용 1년짜리 토큰 출력 -> 복사해 둠

# 1) 서버로 복사
scp -r ~/gongsi-lab ubuntu@<서버IP>:~/
scp ~/.config/gongsi-lab/telegram.json ubuntu@<서버IP>:~/
```
서버에서:
```bash
mkdir -p ~/.config/gongsi-lab && mv ~/telegram.json ~/.config/gongsi-lab/
cd ~/gongsi-lab && bash deploy/server-setup.sh   # 패키지, 스왑, venv, Claude Code, 배포키, systemd
nano ~/.config/gongsi-lab/env                    # 토큰, DART 키, SEC 이름/이메일
# 출력된 배포키를 GitHub 저장소 Settings -> Deploy keys 에 등록 (Allow write access)
bash deploy/server-setup.sh check                # 인증·DART·SEC·가격·텔레그램·GitHub 점검
# 과거 데이터 수집 (setup 마지막 안내대로 tmux 에서) 후
systemctl --user enable --now gongsi-lab         # 시작. 재부팅해도 자동 시작
```

**원격 운영 (텔레그램)**:
- `/log` 데몬 로그와 최근 사이클 출력 꼬리
- `/update` PC 에서 코드를 고쳐 GitHub 에 push 한 뒤 보내면, 서버가 받아서 데몬을 재시작 (작업 중이면 보류)
- 서버가 사이클마다 원장·전략을 GitHub 에 push → PC 에서 `git pull` 하면 직원이 한 일이 그대로 보인다
- Claude 토큰은 1년짜리. 만료 30일 전부터 아침 보고에 경고가 뜬다. 인증이 실패하면 사이클 경고로 "토큰 재발급"이라고 알려준다
- 서버 자체가 죽으면 데몬도 알릴 수 없다. healthchecks.io 무료 체크를 만들어 `HEALTHCHECK_URL` 에 넣으면
  10분마다 핑을 보내고, 끊기면 healthchecks.io 가 메일/텔레그램으로 알린다

Anthropic 클라우드의 예약 작업으로 돌리지 않는 이유: 그쪽 실행 환경은 외부 접속이 허용 목록으로 제한돼
DART·SEC·가격 사이트에 직접 붙지 못한다. 그래서 내 서버에서 Claude Code 를 돌린다.

## 7. 옵시디언: 섹터 > 기업 순서로 쌓이는 리서치 기록

텔레그램은 그때그때 받는 용도, 옵시디언은 쌓아두고 다시 보는 용도. 서버가 노트를 만들고
[Obsidian Headless](https://obsidian.md/help/headless)(공식 서버용 동기화 클라이언트, 오픈 베타)로 옵시디언 싱크에 올린다.
그러면 PC·폰의 옵시디언에 자동으로 나타난다.

```
공시랩/
├─ 대시보드.md                  전체 성적, 섹터별 표, 보유 페이퍼 포지션, 최근 보고
├─ 섹터/
│  ├─ 반도체/
│  │  ├─ 섹터 - 반도체.md        섹터 안 기업 목록, 섹터 성적, 최근 예측
│  │  ├─ 삼성전자 (005930).md   예측 기록표, 판단 근거, 직원 리서치 메모, 공시 타임라인, 내 메모
│  │  └─ SK하이닉스 (000660).md
│  └─ 2차전지/ ...
└─ 보고/2026-10/2026-10-07 1640 예측 보고.md   텔레그램 보고 원문 (회사명은 기업 노트 링크)
```

- **어떤 기업이 노트를 갖나**: 관심종목 + 직원이 예측했거나 리서치 메모를 남긴 기업. 수천 개 상장사 전부가 아니다.
- **직원 리서치 메모**: 직원은 기업을 리서치할 때마다 `journal/notes/` 에 날짜별 메모를 남기고, 다음에 같은 회사를
  볼 때 그걸 먼저 읽는다. 기업 노트가 곧 직원의 기억이고, 정산되면 결과도 그 메모 아래에 붙는다.
- **내 메모**: 기업 노트 맨 아래 `## 내 메모` 아래는 사장님 영역. 폰에서 써도 다음 갱신 때 보존된다
  (서버가 쓰기 전에 먼저 내려받는다). 그 위 부분은 매번 다시 생성되니 고치지 말 것.
- **섹터 기준**: 국내는 KRX 업종, 미국은 SEC 업종 설명. 너무 세밀하면 `config/sector_overrides.yaml` 에서
  묶거나(rename) 종목별로 지정(ticker). 바꾸면 노트가 새 섹터 폴더로 옮겨지고 내 메모는 따라간다.

**서버에 연결** (서버 설치 후 한 번):
```bash
ob login                                   # 옵시디언 계정 (Sync 구독 필요)
ob sync-list-remote                        # 내 원격 보관함 이름 확인
mkdir -p ~/obsidian-vault && cd ~/obsidian-vault
ob sync-setup --vault "보관함 이름" --device-name gongsi-server   # 종단간 암호화면 비밀번호 입력
ob sync --path ~/obsidian-vault           # 첫 동기화 (보관함 전체를 내려받음)
echo 'VAULT_DIR=/home/ubuntu/obsidian-vault' >> ~/.config/gongsi-lab/env
systemctl --user restart gongsi-lab
```
Headless 는 베타라 명령 옵션이 바뀔 수 있다 (`ob --help` 로 확인).
같은 기기에서 데스크톱 앱 싱크와 Headless 를 함께 쓰면 안 된다 (서버는 별도 기기라 괜찮다).

**알아둘 점**: 서버에 보관함 전체가 내려받아진다. 개인 메모가 많은 보관함이라면, 싱크 요금제가 허용하는 경우
투자 전용 보관함을 따로 만들어 연결하는 게 깔끔하다. 텔레그램에서 `/run vault` 로 즉시 갱신.

**설정 바꾸는 법**: `config/` (관심종목, 섹터 묶기, 일정) 는 서버에서 직접 고치면 사이클 후 되돌려진다.
PC 에서 고쳐 GitHub 에 push → 텔레그램 `/update`.

## 성적 보기

```bash
python3 core/score.py
```
- `brier_delta`: 내 Brier − 기저율 Brier. 음수면 기저율보다 나은 예측. **30건 미만은 노이즈.**
- `by_type`: 어떤 공시 유형에서 AI 판단이 가치를 내는지.
- `calibration_buckets`: 0.6이라고 한 것들이 실제로 60% 맞았는지.
- `paper_book`: 페이퍼 손익 (KR 원화 1천만 원, US 1만 달러 가상 자금).

## 알려진 한계 (정직하게)

- DART 목록 API 는 접수 **시각**을 주지 않는다 → 공시일 다음 거래일 시가 진입으로 보수적 처리.
  장중 공시의 당일 반응은 놓치지만 룩어헤드는 없다.
- 분류는 보고서명/8-K item 기준. "계약금액이 매출의 몇 %인가" 같은 **크기 정보는 아직 없다**.
  가장 효과가 클 개선 후보 (DART 주요사항보고서 상세 API 로 파싱).
- 가격 미수정 의심(일간 ±30.5% 초과) 구간은 제외. 상장폐지 종목은 가격이 없으면 빠진다 (생존편향).
- 미국 유니버스가 현재 대형주 목록이라 과거 백테스트에 생존편향이 있다.
- 비용은 단순 가정(KR 왕복 0.35%, US 0.10%). 세율·수수료를 확인해서 조정할 것.
- 전부 페이퍼다. 투자 조언이 아니다.
