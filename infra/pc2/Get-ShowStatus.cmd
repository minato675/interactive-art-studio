@echo off
setlocal
cd /d "%~dp0"
uv run --offline --no-project --python 3.12 --with psutil==7.2.2 python "%~dp0show_check.py"
set "result=%errorlevel%"
pause
exit /b %result%
