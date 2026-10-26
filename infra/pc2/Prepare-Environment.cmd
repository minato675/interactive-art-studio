@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (echo Install uv first and reopen this folder. & pause & exit /b 1)
uv run --no-project --python 3.12 --with psutil==7.2.2 python -c "import psutil; print('environment_prepared: psutil=' + psutil.__version__)"
set "result=%errorlevel%"
pause
exit /b %result%
