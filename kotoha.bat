@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run setup.bat first.
    exit /b 1
)
".venv\Scripts\python.exe" -m kotoha %*
exit /b %errorlevel%
