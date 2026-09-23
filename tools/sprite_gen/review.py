"""out/ の見比べ用。名前ごとに 本体・まばたき・口・コマ を1行に並べ、rows 行ずつの画像に分ける。
    python -m tools.sprite_gen.review [--only a,b] [--rows 11] [--cell 200] [--face]
--face は顔（中身の上 55%）だけを切って並べる。まばたきと口の差分を見るとき用。
--variants は Seed 違いの候補（final_<seed>.png）を並べる。どれを採るか決めるとき用。
"""
import argparse
from PIL import Image, ImageDraw
from . import poses as P
from .__main__ import OUT_DIR

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--rows", type=int, default=11); ap.add_argument("--cell", type=int, default=200); ap.add_argument("--face", action="store_true"); ap.add_argument("--variants", action="store_true")
    a = ap.parse_args()
    names = [n for n in (a.only.split(",") if a.only else P.POSES) if (OUT_DIR / n / f"{n}.png").exists()]
    c = a.cell
    for part in range(0, len(names), a.rows):
        chunk = names[part:part + a.rows]
        rows = []
        for n in chunk:
            if a.variants:
                # Seed 違いの候補（final_<seed>.png）を並べる。採否を決めるとき用
                files = sorted(f for f in (OUT_DIR / n).glob("final_*.png") if "_raw" not in f.name)
            else:
                files = [OUT_DIR / n / f"{n}.png", OUT_DIR / n / f"{n}-blink.png", OUT_DIR / n / f"{n}-mouth.png"]
                files += [OUT_DIR / n / f"{n}-{t}.png" for t in P.POSES[n].get("frames", {})]
            rows.append((n, [f for f in files if f.exists()]))
        width = c * (1 + max(len(f) for _, f in rows))
        out = Image.new("RGBA", (width, c * len(rows)), (200, 200, 200, 255))
        d = ImageDraw.Draw(out)
        for j, (n, files) in enumerate(rows):
            d.text((6, j * c + 6), n, fill=(30, 30, 30, 255))
            for i, f in enumerate(files):
                im = Image.open(f).convert("RGBA")
                if a.face:
                    l, t, r, b = im.getbbox() or (0, 0, im.width, im.height)
                    im = im.crop((l, t, r, t + int((b - t) * 0.55)))
                im.thumbnail((c - 8, c - 8))
                out.alpha_composite(im, ((i + 1) * c + 4, j * c + c - 4 - im.height))
        dest = OUT_DIR / f"review_{part // a.rows + 1}.png"; out.save(dest); print(dest, chunk)

if __name__ == "__main__":
    main()
