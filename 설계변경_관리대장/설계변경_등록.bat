@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%VENV%\installed.ok" goto run

echo [초기 설정] 가상환경 생성 및 패키지 설치 (최초 1회, 1~2분 소요)
if exist "%PY%" goto install
where py > nul 2> nul
if errorlevel 1 goto use_python
py -3 -m venv "%VENV%"
if exist "%PY%" goto install
:use_python
python -m venv "%VENV%"
if not exist "%PY%" (
    echo.
    echo [오류] Python 3.9 이상이 필요합니다. https://www.python.org 에서 설치 후 다시 실행하세요.
    echo        설치 시 "Add python.exe to PATH" 를 체크하세요.
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
"%PY%" "%~dp0ecn_register.py" %*
echo.
pause
