$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $Root ".local_logs\pids.txt"

if (Test-Path $PidFile) {
    Get-Content $PidFile | ForEach-Object {
        $parts = $_ -split "="
        if ($parts.Length -eq 2) {
            $name = $parts[0]
            $procId = [int]$parts[1]
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Host "Stopped $name (PID $procId)"
            }
        }
    }
    Remove-Item $PidFile -Force
} else {
    Write-Host "No active local PID file found."
}
