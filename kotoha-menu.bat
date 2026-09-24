@echo off
rem ことはのメニュー。ダブルクリックすると番号で選べます。引数は要りません。
rem 起動だけなら kotoha.bat をダブルクリック。それ以外はこちら。
rem このファイルは Shift_JIS（CP932）で保存します。
setlocal
set "KOTOHA_ENTRY=%~f0"
call "%~dp0scripts\menu.bat"
exit /b %errorlevel%
