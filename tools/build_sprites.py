"""素材を、そのまま画面に出せる形に整える。開発のときだけ使う。

`img/dot/` に置かれた絵は、1枚ずつ大きさも足元の位置も違う。**そのまま切り替えると、
絵が変わるたびに位置が跳ねる。** ここで中身の外接矩形を測り、横は中央・下は床に
揃えて、同じキャンバスへ並べ直す。

元の絵はドット風だが、中に細かい階調を持っている（色数が1万を超える）。きれいな
格子になっていないので、整数倍に拡大しても崩れるだけになる。**一度だけ面積平均で
縮めて、その実寸を素材として持つ。**

⚠️ **元絵の縦横が揃っていなくてもよい。** 描き直した絵が3倍で書き出されることが
あり、混ざったまま並べると、そこだけ3倍の大きさで出てしまう（実際にそうなった）。
いちばん大きい絵を基準にして、それぞれの絵の縮尺を換算してから並べる。

`*-blink.png` は目を閉じた差分。**base と同じ置き方をする**（別々に測ると、
まばたきのたびに顔がずれる）。差分は後から置き足せる。置いてもう一度これを走らせれば、
`sprites.json` に載る。

    python -m tools.build_sprites

出るもの:
    kotoha/serve/static/sprite/full/*.png      デスクトップの姿
    kotoha/serve/static/sprite/web/*.png       会話画面の姿（iPhoneぶん大きい）
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
# web の420は、iPhoneが140ポイントの枠に使う画素数（1ポイント＝3画素）。
HEIGHTS = {"full": 200, "web": 420}
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
# 並べるときの余白。呼吸で詰めるぶんと、影のぶん（基準の絵の中での値）。
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 18, 18, 12


def _sources():
    """(名前, 本体, まばたき) の並びと、相手のいない差分の名前。

    名前を間違えて置かれた差分は、黙って無視すると気づけない。返して知らせる。
    """
    files = {p.stem: p for p in SOURCE_DIR.glob("*.png")}
    names = sorted(n for n in files if not n.endswith(BLINK_SUFFIX))
    orphans = sorted(n for n in files
                     if n.endswith(BLINK_SUFFIX) and n[:-len(BLINK_SUFFIX)] not in files)
    return [(n, files[n], files.get(n + BLINK_SUFFIX)) for n in names], orphans


def _paste(canvas, image, dx: int, dy: int):
    """左上を (dx, dy) に合わせて置く。はみ出したぶんは捨てる。"""
    for y in range(image.height):
        ty = y + dy
        if not (0 <= ty < canvas.height):
            continue
        start = max(0, -dx)
        end = min(image.width, canvas.width - dx)
        if start >= end:
            continue
        row = image.px[(y * image.width + start) * 4:(y * image.width + end) * 4]
        at = (ty * canvas.width + start + dx) * 4
        canvas.px[at:at + len(row)] = row


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


def _resize(image, out_w: int, out_h: int):
    """その大きさにする。同じなら何もしない。"""
    if (image.width, image.height) == (out_w, out_h):
        return image
    if out_w * out_h >= image.width * image.height:
        return _grow(image, out_w, out_h)
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


def _head(image, bounds, part: float):
    """頭を中心にした正方形の切り抜き。part は中身の高さに対する割合。"""
    left, top, right, bottom = bounds
    side = min(int((bottom - top + 1) * part), image.width, image.height)
    center = (left + right) // 2
    x0 = max(0, min(center - side // 2, image.width - side))
    y0 = max(0, min(top - side // 20, image.height - side))
    crop = png.Image(side, side)
    for y in range(side):
        sy = y0 + y
        if not (0 <= sy < image.height):
            continue
        start = (sy * image.width + x0) * 4
        crop.px[y * side * 4:(y + 1) * side * 4] = image.px[start:start + side * 4]
    return crop


def _icon(image, bounds, size: int):
    """ホーム画面のアイコン1枚。頭と肩が入る正方形に切って、下地を敷く。"""
    crop = _head(image, bounds, 0.72)
    inner = max(1, int(size * (1 - ICON_PADDING * 2)))
    small = _resize(crop, inner, inner)
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

    pairs, orphans = _sources()
    for name in orphans:
        # 絵文字はコンソールの文字集合（cp932）で出せない。
        print(f"注意: {name}.png は相手がいないので使われない"
              f"（{name[:-len(BLINK_SUFFIX)]}.png が無い）")

    print(f"読む: {SOURCE_DIR}")
    loaded = []
    for name, path, blink_path in pairs:
        image = png.load(path)
        bounds = png.bounds(image)
        if bounds is None:
            print(f"  {name}: 中身が無い。飛ばす")
            continue
        loaded.append((name, image, bounds, png.load(blink_path) if blink_path else None))

    if not loaded:
        print("読めるものが無い")
        return 1

    # **いちばん大きい絵を基準にする。** 混ざったまま並べると、そこだけ大きく出る。
    reference = max(max(i.width, b.width if b else 0) for _, i, _, b in loaded)
    sprites, plan = {}, {}
    for name, image, bounds, blink in loaded:
        scale = reference / image.width
        box = tuple(round(v * scale) for v in bounds)
        plan[name] = (image, scale, box, blink)
        marks = "+まばたき" if blink is not None else ""
        if scale != 1 or (blink is not None and blink.width != reference):
            marks += " 縮尺を換算"
        size = f"{bounds[2] - bounds[0] + 1}x{bounds[3] - bounds[1] + 1}"
        print(f"  {name:12} {image.width}x{image.height} 中身={size} {marks}")

    canvas_w = max(b[2] - b[0] + 1 for _, _, b, _ in plan.values()) + MARGIN_X * 2
    canvas_h = max(b[3] - b[1] + 1 for _, _, b, _ in plan.values()) + MARGIN_TOP + MARGIN_BOTTOM
    print(f"並べる広さ: {canvas_w}x{canvas_h}（基準 {reference}px。横は中央、下は床）")

    for folder in HEIGHTS:
        (OUT_DIR / folder).mkdir(parents=True, exist_ok=True)

    sizes = {}
    for folder, height in HEIGHTS.items():
        sizes[folder] = [max(1, round(canvas_w * height / canvas_h)), height]
    sizes["face"] = [FACE_SIZE, FACE_SIZE]

    def render(image, scale, place, out_w, out_h, ratio):
        """1枚を、出す大きさの中へ置く。**縮めるのはここで1回だけ。**"""
        canvas = png.Image(out_w, out_h)
        small = _resize(image,
                        max(1, round(image.width * scale * ratio)),
                        max(1, round(image.height * scale * ratio)))
        _paste(canvas, small, round(place[0] * ratio), round(place[1] * ratio))
        return canvas

    def placement(box):
        """基準の広さでの置き場所。**横は中身の中心、下は床。**

        まばたきも同じ決め方で置く。目を閉じたぶん上の端は変わるが、下端と
        中心は動かないので、切り替えても顔がずれない。描き直した差分（元絵の
        中での位置が違う）でも、これなら揃う。
        """
        left, top, right, bottom = box
        return ((canvas_w - (right - left + 1)) // 2 - left,
                (canvas_h - MARGIN_BOTTOM) - (bottom + 1))

    for name, (image, scale, box, blink) in plan.items():
        place = placement(box)
        blink_place = place
        if blink is not None:
            blink_scale = reference / blink.width
            blink_box = tuple(round(v * blink_scale) for v in png.bounds(blink))
            blink_place = placement(blink_box)
        for folder in HEIGHTS:
            out_w, out_h = sizes[folder]
            ratio = out_h / canvas_h
            png.save(OUT_DIR / folder / f"{name}.png",
                     render(image, scale, place, out_w, out_h, ratio))
            if blink is not None:
                png.save(OUT_DIR / folder / f"{name}{BLINK_SUFFIX}.png",
                         render(blink, blink_scale, blink_place, out_w, out_h, ratio))
        sprites[name] = {"blink": blink is not None}
        print(f"  書いた: {name}{'（まばたきあり）' if blink is not None else ''}")

    face_name = FACE_FROM if FACE_FROM in plan else next(iter(plan))
    face_image = plan[face_name][0]
    bounds = png.bounds(face_image)
    png.save(OUT_DIR / "face.png",
             _resize(_head(face_image, bounds, 0.58), FACE_SIZE, FACE_SIZE))
    icons = OUT_DIR.parent / ICON_DIR
    icons.mkdir(parents=True, exist_ok=True)
    for size in ICON_SIZES:
        png.save(icons / f"icon-{size}.png", _icon(face_image, bounds, size))
    print(f"  書いた: 顔とアイコン（{face_name} から）")

    manifest = {
        "generated": date.today().isoformat(),
        "source": "img/dot",
        "sizes": sizes,
        "face": face_name,
        "sprites": sprites,
    }
    (OUT_DIR / "sprites.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    blinks = sum(1 for s in sprites.values() if s["blink"])
    print(f"\n{len(sprites)}種類（まばたきあり {blinks}種類）")
    print("実寸: " + "、".join(f"{k} {v[0]}x{v[1]}" for k, v in sizes.items()))
    print(f"対応表: {OUT_DIR / 'sprites.json'}")
    print("アイコン: " + "、".join(f"{n}px" for n in ICON_SIZES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
