@echo off
rem "kotoha.bat rescue" から呼ばれます。直接は実行しません。
rem
rem 出先から「ことは」を立て直せるように、このPCを家で1回だけ整えます。
rem 見張り番の登録（推奨）、OpenSSH（最後の手段）、スリープ対策（ふつう不要）の
rem 3つを、1つずつ確かめてから聞いて、はいと答えたぶんだけ変えます。
rem Windows の設定を触るので管理者権限が要ります。足りなければ自分で昇格します。
rem
rem このファイルは Shift_JIS（CP932）で保存します。UTF-8 だと cmd が行を読み飛ばします。
rem 管理者に昇格すると環境変数が引き継がれないので、合図は引数 elevated でも受け付ける。
if /i "%~1"=="elevated" goto :entry
if not defined KOTOHA_ENTRY goto :direct
:entry
setlocal
cd /d "%~dp0.."
title ことは - 出先から立て直す準備

net session >nul 2>&1
if not errorlevel 1 goto :elevated
echo.
echo  この準備には管理者権限が要ります。
echo  このあと「変更を許可しますか」と聞かれるので「はい」を押してください。
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs" >nul 2>&1
if errorlevel 1 (
    echo  昇格できませんでした。
    echo  kotoha-menu.bat を右クリックして「管理者として実行」を選び、
    echo  開いたメニューで 7 を押してください。
    pause
)
exit /b 0

:elevated
set "TASK=Kotoha Watchdog"
set "PYW=%~dp0..\.venv\Scripts\pythonw.exe"
set "TRAY=%~dp0tray.pyw"
set "DID1=していない"
set "DID2=していない"
set "DID3=していない"
set "SSHSTATE=不明"

cls
echo ==================================================
echo   ことは - 出先から立て直す準備
echo ==================================================
echo.
echo  出先から戻せるのは「PCは起きているのに、ことはだけが落ちた」ときです。
echo  PCが寝ている・電源が落ちているときは、外からは戻せません。
echo.
echo  これから3つを順に聞きます。「N」を押せば何も変えずに次へ進みます。
echo.
echo   1. 見張り番   5分ごとに見回り、落ちていれば上げ直す   ← これだけで十分
echo   2. OpenSSH    iPhoneのSSHアプリからPCに入って手で起こす ← 最後の手段
echo   3. スリープ   寝たPCを起こす準備                         ← ふつう不要
echo.
pause

rem --------------------------------------------------------------
rem  1. 見張り番（Watchdog）
rem --------------------------------------------------------------
:step1
cls
echo === 1. 見張り番（タスク スケジューラ「%TASK%」） ===
echo.
echo  ことはが自分で上げ直せるのは、自分から「入れ直す」と言ったときだけです。
echo  それ以外の落ち方をすると、誰かがPCの前で操作するまで止まったままです。
echo  そこで Windows のタスクに、5分ごとにトレイを起こす係を登録します。
echo.
echo  二重に起こしても大丈夫です。すでに居れば、あとから来たほうが黙って引き下がります。
echo.
if not exist "%PYW%" (
    echo   [注意] Python 環境が見つかりません: %PYW%
    echo          先に  kotoha.bat setup  を実行してください。この手順は飛ばします。
    pause
    goto :step2
)
schtasks /query /tn "%TASK%" >nul 2>&1
if errorlevel 1 (
    echo   いまの状態: 未登録
) else (
    echo   いまの状態: 登録ずみ（登録し直します。フォルダを移した後はこれで直ります）
)
echo.
choice /c YN /n /m "  見張り番を登録しますか？ [Y=はい / N=いいえ] "
if errorlevel 2 goto :step2
schtasks /create /f /it /tn "%TASK%" /sc minute /mo 5 ^
    /tr "\"%PYW%\" \"%TRAY%\"" >nul
if errorlevel 1 (
    echo   [失敗] タスクを登録できませんでした。
) else (
    echo   登録しました。ログオンしているあいだ、5分ごとに見回ります。
    set "DID1=した"
)
echo.
pause

