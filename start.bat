@echo off
setlocal
cd /d "%~dp0"
title Kotoha
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run setup.bat first.
    goto :failed
)
".venv\Scripts\python.exe" -m kotoha.launcher
if errorlevel 1 goto :failed
exit /b 0
:failed
echo.
echo Kotoha could not start. Check the message above.
pause
exit /b 1
