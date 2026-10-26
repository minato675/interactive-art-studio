$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot 'runtime.local.json'
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$worker = Join-Path $PSScriptRoot 'runtime_worker.py'
if ($config.mode -eq 'verify') {
    Push-Location -LiteralPath $config.repository
    try {
        & uv run python $worker --folder $PSScriptRoot @args
        if ($LASTEXITCODE -ne 0) { throw 'PC1 verification worker failed.' }
    } finally { Pop-Location }
} elseif ($config.mode -eq 'show') {
    $offlineArguments = @()
    if ($config.auto_launch -and $args -notcontains '--check') { $offlineArguments = @('--offline') }
    & uv run --no-project @offlineArguments --python 3.12 --with psutil==7.2.2 python $worker --folder $PSScriptRoot @args
    if ($LASTEXITCODE -ne 0) { throw 'PC2 display worker failed.' }
} else { throw 'Invalid runtime mode.' }
