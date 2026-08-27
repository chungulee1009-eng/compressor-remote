@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo 관리자 권한이 필요합니다. 권한 상승 후 다시 실행합니다...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_service.ps1"
echo.
echo [설치 완료] 이제 PC 로그인 시 서버가 자동으로 실행됩니다.
echo.
pause
