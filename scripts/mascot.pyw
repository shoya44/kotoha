"""ことはの姿を出す入口。pythonw.exe から呼ぶと、コンソールが1枚も出ない。

トレイからは作業フォルダーが決まらないので、ここで場所を教える。
中身は kotoha/mascot/__main__.py にある。

窓が無いので、main() に入る前の失敗はどこにも出ない。data/mascot.log に書いておく。
"""

import pathlib
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _leave_note(text: str) -> None:
    try:
        log = ROOT / "data" / "mascot.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        from kotoha.mascot.__main__ import main

        main()
    except SystemExit as error:
        if error.code not in (None, 0):
            _leave_note(f"姿を出せない: {error.code}")
        raise
    except Exception:
        _leave_note("姿を出せない:\n" + traceback.format_exc())
        raise
