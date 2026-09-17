""".envの編集補助。アプリ設定が壊れていてもエディターを開ける。"""

from pathlib import Path
import subprocess
import sys

BASE_DIR = Path(__file__).resolve().parent.parent


def read_env(path):
    values = {}
    if not path.exists():
        return values
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"設定エラー: {path.name} の{number}行目は KEY=VALUE で記載してください。")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def ensure_settings(base_dir=BASE_DIR):
    """既存行・キー・値を変えず、未記載の項目だけ説明付きで追記する。"""
    target = base_dir / ".env"
    template = base_dir / ".env.example"
    if not target.exists():
        # テンプレートに秘密情報は含めない。
        with target.open("xb") as stream:
            stream.write(template.read_bytes())
        return target
    # 壊れた数値や書式もここでは検査せず、先に編集できるようにする。
    existing = {
        line.split("=", 1)[0].strip()
        for line in target.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    comments, additions = [], []
    for line in template.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#"):
            comments.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key not in existing:
            additions.extend(comments)
            additions.append(line)
        comments = []
    if additions:
        with target.open("ab") as stream:
            stream.write(("\n\n# 追加の設定項目\n" + "\n".join(additions) + "\n").encode("utf-8"))
    return target


def main():
    try:
        target = ensure_settings()
        subprocess.Popen(["notepad.exe", str(target)])
        input("設定ファイルを保存してから、この画面でEnterを押してください: ")
        result = subprocess.run([sys.executable, "-m", "kotoha.config"], cwd=BASE_DIR)
        return result.returncode
    except OSError:
        print("設定ファイルまたはメモ帳を開けませんでした。アクセス権を確認してください。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
