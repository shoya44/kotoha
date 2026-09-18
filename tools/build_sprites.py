"""素材を、そのまま画面に出せる形に整える。開発のときだけ使う。

`img/dot/` に置かれた絵は、1枚ずつ大きさも足元の位置も違う。**そのまま切り替えると、
絵が変わるたびに位置が跳ねる。** ここで中身の外接矩形を測り、横は中央・下は床に
揃えて、同じキャンバスへ並べ直す。

元の絵はドット風だが、中に細かい階調を持っている（色数が1万を超える）。きれいな
格子になっていないので、整数倍に拡大しても崩れるだけになる。**一度だけ面積平均で
縮めて、その実寸を素材として持つ。**

`*-blink.png` は目を閉じた差分。**base と同じ置き方をする**（別々に測ると、
まばたきのたびに顔がずれる）。差分は後から置き足せる。置いてもう一度これを走らせれば、
`sprites.json` に載る。

    python -m tools.build_sprites

出るもの:
    kotoha/serve/static/sprite/full/*.png      ドットと会話画面のアバター
    kotoha/serve/static/sprite/face.png        会話の行やヘッダーに出る顔
    kotoha/serve/static/sprite/sprites.json    対応表。器はこれだけを見る
    kotoha/serve/static/icons/icon-*.png       ホーム画面のアイコン
"""

import json
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kotoha.mascot import png  # noqa: E402

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIR = BASE_DIR / "img" / "dot"
OUT_DIR = BASE_DIR / "kotoha" / "serve" / "static" / "sprite"

# 出す実寸。高さで揃える（横幅はキャンバスの比で決まる）。
#
# ⚠️ **画面の点と、絵の点は同じではない。** iPhoneは1ポイントを3画素で描くので、
# 140ポイントの枠に140pxの絵を置くと3倍に引き伸ばされて眠くなる。会話画面用は
# 元絵と同じ大きさ（縮めない）で出し、枠側で縮めてもらう。
# タスクトレイのドットは200。タスクバー（48）の4倍ほどで、机の隅に居る大きさ。
# 元絵が410なので、ここも縮める側のまま。
HEIGHTS = {"full": 200, "web": 0}     # web は 0 で「縮めない」の意
# 会話の行やヘッダーに並ぶ顔。いちばん大きい使い方で56ポイント＝168画素なので、
# これだけあれば常に縮める側になる（拡大した絵は眠くなる）。
FACE_SIZE = 192
# 顔を切り出す絵。こちらを向いているものを使う。
FACE_FROM = "talk"

# ホーム画面のアイコン。**要る大きさぴったりで出す。**
# 端末側で縮めさせると、ドット絵の輪郭が潰れて荒く見える。
ICON_DIR = "icons"
ICON_SIZES = (120, 152, 167, 180, 192, 256, 384)
# 透けたままだと、iOSでは黒く塗られる。会話画面と同じ下地を敷く。
ICON_BG = (0x17, 0x18, 0x1B)
# 角を丸められても頭が欠けないよう、まわりに空ける割合。
ICON_PADDING = 0.12

BLINK_SUFFIX = "-blink"
# キャンバスの余白。動きで1ドット上下させるぶんと、影のぶん。
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 6, 6, 4


def _sources():
    """(名前, 本体の絵, まばたきの絵) を、名前の順に返す。"""
    files = {p.stem: p for p in SOURCE_DIR.glob("*.png")}
    names = sorted(n for n in files if not n.endswith(BLINK_SUFFIX))
    for name in names:
        blink = files.get(name + BLINK_SUFFIX)
        yield name, files[name], blink


def _place(image, box, canvas_w, canvas_h, offset=None):
    """外接矩形を、横は中央・下は床に合わせて、同じ大きさのキャンバスへ移す。

    offset を渡すと、測り直さずにその置き方をそのまま使う（まばたき用）。
    """
    left, top, right, bottom = box
    if offset is None:
        dx = (canvas_w - (right - left + 1)) // 2 - left
        dy = (canvas_h - MARGIN_BOTTOM) - (bottom + 1)
        offset = (dx, dy)
    dx, dy = offset
    out = png.Image(canvas_w, canvas_h)
    for y in range(image.height):
        ty = y + dy
        if not (0 <= ty < canvas_h):
            continue
        row = image.px[y * image.width * 4:(y + 1) * image.width * 4]
        start = max(0, -dx)
        end = min(image.width, canvas_w - dx)
        if start >= end:
            continue
        out.px[(ty * canvas_w + start + dx) * 4:(ty * canvas_w + end + dx) * 4] = \
            row[start * 4:end * 4]
    return out, offset


