@echo off
chcp 65001 >nul
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo 관리자 권한이 필요합니다. 권한 상승 후 다시 실행합니다...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_service.ps1"
echo.
echo [제거 완료] 자동 실행이 해제되고 서버가 종료되었습니다.
echo.
pause
