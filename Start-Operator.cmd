@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required. Install uv, then run this file again.
  echo https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
set "CENTRAL_URL=https://k15a504.p.ssafy.io"
set "OPERATOR_TOKEN="
uv sync --locked --all-packages
if errorlevel 1 (
  pause
  exit /b 1
)
uv run --locked --all-packages a504-operator
if errorlevel 1 pause
