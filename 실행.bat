@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\" (
  echo [setup] 가상환경 생성 중...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

echo.
echo  브라우저에서 http://127.0.0.1:8070 접속
echo  로그인: admin / admin1234  (첫 로그인 후 비밀번호 변경)
echo  종료: Ctrl+C
echo.
python run.py
pause
