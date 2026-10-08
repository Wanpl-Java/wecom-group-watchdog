@echo off
cd /d "%~dp0\.."
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b "BRIDGE_INTERVAL=" ".env"`) do set "INTERVAL=%%B"
)
if "%INTERVAL%"=="" set INTERVAL=60
if "%BRIDGE_INTERVAL%" NEQ "" set INTERVAL=%BRIDGE_INTERVAL%

echo [bridge] starting SentLink sync loop every %INTERVAL%s
set PYTHONUNBUFFERED=1
".\.venv\Scripts\python.exe" -u "scripts\bridge_sentlink.py" --loop --interval %INTERVAL%

