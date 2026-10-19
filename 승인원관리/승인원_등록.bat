@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 승인원 엑셀 등록

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
if "%~1"=="" (
  echo  [수신함 모드] 승인원_수신함 폴더의 PDF 를 등록합니다.
) else (
  echo  [드래그 모드] 끌어놓은 PDF 를 등록합니다.
)
echo.
python 승인원_등록.py %*
echo.
pause
