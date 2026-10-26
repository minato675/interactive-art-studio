param()
$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'RanelMaster'
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskSid = $taskIdentity.User.Value
& icacls $taskRoot /inheritance:r /grant:r "*${taskSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect master state directory' }
$taskPowerFile = Join-Path $taskRoot 'power-before.json'
$taskEarlierPower = Join-Path $env:USERPROFILE 'AppData/Local/Ranel/MasterNode/power-before.json'
if (!(Test-Path -LiteralPath $taskPowerFile) -and (Test-Path -LiteralPath $taskEarlierPower)) {
    Copy-Item -LiteralPath $taskEarlierPower -Destination $taskPowerFile
}
if (!(Test-Path -LiteralPath $taskPowerFile)) {
    [ordered]@{ scheme=(& powercfg /getactivescheme | Out-String); sleep=(& powercfg /query SCHEME_CURRENT SUB_SLEEP | Out-String) } |
        ConvertTo-Json | Set-Content -LiteralPath $taskPowerFile -Encoding utf8
}
& powercfg /change standby-timeout-ac 0
if ($LASTEXITCODE -ne 0) { throw 'Cannot configure AC standby timeout' }
& powercfg /change hibernate-timeout-ac 0
if ($LASTEXITCODE -ne 0) { throw 'Cannot configure AC hibernation timeout' }
$taskStart = Join-Path $PSScriptRoot 'Start-Master.ps1'
$taskShell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (!$taskShell) { $taskShell = (Get-Command powershell).Source }
$taskArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $taskStart + '"'
$taskShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Ranel Master.lnk'
$taskShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($taskShortcutPath)
$taskShortcut.TargetPath = $taskShell
$taskShortcut.Arguments = $taskArgs + ' -Action Start'
$taskShortcut.WorkingDirectory = Split-Path $PSScriptRoot -Parent
$taskShortcut.Description = 'Start Ranel master after Windows user logon'
$taskShortcut.Save()
$taskReport = [ordered]@{ repository=$taskShortcut.WorkingDirectory; startup=$taskShortcutPath; ac_sleep_seconds=0; daily_backup=$false; remote_pc_check='deferred' }
try {
    $taskAction = New-ScheduledTaskAction -Execute $taskShell -Argument ($taskArgs + ' -Action Backup') -WorkingDirectory $taskShortcut.WorkingDirectory
    $taskTrigger = New-ScheduledTaskTrigger -Daily -At '19:00'
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskIdentity.Name -LogonType Interactive -RunLevel Limited
    $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName 'Ranel-Master-Backup' -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
    $taskReport.daily_backup = $true
} catch { $taskReport.backup_registration_error = $_.Exception.GetType().Name }
$taskReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'installation.json') -Encoding utf8
$taskReport | ConvertTo-Json
