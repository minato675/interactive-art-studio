@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Configure-PC2.ps1"
set "result=%errorlevel%"
pause
exit /b %result%
