@echo off
cd /d "%~dp0"

REM Fix Tcl/Tk for build host (Python 3.13)
if exist "D:\Python313\tcl\tcl8.6\init.tcl" (
  set "TCL_LIBRARY=D:\Python313\tcl\tcl8.6"
  set "TK_LIBRARY=D:\Python313\tcl\tk8.6"
)
if exist "%LocalAppData%\Programs\Python\Python313\tcl\tcl8.6\init.tcl" (
  set "TCL_LIBRARY=%LocalAppData%\Programs\Python\Python313\tcl\tcl8.6"
  set "TK_LIBRARY=%LocalAppData%\Programs\Python\Python313\tcl\tk8.6"
)

if not exist .venv\Scripts\python.exe (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)

echo [info] installing pyinstaller ...
.venv\Scripts\pip install -q "pyinstaller>=6.0"

echo [info] building WatchdogConsole.exe (onedir, windowed) ...
.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed ^
  --name WatchdogConsole ^
  --paths app ^
  --collect-all customtkinter ^
  --hidden-import client ^
  --hidden-import customtkinter ^
  watchdog_console.py

if errorlevel 1 (
  echo [error] build failed
  exit /b 1
)

echo.
echo [ok] output: %cd%\dist\WatchdogConsole\WatchdogConsole.exe
echo      double-click that exe; Watchdog service must be running on 127.0.0.1:8092
explorer "%cd%\dist\WatchdogConsole"
