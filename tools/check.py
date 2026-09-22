"""CI と同じ検査を、手元で1コマンドで。**押す前に、押したあとと同じものを見る。**

    python -m tools.check          # pyflakes → テスト
    python -m tools.check 20       # 遅いテスト20件も見せる

CI（.github/workflows/tests.yml）が回すのはこの2つだけなので、ここが通れば
向こうも通る。逆に、ここを飛ばして押すと、赤くなってから直すことになる。
"""

import subprocess
import sys


def main(argv) -> int:
    steps = [
        ("名前の無さ（pyflakes）", [sys.executable, "-m", "pyflakes", "kotoha", "tools"]),
        ("テスト", [sys.executable, "-m", "tools.time_tests", *argv[:1]]),
    ]
    for label, command in steps:
        print(f"== {label}")
        code = subprocess.call(command)
        if code:
            print(f"-- {label} で止まった（終了コード {code}）")
            return code
    print("== 全部通った")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
