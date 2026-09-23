@echo off
rem Called by "kotoha.bat menu". Not meant to be run directly.
setlocal
cd /d "%~dp0.."
title Kotoha Menu
set "KOTOHA=%~dp0..\kotoha.bat"
:menu
cls
echo === Kotoha ===
echo 1. Start in the task tray (opens the browser)
echo 2. Start in a window, with the log visible
echo 3. Start console chat
echo 4. Show status
echo 5. Show memories
echo 6. Backup database
echo 7. Open Tailscale
echo 8. Settings (edit and validate .env)
echo 9. Setup Python dependencies
echo 0. Exit
choice /c 1234567890 /n /m "Select: "
if errorlevel 10 exit /b 0
if errorlevel 9 goto :setup
if errorlevel 8 goto :config
if errorlevel 7 goto :tailscale
if errorlevel 6 goto :backup
if errorlevel 5 goto :memory
if errorlevel 4 goto :status
if errorlevel 3 goto :console
if errorlevel 2 goto :window
if errorlevel 1 goto :tray
exit /b 1
:tray
call "%KOTOHA%" tray
pause
goto :menu
:window
start "Kotoha" "%ComSpec%" /d /c call "%KOTOHA%" console
goto :menu
:console
call "%KOTOHA%" start
pause
goto :menu
:status
call "%KOTOHA%" status
pause
goto :menu
:memory
call "%KOTOHA%" memory
pause
goto :menu
:backup
call "%KOTOHA%" backup
pause
goto :menu
:tailscale
call "%KOTOHA%" tailscale
pause
goto :menu
:config
call "%KOTOHA%" settings
goto :menu
:setup
call "%KOTOHA%" setup
goto :menu
