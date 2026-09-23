@echo off
rem The one entry point. Everything else is a subcommand of this file.
rem Keeping a single door means nothing has to be remembered about which
rem file to click, and shortcuts and the docs can all point at one name.
rem The long parts live in scripts\ and are not meant to be run directly.
rem
rem   double-click / no argument  -> live in the task tray (the normal way)
rem   kotoha.bat console          -> run in this window, with the log visible
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"

rem Was this file double-clicked? Then the window closes as soon as it ends,
rem so wait at the end to leave any message readable. A console call stays quiet.
set "CLICKED="
echo %cmdcmdline% | find /i "%~f0" >nul && set "CLICKED=1"

rem setup and rescue have to work before the Python environment exists.
if /i "%~1"=="setup"  goto :setup
if /i "%~1"=="rescue" goto :rescue
if /i "%~1"=="help"   goto :help
if /i "%~1"=="--help" goto :help
if /i "%~1"=="/?"     goto :help

if not exist "%PY%" goto :nopython

if "%~1"==""            goto :tray
if /i "%~1"=="tray"     goto :tray
if /i "%~1"=="console"  goto :console
if /i "%~1"=="menu"     goto :menu
if /i "%~1"=="settings" goto :settings

rem The rest goes straight to the CLI: status, backup, memory, note, ...
"%PY%" -m kotoha %*
exit /b %errorlevel%

rem --------------------------------------------------------------
:tray
rem Starts (or finds) the tray and opens the browser. The tray itself runs
rem under pythonw, so this window only carries the message.
title Kotoha
"%PY%" -m kotoha tray
set "tray_result=%errorlevel%"
if not "%tray_result%"=="0" (
    echo.
    echo Kotoha could not start in the tray. Check the message above,
    echo or run "kotoha.bat console" to watch the log in a window.
    if defined CLICKED pause
    exit /b %tray_result%
)
if defined CLICKED timeout /t 3 >nul 2>&1
exit /b 0

:console
title Kotoha (console)
:loop
"%PY%" -m kotoha.launcher
rem 42 is the restart request from the app. Compare the exact value: "if errorlevel"
rem is a "greater or equal" test, and a crash code (0xC0000005) is negative in cmd.
if "%errorlevel%"=="42" (
    echo.
    echo Restarting Kotoha...
    echo.
    goto :loop
)
if not "%errorlevel%"=="0" goto :failed
exit /b 0

:failed
echo.
echo Kotoha could not start. Check the message above.
pause
exit /b 1

rem --------------------------------------------------------------
:setup
call "%~dp0scripts\setup.bat"
exit /b %errorlevel%

:rescue
call "%~dp0scripts\rescue.bat"
exit /b %errorlevel%

:menu
call "%~dp0scripts\menu.bat"
exit /b %errorlevel%

:settings
title Kotoha Settings
"%PY%" -m kotoha.settings
set "settings_result=%errorlevel%"
if not "%settings_result%"=="0" echo Settings check failed. Run "kotoha.bat settings" again to correct it.
pause
exit /b %settings_result%

rem --------------------------------------------------------------
:nopython
echo Python environment not found. Run "kotoha.bat setup" first.
rem Only wait when it was double-clicked, so a console call stays quiet.
if "%~1"=="" pause
exit /b 1

:help
echo.
echo   kotoha.bat               live in the task tray and open the browser.
echo                            Double-click this. Same as "kotoha.bat tray"
echo   kotoha.bat console       run in a window instead, with the log visible
echo   kotoha.bat setup         create the Python environment. Once, at the start
echo   kotoha.bat settings      edit and check .env
echo   kotoha.bat autostart on  start the tray at logon (off / status too)
echo   kotoha.bat menu          a menu of the usual things
echo   kotoha.bat rescue        prepare remote recovery (needs administrator)
echo.
echo   kotoha.bat start         talk in the console
echo   kotoha.bat status        show how she is doing
echo   kotoha.bat memory        show what she remembers
echo   kotoha.bat remind        show what she was asked to hold
echo   kotoha.bat note "..."    let her remember something about herself
echo   kotoha.bat backup        take a copy of the memories
echo   kotoha.bat tailscale     start Tailscale and show how it is doing
echo   kotoha.bat consolidate   turn the unprocessed talk into memories now
echo   kotoha.bat test-llm      check that the Gemini key works
echo.
exit /b 0
