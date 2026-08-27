@echo off
chcp 65001 >nul
echo ============================================================
echo  Tailscale HTTPS 프록시를 8070 서버 앞에 붙입니다.
echo  (내 Tailscale 계정 기기에서만 접근 - 공개 인터넷 노출 아님)
echo.
echo  사전조건:
echo   1) 이 PC 와 핸드폰에 Tailscale 설치 + 같은 계정 로그인
echo   2) 관리콘솔 login.tailscale.com/admin/dns 에서
echo      MagicDNS 와 HTTPS Certificates 활성화
echo ============================================================
echo.
tailscale serve --bg 8070
if %errorlevel% neq 0 (
  echo.
  echo [실패] tailscale 명령 오류. Tailscale 설치/로그인 상태를 확인하세요.
  echo   구버전이면:  tailscale serve https / http://localhost:8070
  pause
  exit /b
)
echo.
tailscale serve status
echo.
echo 위 https://... 주소를 핸드폰(같은 Tailscale 계정)에서 열면 됩니다.
echo 해제하려면 https_tailscale_stop.bat 실행.
pause
