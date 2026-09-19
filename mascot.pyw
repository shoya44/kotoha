"""ことはの姿を出す入口。pythonw.exe から呼ぶと、コンソールが1枚も出ない。

トレイからは作業フォルダーが決まらないので、ここで場所を教える。
中身は kotoha/mascot/__main__.py にある。
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from kotoha.mascot.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
