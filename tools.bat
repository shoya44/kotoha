@echo off
setlocal
cd /d "%~dp0"
title Kotoha Tools
:menu
cls
echo === Kotoha Tools ===
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
start "Kotoha" "%ComSpec%" /d /c call "%~dp0start.bat"
goto :menu
:console
call kotoha.bat start
pause
goto :menu
:status
call kotoha.bat status
pause
goto :menu
:memory
call kotoha.bat memory
pause
goto :menu
:backup
call kotoha.bat backup
pause
goto :menu
:tailscale
call kotoha.bat tailscale
pause
goto :menu
:readme
start "" notepad.exe "%~dp0README.md"
goto :menu
:config
call settings.bat
goto :menu
:setup
call setup.bat
goto :menu
