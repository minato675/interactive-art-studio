@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime.local.json" (echo Run Configure-PC2.cmd first. & pause & exit /b 1)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Runtime.ps1" --check
set "result=%errorlevel%"
pause
exit /b %result%
