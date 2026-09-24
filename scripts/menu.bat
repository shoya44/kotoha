@echo off
rem kotoha-menu.bat（または kotoha.bat menu）から呼ばれます。直接は実行しません。
rem このファイルは Shift_JIS（CP932）で保存します。
if not defined KOTOHA_ENTRY goto :direct
setlocal
cd /d "%~dp0.."
title ことは メニュー
set "KOTOHA=%~dp0..\kotoha.bat"
:menu
cls
echo ==============================
echo   ことは メニュー
echo ==============================
echo.
echo  ふだん
echo   1. 起動する（タスクトレイに常駐して会話画面を開く）
echo   2. 窓の中で動かす（ログが見える）
echo   3. いまの様子を見る
echo.
echo  最初に、または困ったとき
echo   4. Python 環境を作る（最初に1回）
echo   5. 設定を直す（.env）
echo   6. ログオン時の自動起動を切り替える
echo   7. 出先から立て直す準備（管理者権限。家で1回）
echo.
echo  そのほか
echo   8. 記憶の控えを取る
echo   9. できることの一覧
echo   0. 終わる
echo.
choice /c 1234567890 /n /m "  番号を押してください: "
if errorlevel 10 exit /b 0
if errorlevel 9 goto :help
if errorlevel 8 goto :backup
if errorlevel 7 goto :rescue
if errorlevel 6 goto :autostart
if errorlevel 5 goto :config
if errorlevel 4 goto :setup
if errorlevel 3 goto :status
if errorlevel 2 goto :window
if errorlevel 1 goto :tray
exit /b 1
:tray
call "%KOTOHA%" tray
pause
goto :menu
:window
start "ことは" "%ComSpec%" /d /c call "%KOTOHA%" console
goto :menu
:status
call "%KOTOHA%" status
pause
goto :menu
:setup
call "%KOTOHA%" setup
goto :menu
:config
call "%KOTOHA%" settings
goto :menu
:autostart
cls
echo === ログオン時の自動起動 ===
echo.
call "%KOTOHA%" autostart status
echo.
choice /c YNQ /n /m "  Y=入れる / N=切る / Q=そのまま戻る: "
if errorlevel 3 goto :menu
if errorlevel 2 (call "%KOTOHA%" autostart off) else (call "%KOTOHA%" autostart on)
pause
goto :menu
:rescue
call "%KOTOHA%" rescue
goto :menu
:backup
call "%KOTOHA%" backup
pause
goto :menu
:help
call "%KOTOHA%" help
pause
goto :menu
:direct
echo  このファイルは直接実行しません。1つ上の  kotoha-menu.bat  を使ってください。
pause
exit /b 1
