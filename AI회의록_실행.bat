@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "meeting_minutes\.venv\" (
  echo [setup] AI 회의록 최초 설치 중... 5~10분 걸릴 수 있습니다.
  python -m venv meeting_minutes\.venv
  call "meeting_minutes\.venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install -r meeting_minutes\requirements.txt
) else (
  call "meeting_minutes\.venv\Scripts\activate.bat"
)

echo.
echo  SAM4S AI 회의록 실행 중... (이 창을 닫으면 프로그램도 종료됩니다)
echo  최초 음성인식 시 모델 다운로드(약 1.5GB, medium)로 시간이 걸립니다.
echo.
python -m meeting_minutes
if errorlevel 1 pause
