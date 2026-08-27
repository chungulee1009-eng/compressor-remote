# serve_forever.ps1
# Watchdog: keeps the compressor server (run.py) running; restarts it if it exits.
# ASCII-only on purpose (Windows PowerShell 5.1 mangles non-ASCII .ps1 without a BOM).

$ErrorActionPreference = 'Continue'
$srvDir  = $PSScriptRoot
$projDir = Split-Path -Parent $srvDir

$py = Join-Path $projDir '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$logDir = Join-Path $srvDir 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$stopFlag = Join-Path $srvDir 'STOP'
if (Test-Path $stopFlag) { Remove-Item $stopFlag -Force }

Set-Location $projDir

function Write-Log([string]$msg) {
    $file = Join-Path $logDir ('server_{0}.log' -f (Get-Date -Format 'yyyyMMdd'))
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -LiteralPath $file -Value $line -Encoding utf8
}

Write-Log ('watchdog started (python: {0})' -f $py)

while (-not (Test-Path $stopFlag)) {
    Write-Log 'launching run.py'
    $file = Join-Path $logDir ('server_{0}.log' -f (Get-Date -Format 'yyyyMMdd'))
    try {
        & $py 'run.py' 2>&1 | ForEach-Object {
            Add-Content -LiteralPath $file -Value ("$_") -Encoding utf8
        }
    } catch {
        Write-Log ('exception: ' + $_.Exception.Message)
    }
    Write-Log ('run.py exited (code {0})' -f $LASTEXITCODE)
    if (Test-Path $stopFlag) { break }
    Start-Sleep -Seconds 5
}

Remove-Item $stopFlag -Force -ErrorAction SilentlyContinue
Write-Log 'watchdog stopped'
