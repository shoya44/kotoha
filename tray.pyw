"""タスクトレイ常駐の入口。pythonw.exe から呼ぶと、コンソールが1枚も出ない。

スタートアップからは作業フォルダーが決まらないので、ここで場所を教える。
中身は kotoha/tray.py にある。
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from kotoha.tray import main  # noqa: E402

if __name__ == "__main__":
    main()
