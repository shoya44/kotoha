"""学習素材を作る。img/dot の本体14枚（-blink は除く）を白い 1024 角に置き、
上半身の切り出しを足して、キャプション（.txt）を並べる。

    python -m tools.sprite_gen.lora.prepare

出来上がり: tools/sprite_gen/lora/dataset/10_kotoha/<名前>.png / <名前>-face.png と同名 .txt
（先頭の 10 は kohya の繰り返し回数。28枚 × 10 = 280 step / epoch）

キャプションは poses.py の pose 文を使う。髪・髪飾り・服はトリガー語 `kotoha` に吸わせるので書かない。
"""
import pathlib
import sys

from PIL import Image

from .. import poses as P

ROOT = pathlib.Path(__file__).resolve().parents[3]
DOT = ROOT / "img" / "dot"
OUT = pathlib.Path(__file__).resolve().parent / "dataset" / "10_kotoha"
SIZE = 1024
TRIGGER = "kotoha"
TAIL = "pixel art, chibi, 1girl, solo, white background"


def _canvas(im: Image.Image, fill=0.42) -> Image.Image:
    """白い正方形の真ん中に置く。fill は人物の高さの割合（既存の絵は 1024 中 400〜420px）。"""
    im = im.convert("RGBA").crop(im.getbbox())
    h = int(SIZE * fill)
    scale = h / im.height
    im = im.resize((max(1, round(im.width * scale)), h), Image.LANCZOS)
    bg = Image.new("RGBA", (SIZE, SIZE), "white")
    bg.paste(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2), im)
    return bg.convert("RGB")


def _face(im: Image.Image) -> Image.Image:
    """上半身。人物の上 55% の高さを一辺にして、横は中心で切る。顔の学習密度を上げるため。"""
    im = im.convert("RGBA").crop(im.getbbox())
    side = int(im.height * 0.55)
    x0 = max(0, (im.width - side) // 2)
    box = Image.new("RGBA", (side, side), "white")
    box.paste(im.crop((x0, 0, x0 + side, side)), (0, 0), im.crop((x0, 0, x0 + side, side)))
    return box.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*"):
        old.unlink()
    n = 0
    for name, spec in P.POSES.items():
        if spec.get("source") != "self":
            continue
        src = DOT / f"{name}.png"
        if not src.exists():
            print("skip (no image):", name)
            continue
        im = Image.open(src)
        cap_full = f"{TRIGGER}, {TAIL}, full body, {spec['pose']}"
        cap_face = f"{TRIGGER}, {TAIL}, upper body, portrait, {spec['pose']}"
        _canvas(im).save(OUT / f"{name}.png")
        (OUT / f"{name}.txt").write_text(cap_full, encoding="utf-8")
        _face(im).save(OUT / f"{name}-face.png")
        (OUT / f"{name}-face.txt").write_text(cap_face, encoding="utf-8")
        n += 2
    print(f"{n} images -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