def _shrink(image, out_w: int, out_h: int):
    """面積平均で縮める。**色は不透明さで重みを付ける。**

    そのまま平均すると、透明な部分の色（多くは黒）が縁に滲む。よくある失敗で、
    髪の先が黒ずむ形で出る。
    """
    out = png.Image(out_w, out_h)
    src, sw = image.px, image.width
    x_ratio = image.width / out_w
    y_ratio = image.height / out_h
    for y in range(out_h):
        y0, y1 = int(y * y_ratio), max(int((y + 1) * y_ratio), int(y * y_ratio) + 1)
        for x in range(out_w):
            x0, x1 = int(x * x_ratio), max(int((x + 1) * x_ratio), int(x * x_ratio) + 1)
            r = g = b = a = 0
            count = 0
            for sy in range(y0, min(y1, image.height)):
                row = sy * sw
                for sx in range(x0, min(x1, image.width)):
                    i = (row + sx) * 4
                    alpha = src[i + 3]
                    r += src[i] * alpha
                    g += src[i + 1] * alpha
                    b += src[i + 2] * alpha
                    a += alpha
                    count += 1
            i = (y * out_w + x) * 4
            if a:
                out.px[i] = min(255, r // a)
                out.px[i + 1] = min(255, g // a)
                out.px[i + 2] = min(255, b // a)
                out.px[i + 3] = a // count
    return out


def _fit(image, out_w: int, out_h: int):
    """その大きさにする。同じなら何もしない（縮めないぶんは元のまま渡す）。"""
    if (image.width, image.height) == (out_w, out_h):
        return image
    return _shrink(image, out_w, out_h)


def _grow(image, out_w: int, out_h: int):
    """大きくする。線の間を埋めず、点をそのまま並べる（ドット絵の輪郭を保つ）。"""
    out = png.Image(out_w, out_h)
    for y in range(out_h):
        sy = min(image.height - 1, y * image.height // out_h)
        for x in range(out_w):
            sx = min(image.width - 1, x * image.width // out_w)
            i, j = (y * out_w + x) * 4, (sy * image.width + sx) * 4
            out.px[i:i + 4] = image.px[j:j + 4]
    return out


def _face(canvas):
    """顔のあたりを正方形で切り出す。会話の行に並べる小さな顔になる。"""
    left, top, right, bottom = png.bounds(canvas)
    # 頭から肩までが入る大きさ。顔だけに寄せすぎると、32pxでは何の絵か分からない。
    side = min(int((bottom - top + 1) * 0.58), canvas.width, canvas.height)
    center = (left + right) // 2
    x0 = max(0, min(center - side // 2, canvas.width - side))
    y0 = max(0, min(top - 4, canvas.height - side))
    crop = png.Image(side, side)
    for y in range(side):
        sy = y0 + y
        if sy >= canvas.height:
            break
        start = (sy * canvas.width + x0) * 4
        crop.px[y * side * 4:(y + 1) * side * 4] = canvas.px[start:start + side * 4]
    if side >= FACE_SIZE:
        return _shrink(crop, FACE_SIZE, FACE_SIZE)
    # 元より大きくするときは、ぼかさずに点を並べる。
    return _grow(crop, FACE_SIZE, FACE_SIZE)


def _icon(canvas, size: int):
    """ホーム画面のアイコン1枚。頭と肩が入る正方形に切って、下地を敷く。"""
    left, top, right, bottom = png.bounds(canvas)
    side = min(int((bottom - top + 1) * 0.72), canvas.width, canvas.height)
    center = (left + right) // 2
    x0 = max(0, min(center - side // 2, canvas.width - side))
    y0 = max(0, min(top - side // 20, canvas.height - side))
    crop = png.Image(side, side)
    for y in range(side):
        start = ((y0 + y) * canvas.width + x0) * 4
        crop.px[y * side * 4:(y + 1) * side * 4] = canvas.px[start:start + side * 4]

    inner = max(1, int(size * (1 - ICON_PADDING * 2)))
    small = _shrink(crop, inner, inner) if side >= inner else _grow(crop, inner, inner)
    out = png.Image(size, size)
    red, green, blue = ICON_BG
    for i in range(size * size):
        out.px[i * 4:i * 4 + 4] = bytes((red, green, blue, 255))
    offset = (size - inner) // 2
    for y in range(inner):
        for x in range(inner):
            j = (y * inner + x) * 4
            alpha = small.px[j + 3]
            if not alpha:
                continue
            i = ((y + offset) * size + x + offset) * 4
            # 下地の上に重ねる。掛け算はここで1回だけ。
            for channel in range(3):
                over, under = small.px[j + channel], out.px[i + channel]
                out.px[i + channel] = (over * alpha + under * (255 - alpha)) // 255
    return out


def main() -> int:
    if not SOURCE_DIR.is_dir():
        print(f"素材が見つからない: {SOURCE_DIR}")
        return 1

    print(f"読む: {SOURCE_DIR}")
    loaded = []
    for name, path, blink_path in _sources():
        image = png.load(path)
        box = png.bounds(image)
        if box is None:
            print(f"  {name}: 中身が無い。飛ばす")
            continue
        blink = png.load(blink_path) if blink_path else None
        loaded.append((name, image, box, blink))
        size = f"{box[2] - box[0] + 1}x{box[3] - box[1] + 1}"
        print(f"  {name:12} {image.width}x{image.height} 中身={size}"
              f"{' +まばたき' if blink else ''}")

    if not loaded:
        print("読めるものが無い")
        return 1

    canvas_w = max(box[2] - box[0] + 1 for _, _, box, _ in loaded) + MARGIN_X * 2
    canvas_h = max(box[3] - box[1] + 1 for _, _, box, _ in loaded) + MARGIN_TOP + MARGIN_BOTTOM
    print(f"揃えるキャンバス: {canvas_w}x{canvas_h}（横は中央、下は床に合わせる）")

    for folder in HEIGHTS:
        (OUT_DIR / folder).mkdir(parents=True, exist_ok=True)

    sizes = {}
    for folder, height in HEIGHTS.items():
        if height:
            sizes[folder] = [max(1, round(canvas_w * height / canvas_h)), height]
        else:
            sizes[folder] = [canvas_w, canvas_h]      # 縮めない
    sizes["face"] = [FACE_SIZE, FACE_SIZE]

    sprites = {}
    for name, image, box, blink in loaded:
        placed, offset = _place(image, box, canvas_w, canvas_h)
        for folder in HEIGHTS:
            out_w, out_h = sizes[folder]
            png.save(OUT_DIR / folder / f"{name}.png", _fit(placed, out_w, out_h))
        if blink is not None:
            # **本体と同じ置き方**。測り直すと、まばたきのたびに顔がずれる。
            shifted, _ = _place(blink, box, canvas_w, canvas_h, offset)
            for folder in HEIGHTS:
                out_w, out_h = sizes[folder]
                png.save(OUT_DIR / folder / f"{name}{BLINK_SUFFIX}.png",
                         _fit(shifted, out_w, out_h))
        sprites[name] = {"blink": blink is not None}
        if name == FACE_FROM:
            png.save(OUT_DIR / "face.png", _face(placed))
            icons = OUT_DIR.parent / ICON_DIR
            icons.mkdir(parents=True, exist_ok=True)
            for size in ICON_SIZES:
                png.save(icons / f"icon-{size}.png", _icon(placed, size))
            print(f"  書いた: アイコン {len(ICON_SIZES)}枚")
        print(f"  書いた: {name}{'（まばたきあり）' if blink else ''}")

    manifest = {
        "generated": date.today().isoformat(),
        "source": "img/dot",
        "sizes": sizes,
        "face": FACE_FROM,
        "sprites": sprites,
    }
    (OUT_DIR / "sprites.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    blinks = sum(1 for s in sprites.values() if s["blink"])
    print(f"\n{len(sprites)}種類（まばたきあり {blinks}種類）")
    print(f"実寸: " + "、".join(f"{k} {v[0]}x{v[1]}" for k, v in sizes.items()))
    print(f"対応表: {OUT_DIR / 'sprites.json'}")
    print(f"アイコン: " + "、".join(f"{n}px" for n in ICON_SIZES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
