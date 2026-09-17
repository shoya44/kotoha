@echo off
setlocal
cd /d "%~dp0"
title Kotoha
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run setup.bat first.
    goto :failed
)
:loop
".venv\Scripts\python.exe" -m kotoha.launcher
rem 42 is the restart request from the app. errorlevel is a "greater or equal" test,
rem so the highest code has to be checked first.
if errorlevel 42 (
    echo.
    echo Restarting Kotoha...
    echo.
    goto :loop
)
if errorlevel 1 goto :failed
exit /b 0
:failed
echo.
echo Kotoha could not start. Check the message above.
pause
exit /b 1