rem --------------------------------------------------------------
rem  2. OpenSSH サーバー
rem --------------------------------------------------------------
:step2
cls
echo === 2. OpenSSH サーバー（最後の手段） ===
echo.
echo  見張り番でも戻らないとき、iPhone の SSH アプリから Tailscale 経由でPCに入り、
echo  手でトレイを起こせるようにします。
echo.
echo   [注意] PCへの入口を1つ開けます。Tailscale の中だけで使い、
echo          できればパスワードではなく鍵で入ってください。
echo.
for /f "delims=" %%S in ('powershell -NoProfile -Command "(Get-WindowsCapability -Online -Name OpenSSH.Server*).State" 2^>nul') do set "SSHSTATE=%%S"
echo   いまの状態: %SSHSTATE%   （Installed なら入っています）
echo.
choice /c YN /n /m "  OpenSSH サーバーを入れて有効にしますか？ [Y=はい / N=いいえ] "
if errorlevel 2 goto :step3
rem PowerShell は止めるよう言わないと、失敗しても 0 を返す。
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null; Set-Service -Name sshd -StartupType Automatic; Start-Service sshd } catch { exit 1 }"
if errorlevel 1 (
    echo   [失敗] OpenSSH サーバーを有効にできませんでした。
) else (
    echo   有効にしました。Windows と一緒に起動します。
    set "DID2=した"
)
echo.
pause

rem --------------------------------------------------------------
rem  3. スリープ対策
rem --------------------------------------------------------------
:step3
cls
echo === 3. スリープ対策（ふつう不要） ===
echo.
echo  PCが寝ると Tailscale は応えません。外から起こすには Wake-on-LAN が要りますが、
echo  その合図は家のLANの中からしか送れません（ルーター、スマートプラグなど）。
echo.
echo  楽なのは「寝かせない」ことです。.env に  KOTOHA_KEEP_AWAKE=true  と書けば、
echo  トレイが居るあいだPCは寝ません（画面は消えます。電源設定は触りません）。
echo  それで足りるなら、この手順は「N」で飛ばしてください。
echo.
echo  ここで変えるのは「高速スタートアップ」を切ることだけです。
echo  これが入っていると、シャットダウン後にネットワークカードが眠ったままになります。
echo.
echo   いまPCを起こせる機器:
powercfg -devicequery wake_armed
echo.
choice /c YN /n /m "  高速スタートアップを切りますか？ [Y=はい / N=いいえ] "
if errorlevel 2 goto :done
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" ^
    /v HiberbootEnabled /t REG_DWORD /d 0 /f >nul
if errorlevel 1 (
    echo   [失敗] 設定を変えられませんでした。
) else (
    echo   高速スタートアップを切りました。休止状態そのものは触っていません。
    set "DID3=した"
)
echo.
echo   残り2つは、ここからはできません:
echo.
echo     - ネットワークカードに起こす許可を出す。下の一覧から名前を選んで:
echo         powercfg -deviceenablewake "一覧にある正確な名前"
echo.
powercfg -devicequery wake_from_any
echo.
echo     - BIOS/UEFI で Wake-on-LAN を有効にする。
echo.
pause

rem --------------------------------------------------------------
:done
cls
echo ==================================================
echo   できあがり
echo ==================================================
echo.
echo   1. 見張り番の登録          %DID1%
echo   2. OpenSSH サーバー        %DID2%
echo   3. 高速スタートアップ停止  %DID3%
echo.
echo  この画面の内容は docs/07_運用と設定.md「出先から立て直す」にもあります。
echo.
echo --------------------------------------------------
echo   iPhone から戻すときは、上から順に
echo --------------------------------------------------
echo.
echo   (1) Tailscale アプリで、このPCが「Connected」か見る
echo        → 灰色（オフライン）なら寝ているか電源が落ちている。外からは戻せない。
echo.
echo   (2) 会話画面は開くが、様子がおかしい
echo        → 画面の歯車 → 「再起動する」。トレイが上げ直す。
echo.
echo   (3) 会話画面が開かない
echo        → 5分待って開き直す。見張り番がトレイを起こす。
echo.
echo   (4) 5分たっても開かない（OpenSSH を入れてあるとき）
echo        SSH アプリ（Termius など）で次の宛先につなぐ:
echo          ホスト:   Tailscale アプリに出ているこのPCの名前（%COMPUTERNAME%）
echo          ユーザー: %USERNAME%
echo        つながったら、この1行を打つ:
echo          schtasks /run /tn "%TASK%"
echo        これで自分のデスクトップ側にトレイが立ち、姿もアイコンも戻る。
echo        ※ kotoha.bat tray を直接打つと SSH 側で動くので、会話画面は使えても
echo          家のPCの画面には何も出ない。見張り番を走らせるほうを使うこと。
echo.
echo --------------------------------------------------
echo   元に戻すとき（管理者のコマンド プロンプトで）
echo --------------------------------------------------
echo   1. schtasks /delete /tn "%TASK%" /f
echo   2. powershell -Command "Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"
echo   3. reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled /t REG_DWORD /d 1 /f
echo.
pause
exit /b 0
:direct
echo  このファイルは直接実行しません。1つ上の  kotoha-menu.bat  の 7 か、kotoha.bat rescue  を使ってください。
pause
exit /b 1
