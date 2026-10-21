@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title SA 문서 통합관리 (승인원 / 설계변경)
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

echo "%~dp0" | findstr /i /c:".zip" > nul
if not errorlevel 1 (
    echo.
    echo [오류] 압축파일 안에서 바로 실행 중입니다. 이 상태에서는 등록 내용이 누적되지 않습니다.
    echo        zip 파일 우클릭 - [압축 풀기] 로 고정 폴더에 푼 뒤 그 폴더의 실행.bat 을 실행하세요.
    pause
    exit /b 1
)

if exist "%VENV%\installed.ok" goto run

echo [초기 설정] 프로그램 실행 환경 설치 중 (최초 1회, 1~3분 소요)
if exist "%PY%" goto install
where py > nul 2> nul
if errorlevel 1 goto use_python
py -3 -m venv "%VENV%"
if exist "%PY%" goto install
:use_python
python -m venv "%VENV%"
if not exist "%PY%" (
    echo.
    echo [오류] Python 3.9 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
    echo        설치 화면에서 "Add python.exe to PATH" 를 반드시 체크하세요.
    pause
    exit /b 1
)

:install
"%PY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%PY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [오류] 패키지 설치 실패. 인터넷 연결 또는 사내 프록시 설정을 확인하세요.
    pause
    exit /b 1
)
echo ok > "%VENV%\installed.ok"

:run
"%PY%" "%~dp0app.py"
echo.
pause
