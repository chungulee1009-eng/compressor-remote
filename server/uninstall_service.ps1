# uninstall_service.ps1
# Stops and removes the CompressorRemoteServer scheduled task and kills the running server.

$ErrorActionPreference = 'Continue'
$taskName = 'CompressorRemoteServer'
$srvDir   = $PSScriptRoot

# tell the watchdog loop to stop
'stop' | Out-File -LiteralPath (Join-Path $srvDir 'STOP') -Encoding ascii

try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
    Write-Host "[ok] scheduled task '$taskName' removed"
} catch {
    Write-Host "[skip] scheduled task '$taskName' not found"
}

# kill the Flask server (python run.py)
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*run.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("[ok] stopped server process pid {0}" -f $_.ProcessId)
    }

# kill any lingering watchdog
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -like '*serve_forever.ps1*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("[ok] stopped watchdog pid {0}" -f $_.ProcessId)
    }

Start-Sleep -Seconds 1
Remove-Item (Join-Path $srvDir 'STOP') -Force -ErrorAction SilentlyContinue
Write-Host "done. (firewall rule 'Compressor 8070' left in place - remove it manually if needed)"
