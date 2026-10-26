param([ValidateSet('Start','Backup')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRepo '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Run uv sync --locked --all-packages first' }
if ($Action -eq 'Start') {
    & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $taskDesktop = Join-Path $env:ProgramFiles 'Docker/Docker/Docker Desktop.exe'
        if (!(Test-Path -LiteralPath $taskDesktop)) { throw 'Docker Desktop is not installed' }
        Start-Process -FilePath $taskDesktop -WindowStyle Hidden
    }
}
Push-Location $taskRepo
try { & $taskPython -X utf8 -m infra.master_node $Action.ToLowerInvariant(); $taskExit = $LASTEXITCODE }
finally { Pop-Location }
exit $taskExit
