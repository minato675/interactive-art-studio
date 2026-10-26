@echo off
setlocal
cd /d "%~dp0"
uv run --no-project --python 3.12 --with psutil==7.2.2 python "%~dp0client.py" --check
set "result=%errorlevel%"
pause
exit /b %result%
