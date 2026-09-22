@echo off
rem Called by "kotoha.bat menu". Not meant to be run directly.
setlocal
cd /d "%~dp0.."
title Kotoha Menu
set "KOTOHA=%~dp0..\kotoha.bat"
:menu
cls
echo === Kotoha ===
echo 1. Start Web + browser + Tailscale
echo 2. Start console chat
echo 3. Show status
echo 4. Show memories
echo 5. Backup database
echo 6. Open Tailscale
echo 7. Open README
echo 8. Settings (edit and validate .env)
echo 9. Setup Python dependencies
echo 0. Exit
choice /c 1234567890 /n /m "Select: "
if errorlevel 10 exit /b 0
if errorlevel 9 goto :setup
if errorlevel 8 goto :config
if errorlevel 7 goto :readme
if errorlevel 6 goto :tailscale
if errorlevel 5 goto :backup
if errorlevel 4 goto :memory
if errorlevel 3 goto :status
if errorlevel 2 goto :console
if errorlevel 1 goto :web
exit /b 1
:web
start "Kotoha" "%ComSpec%" /d /c call "%KOTOHA%"
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
:readme
start "" notepad.exe "%~dp0..\README.md"
goto :menu
:config
call "%KOTOHA%" settings
goto :menu
:setup
call "%KOTOHA%" setup
goto :menu
