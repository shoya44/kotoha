@echo off
setlocal
cd /d "%~dp0"
title Kotoha Setup
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto :install
where py >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.10 or later with the Windows py launcher first.
    goto :failed
)
py -3 -m venv .venv
if errorlevel 1 goto :failed
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo The Python environment is unavailable or older than Python 3.10.
    echo Check the Python installation used to create .venv.
    goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
call kotoha.bat init
if errorlevel 1 goto :failed
echo.
echo Setup completed. Configure .env, then double-click start.bat.
pause
exit /b 0
:failed
echo.
echo Setup failed. Check the message above.
pause
exit /b 1
