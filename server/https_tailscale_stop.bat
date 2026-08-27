@echo off
chcp 65001 >nul
tailscale serve reset
echo Tailscale HTTPS 프록시를 해제했습니다. (서버 8070 자체는 계속 실행 중)
pause
