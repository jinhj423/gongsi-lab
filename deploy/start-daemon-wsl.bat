@echo off
REM Windows 작업 스케줄러용: 트리거 "로그온할 때", 동작 "프로그램 시작" = 이 파일.
REM 데몬이 포그라운드로 실행되어야 WSL 이 꺼지지 않는다 (창은 작업 스케줄러에서 숨김 실행 권장).
REM 전원 옵션에서 절전 모드를 "안 함" 으로 두어야 밤새 일한다.
wsl.exe -d Ubuntu -- bash -lc "cd ~/gongsi-lab && mkdir -p data && exec python3 core/daemon.py >> data/daemon.log 2>&1"
