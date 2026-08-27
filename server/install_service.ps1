# install_service.ps1
# Registers a Scheduled Task that runs serve_forever.ps1 at every logon,
# so the compressor server starts automatically and stays up.
# Run elevated (Administrator) so the firewall rule can be added too.

$ErrorActionPreference = 'Stop'
$taskName = 'CompressorRemoteServer'
$srvDir   = $PSScriptRoot
$wrapper  = Join-Path $srvDir 'serve_forever.ps1'
$port     = 8070

$principalId = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = $principalId.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# --- firewall (needs admin) ---
if ($isAdmin) {
    if (-not (Get-NetFirewallRule -DisplayName "Compressor $port" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Compressor $port" -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $port -Profile Any | Out-Null
        Write-Host "[ok] firewall inbound rule added: TCP $port"
    } else {
        Write-Host "[skip] firewall rule already exists"
    }
} else {
    Write-Warning "not elevated - firewall rule NOT added. Add it manually in an admin prompt:"
    Write-Warning "  netsh advfirewall firewall add rule name=`"Compressor $port`" dir=in action=allow protocol=TCP localport=$port"
}

# --- scheduled task ---
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $wrapper)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "[ok] scheduled task '$taskName' registered (starts at every logon)"

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "[ok] server is listening on port $port"
} else {
    Write-Host "[..] not listening yet - check server\logs\server_*.log in a few seconds"
}

$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1 -ExpandProperty IPAddress)
Write-Host ""
Write-Host "  local : http://localhost:$port"
if ($ip) { Write-Host "  LAN   : http://${ip}:$port" }
Write-Host ""
Write-Host "Login: admin / admin1234  (change the password after first login)"
