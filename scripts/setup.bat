@echo off
rem "kotoha.bat setup" から呼ばれます。直接は実行しません。
rem .venv ができる前に動く唯一の部品です（これが .venv を作るので）。
rem このファイルは Shift_JIS（CP932）で保存します。
if not defined KOTOHA_ENTRY goto :direct
setlocal
cd /d "%~dp0.."
title ことは セットアップ
set "PYTHONUTF8=1"
if exist ".venv\Scripts\python.exe" goto :install
where py >nul 2>&1
if errorlevel 1 (
    echo  先に Python 3.10 以上を、py ランチャー付きで入れてください。
    goto :failed
)
py -3 -m venv .venv
if errorlevel 1 goto :failed
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo  Python 環境が使えないか、3.10 より古いです。
    echo  .venv を作った Python を確かめてください。
    goto :failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
call "%~dp0..\kotoha.bat" init
if errorlevel 1 goto :failed
echo.
echo  セットアップできました。次は  kotoha.bat settings  で .env を埋めて、kotoha.bat をダブルクリックしてください。
pause
exit /b 0
:failed
echo.
echo  セットアップに失敗しました。上のメッセージを確かめてください。
pause
exit /b 1
:direct
echo  このファイルは直接実行しません。1つ上の  kotoha.bat setup  を使ってください。
pause
exit /b 1
