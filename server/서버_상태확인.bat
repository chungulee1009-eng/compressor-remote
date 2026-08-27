@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 예약 작업 상태 ===
schtasks /query /tn CompressorRemoteServer /fo LIST 2>nul | findstr /i "TaskName 상태 Status 다음 Next" || echo (미설치)
echo.
echo === 포트 8070 수신 상태 ===
netstat -ano | findstr ":8070" | findstr LISTENING || echo (수신 대기 없음 - 서버 미실행)
echo.
echo === 최근 로그 20줄 ===
powershell -NoProfile -Command "$f = Get-ChildItem '%~dp0logs\server_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1; if ($f) { Write-Host $f.FullName; Get-Content $f.FullName -Tail 20 } else { Write-Host '(로그 없음)' }"
echo.
pause
