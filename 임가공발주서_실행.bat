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
  python -m pip install -q openpyxl pyxlsb pywin32
)

echo.
echo  임가공 발주서(수삽) 작성기 - 브라우저에서 http://127.0.0.1:8071 접속
echo  종료: Ctrl+C
echo.
start "" http://127.0.0.1:8071
python -m subcon_po
pause
