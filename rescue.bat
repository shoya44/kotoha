@echo off
setlocal
cd /d "%~dp0"
title Kotoha Rescue

rem Prepare this PC so Kotoha can be brought back from outside the house.
rem Run this ONCE, at home. What you do from the phone later is printed
rem at the end (and written in docs/07).
rem
rem Three steps, checked and applied one at a time. Nothing is changed
rem without asking. Steps 2 and 3 alter Windows settings, so this needs
rem administrator rights.

net session >nul 2>&1
if not errorlevel 1 goto :elevated
echo Administrator rights are needed. A confirmation dialog will appear.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
if errorlevel 1 (
    echo Could not elevate. Right-click rescue.bat and pick "Run as administrator".
    pause
)
exit /b 0

:elevated
set "TASK=Kotoha Watchdog"
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "TRAY=%~dp0tray.pyw"
set "DID1=no"
set "DID2=no"
set "DID3=no"
set "SSHSTATE=unknown"

cls
echo ===============================================
echo  Kotoha Rescue - remote recovery preparation
echo ===============================================
echo.
echo  1. Watchdog    bring the tray back by itself      (do this one)
echo  2. OpenSSH     start it by hand from a phone       (last resort)
echo  3. Wake        let the PC wake from sleep          (only if it sleeps)
echo.
pause

rem --------------------------------------------------------------
rem  1. Watchdog
rem --------------------------------------------------------------
:step1
cls
echo === 1. Watchdog ===
echo.
echo Kotoha only restarts itself when it asks for it (exit code 42).
echo Any other crash leaves start.bat waiting at "pause", which nobody
echo can clear from outside. A scheduled task every 5 minutes fixes that.
echo.
echo Starting the tray twice is safe: tray.py holds a mutex and the second
echo one quietly backs off. So no liveness check is needed here.
echo.
if not exist "%PYW%" (
    echo   [warn] %PYW% not found. Run setup.bat first.
    echo       Skipping this step.
    pause
    goto :step2
)
schtasks /query /tn "%TASK%" >nul 2>&1
if errorlevel 1 (
    echo   Current state: not registered
) else (
    echo   Current state: already registered ^(it will be replaced^)
)
echo.
choice /c YN /n /m "Register the watchdog task? [Y/N] "
if errorlevel 2 goto :step2
schtasks /create /f /it /tn "%TASK%" /sc minute /mo 5 ^
    /tr "\"%PYW%\" \"%TRAY%\"" >nul
if errorlevel 1 (
    echo   [warn] Could not register the task.
) else (
    echo   Registered. It runs every 5 minutes while you are logged on.
    set "DID1=yes"
)
echo.
pause

rem --------------------------------------------------------------
rem  2. OpenSSH Server
rem --------------------------------------------------------------
:step2
cls
echo === 2. OpenSSH Server ===
echo.
echo With this on, you can reach the PC over Tailscale from an SSH app on
echo the phone and start Kotoha again:  schtasks /run /tn "%TASK%"
echo.
echo   [warn] This opens a way in. Keep it inside the tailnet, and prefer
echo       key authentication over passwords.
echo.
for /f "delims=" %%S in ('powershell -NoProfile -Command "(Get-WindowsCapability -Online -Name OpenSSH.Server*).State" 2^>nul') do set "SSHSTATE=%%S"
echo   Current state: %SSHSTATE%
echo.
choice /c YN /n /m "Install and enable the OpenSSH server? [Y/N] "
if errorlevel 2 goto :step3
rem PowerShell returns 0 even when a cmdlet fails, unless it is told to stop.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null; Set-Service -Name sshd -StartupType Automatic; Start-Service sshd } catch { exit 1 }"
if errorlevel 1 (
    echo   [warn] Could not enable the OpenSSH server.
) else (
    echo   Enabled and set to start with Windows.
    set "DID2=yes"
)
echo.
pause

rem --------------------------------------------------------------
rem  3. Wake from sleep
rem --------------------------------------------------------------
:step3
cls
echo === 3. Wake from sleep ===
echo.
echo Tailscale does not answer while the PC sleeps. Waking it needs
echo Wake-on-LAN, and a magic packet can only come from the same LAN -
echo a router, a smart plug or another always-on machine at home.
echo.
echo The easier way is not to sleep at all: set KOTOHA_KEEP_AWAKE=true in
echo .env and the tray keeps the PC awake while it runs. The screen still
echo turns off, and the power plan itself is left alone. If that is on,
echo and the power plan does not sleep on AC, you can skip this step.
echo.
echo Fast startup has to be off, or the network card stays asleep.
echo.
echo   Devices allowed to wake this PC now:
powercfg -devicequery wake_armed
echo.
choice /c YN /n /m "Turn off fast startup? [Y/N] "
if errorlevel 2 goto :done
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" ^
    /v HiberbootEnabled /t REG_DWORD /d 0 /f >nul
if errorlevel 1 (
    echo   [warn] Could not change the setting.
) else (
    echo   Fast startup is off. Hibernation itself is untouched.
    set "DID3=yes"
)
echo.
echo   Two things are left, and neither can be done from here:
echo.
echo     - Arm the network card. Pick yours from the list below and run:
echo         powercfg -deviceenablewake "the exact name"
echo.
powercfg -devicequery wake_from_any
echo.
echo     - Turn on Wake-on-LAN in the BIOS/UEFI.
echo.
pause

rem --------------------------------------------------------------
:done
cls
echo === Done ===
echo.
echo   1. Watchdog        %DID1%
echo   2. OpenSSH server  %DID2%
echo   3. Fast startup    %DID3%
echo.
echo ===============================================
echo  From the phone, in this order
echo ===============================================
echo.
echo   1. Tailscale app: is this PC online?
echo        offline -^> it is asleep or off. Nothing can be done from
echo        outside; Wake-on-LAN only reaches it from inside the house.
echo   2. The URL opens, but she is acting strangely
echo        settings (gear) -^> "Restart". The tray brings her back up.
echo   3. The URL does not open
echo        wait 5 minutes. The watchdog task starts the tray again.
echo   4. Still nothing
echo        SSH in and run:  schtasks /run /tn "%TASK%"
echo        That starts the tray in YOUR desktop session, so the icon and
echo        the figure come back too. Without the watchdog task, run
echo        cd /d "%~dp0" ^&^& kotoha.bat tray - the chat screen works,
echo        but nothing appears on the desktop at home.
echo.
echo To undo:
echo   1. schtasks /delete /tn "%TASK%" /f
echo   2. powershell -Command "Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"
echo   3. reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled /t REG_DWORD /d 1 /f
echo.
pause
exit /b 0
