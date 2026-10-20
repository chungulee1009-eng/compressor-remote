@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 승인원 재분석

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
