$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root ".local_logs"

if (-not (Test-Path $Python)) {
    Write-Error "Virtual environment not found at $Python"
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Load environment variables from .env if present (gitignored)
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line -split "=", 2
            $k = $parts[0].Trim()
            $v = $parts[1].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
}

$env:LOCAL_MODE = "true"
$env:ADVISORY_SVC_URL = "http://127.0.0.1:8002/advisories"
$env:SEARCH_SVC_URL = "http://127.0.0.1:8003/search"
$env:USER_SVC_URL = "http://127.0.0.1:8001"
$env:CHATBOT_SVC_URL = "http://127.0.0.1:8004"

$Services = @(
    @{ Name = "user-service"; Dir = "user-service"; Port = 8001 },
    @{ Name = "advisory-service"; Dir = "advisory-service"; Port = 8002 },
    @{ Name = "search-rec-service"; Dir = "search-rec-service"; Port = 8003 },
    @{ Name = "chatbot-service"; Dir = "chatbot-service"; Port = 8004 },
    @{ Name = "gateway-ui-service"; Dir = "gateway-ui-service"; Port = 8000 }
)

$PidFile = Join-Path $LogDir "pids.txt"
if (Test-Path $PidFile) {
    Remove-Item $PidFile -Force
}

foreach ($svc in $Services) {
    $svcDir = Join-Path $Root $svc.Dir
    $outLog = Join-Path $LogDir "$($svc.Name).out.log"
    $errLog = Join-Path $LogDir "$($svc.Name).err.log"

    if ($svc.Name -eq "gateway-ui-service") {
        $env:SEARCH_SVC_URL = "http://127.0.0.1:8003"
        $env:ADVISORY_SVC_URL = "http://127.0.0.1:8002"
    }

    $proc = Start-Process -FilePath $Python `
        -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$($svc.Port)" `
        -WorkingDirectory $svcDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru

    Add-Content -Path $PidFile -Value "$($svc.Name)=$($proc.Id)"
    Write-Host "Started $($svc.Name) on http://127.0.0.1:$($svc.Port) (PID $($proc.Id))"
}

Write-Host "`nAll 5 services launched. Open http://127.0.0.1:8000 in your browser."
