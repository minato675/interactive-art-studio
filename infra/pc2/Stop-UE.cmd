@echo off
setlocal
cd /d "%~dp0"
echo Stop Start-Worker with Ctrl+C first. This stops only the verified owned UE.
uv run --offline --no-project --python 3.12 --with psutil==7.2.2 python "%~dp0show_supervisor.py" --stop
set "result=%errorlevel%"
pause
exit /b %result%
