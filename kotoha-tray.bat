@echo off
rem Double-click to live in the task tray. Same as "kotoha.bat tray".
rem kotoha.bat stays the one entry point; this file only saves opening a console.
call "%~dp0kotoha.bat" tray
if errorlevel 1 pause
