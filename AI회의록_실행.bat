@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 가상환경은 OneDrive 동기화 폴더 밖(로컬 앱데이터)에 둔다 — 수천 개 파일 동기화로 인한 충돌/손상 방지
set "VENV=%LOCALAPPDATA%\SAM4S_MeetingMinutes\venv"

if not exist "%VENV%\Scripts\python.exe" (
  echo [setup] AI 회의록 최초 설치 중... 5~10분 걸릴 수 있습니다.
  python -m venv "%VENV%"
  if errorlevel 1 (
    echo [오류] Python 이 설치되어 있지 않거나 PATH 에 없습니다. python.org 에서 3.12 설치 시 "Add python.exe to PATH" 를 체크하세요.
    pause
    exit /b 1
  )
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -r meeting_minutes\requirements.txt
)

echo.
echo  SAM4S AI 회의록 실행 중... (이 창을 닫으면 프로그램도 종료됩니다)
echo  최초 음성인식 시 모델 다운로드(약 1.5GB, medium)로 시간이 걸립니다.
echo.
"%VENV%\Scripts\python.exe" -m meeting_minutes
if errorlevel 1 pause
