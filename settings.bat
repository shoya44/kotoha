@echo off
setlocal
cd /d "%~dp0"
title Kotoha Settings
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m kotoha.settings
set "settings_result=%errorlevel%"
if not "%settings_result%"=="0" echo Settings check failed. Run settings.bat again to correct it.
pause
exit /b %settings_result%
