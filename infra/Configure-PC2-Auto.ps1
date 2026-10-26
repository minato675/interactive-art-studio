$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$answer = (Read-Host 'Full path to Art-Win64-Shipping.exe (not Art.exe)').Trim().Trim('"')
$executable = (Resolve-Path -LiteralPath $answer).Path
if ([IO.Path]::GetFileName($executable) -notlike '*-Win64-Shipping.exe') {
    throw 'Choose the actual Win64 Shipping executable, not the launcher.'
}
$suggestedRoot = $executable
1..4 | ForEach-Object { $suggestedRoot = Split-Path -Parent $suggestedRoot }
$answer = (Read-Host "Package root containing Art and Engine [$suggestedRoot]").Trim().Trim('"')
$workingDirectory = if ($answer) { (Resolve-Path -LiteralPath $answer).Path } else { $suggestedRoot }
if (-not (Test-Path -LiteralPath (Join-Path $workingDirectory 'Art') -PathType Container)) {
    throw 'Package root must contain the complete Art directory.'
}
$widthText = Read-Host 'Output width [1920]'
$heightText = Read-Host 'Output height [1080]'
$outputWidth = 1920
$outputHeight = 1080
if ($widthText -and (-not [int]::TryParse($widthText, [ref]$outputWidth) -or $outputWidth -lt 320)) {
    throw 'Invalid output width.'
}
if ($heightText -and (-not [int]::TryParse($heightText, [ref]$outputHeight) -or $outputHeight -lt 200)) {
    throw 'Invalid output height.'
}
$fullscreen = (Read-Host 'Use fullscreen? y/N') -eq 'y'
$windowMode = if ($fullscreen) { '-fullscreen' } else { '-windowed' }
$delivery = Join-Path $root 'delivery'
$cache = Join-Path $root 'cache'
New-Item -ItemType Directory -Path $delivery, $cache -Force | Out-Null
$config = [ordered]@{
    mode = 'show'
    auto_launch = $true
    delivery = $delivery
    cache = $cache
    executable = $executable
    working_directory = $workingDirectory
    launch_arguments = @('Art', '-dx12', '-unattended', $windowMode, "-ResX=$outputWidth", "-ResY=$outputHeight")
    apply_timeout_seconds = 120
    startup_timeout_seconds = 120
    restart_initial_seconds = 2
    restart_max_seconds = 60
}
[IO.File]::WriteAllText((Join-Path $root 'runtime.local.json'),
    ($config | ConvertTo-Json -Depth 5) + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
Write-Host 'Saved automatic launch/recovery configuration. No PID entry is needed.'
Write-Host 'Stop the previous PC2 Agent and all manual UE instances before Start-Worker.cmd.'
Write-Host 'Run Check-Runtime.cmd first. This check does not start UE.'
