@echo off
cd /d "%~dp0"

REM Fix Tcl/Tk for Python 3.13 on D:\
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

echo [info] starting DESKTOP GUI window ...
.venv\Scripts\python.exe app\main.py
if errorlevel 1 (
  echo [warn] desktop GUI failed, fallback to browser HUD
  .venv\Scripts\python.exe app\web_ui.py
)
