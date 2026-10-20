@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 승인원 재분석

python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo  [오류] Python 이 설치되어 있지 않습니다.
  echo         https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
  echo         설치 화면에서 "Add python.exe to PATH" 를 반드시 체크하세요.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\" (
  echo [setup] 최초 1회 가상환경 생성 및 패키지 설치 중...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip >nul
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

echo.
echo  [재분석] 보관 중인 승인원 PDF 를 다시 분석해 대장을 갱신합니다.
echo           (No, 등록일시, 비고 는 그대로 유지 / 변경 내용은 처리로그 시트에 기록)
echo.
python 승인원_등록.py --재분석
echo.
pause
