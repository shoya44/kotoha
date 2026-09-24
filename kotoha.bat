@echo off
rem ことはの入口。叩くのはこの1枚だけで、あとは引数で分かれます。
rem   ダブルクリック / 引数なし  → タスクトレイに常駐して会話画面を開く（ふだんはこれ）
rem   kotoha.bat console         → この窓の中で動かす。ログが見える
rem   kotoha.bat help            → できることの一覧
rem 引数を打ちたくなければ、隣の kotoha-menu.bat をダブルクリックすると番号で選べます。
rem 長い処理は scripts\ にありますが、そちらは直接実行しません。
rem このファイルは Shift_JIS（CP932）で保存します。UTF-8 だと cmd が行を読み飛ばします。
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
rem scripts\ の部品に「入口から来た」と伝える合図。直接叩かれたときは止まる。
set "KOTOHA_ENTRY=%~f0"

rem ダブルクリックだと終わった瞬間に窓が閉じるので、最後に待って読めるようにする。
rem コンソールから呼ばれたときは待たない。
set "CLICKED="
echo %cmdcmdline% | find /i "%~f0" >nul && set "CLICKED=1"

rem setup と rescue は Python 環境ができる前でも動く必要がある。
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

rem それ以外はそのまま CLI へ: status, backup, memory, note, ...
"%PY%" -m kotoha %*
exit /b %errorlevel%

rem --------------------------------------------------------------
:tray
rem トレイを起こす（すでに居れば画面だけ開く）。トレイ本体は pythonw で動くので、
rem この窓は伝言を出すだけ。
title ことは
"%PY%" -m kotoha tray
set "tray_result=%errorlevel%"
if not "%tray_result%"=="0" (
    echo.
    echo  トレイで起こせませんでした。上のメッセージを確かめるか、
    echo  kotoha.bat console  で窓の中で動かしてログを見てください。
    if defined CLICKED pause
    exit /b %tray_result%
)
if defined CLICKED timeout /t 3 >nul 2>&1
exit /b 0

:console
title ことは（窓）
:loop
"%PY%" -m kotoha.launcher
rem 42 は本体からの「入れ直して」。errorlevel は「以上」で見るので大きい方から。
if errorlevel 42 (
    echo.
    echo  ことはを入れ直します...
    echo.
    goto :loop
)
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo  起こせませんでした。上のメッセージを確かめてください。
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
title ことは 設定
"%PY%" -m kotoha.settings
set "settings_result=%errorlevel%"
if not "%settings_result%"=="0" echo  設定の確認で引っかかりました。もう一度  kotoha.bat settings  で直してください。
pause
exit /b %settings_result%

rem --------------------------------------------------------------
:nopython
echo  Python 環境がまだありません。先に  kotoha.bat setup  が要ります。
rem ダブルクリックのときはその場で作れるように聞く。コンソールから呼ばれたときは静かに返す。
if not defined CLICKED exit /b 1
echo.
choice /c YN /n /m "  いまここで作りますか？ [Y=はい / N=いいえ] "
if errorlevel 2 exit /b 1
call "%~dp0scripts\setup.bat"
exit /b %errorlevel%

:help
echo.
echo  ダブルクリックするのは kotoha.bat（起動）と kotoha-menu.bat（それ以外）の2枚だけです。
echo  コマンドで使うなら、kotoha.bat の後ろに言葉を付けます。
echo.
echo   kotoha.bat               タスクトレイに常駐して会話画面を開く。
echo                            ダブルクリックはこれ。"kotoha.bat tray" と同じ
echo   kotoha.bat console       窓の中で動かす。ログが見える
echo   kotoha.bat setup         Python 環境を作る。最初に1回
echo   kotoha.bat settings      .env を対話で直して確かめる
echo   kotoha.bat autostart on  ログオン時にトレイを上げる（off / status も）
echo   kotoha.bat menu          よく使うものを並べた画面
echo   kotoha.bat rescue        出先から立て直す準備（管理者権限。家で1回）
echo.
echo   kotoha.bat start         コンソールで話す
echo   kotoha.bat status        いまの様子
echo   kotoha.bat memory        覚えていること
echo   kotoha.bat remind        頼まれて預かっていること
echo   kotoha.bat note "..."    自分のことをひとつ覚えさせる
echo   kotoha.bat backup        記憶の控えを取る
echo.
echo  scripts\ の中の bat は kotoha.bat から呼ばれる部品です。直接は実行しません。
echo.
exit /b 0
