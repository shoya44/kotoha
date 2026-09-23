"""PNGの読み書き。標準の `zlib` だけで足りる。

Pillowを入れれば3行で済むが、依存3つで動いている構成に10MB超を足す理由が
ない（`tray.py` が pystray を断ったのと同じ判断）。加えて、**GDI+で読むと
プリマルチプライ済みアルファへの変換が別に要る**。自分で読めば、その場で
掛けて終わりになる。

読めるのは8bitの非インターレースだけ。素材はこちらで用意するので、それ以上は
読まない。読めないものは黙って壊れた絵を返さず、その場で落とす。

書くほうは `tools/build_sprites.py` が使う。読みと書きを同じ場所に置いておくと、
片方だけ直して食い違うことがない。
"""

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 対応する色の種類。数字はPNGの仕様のもの。
GRAY, RGB, PALETTE, GRAY_ALPHA, RGBA = 0, 2, 3, 4, 6
CHANNELS = {GRAY: 1, RGB: 3, PALETTE: 1, GRAY_ALPHA: 2, RGBA: 4}


class PngError(ValueError):
    """読めないPNG。黙って進むと、崩れた絵が画面に出る。"""


class Image:
    """8bit RGBAの絵。`px` は行ごとに詰めた bytearray。"""

    __slots__ = ("width", "height", "px")

    def __init__(self, width: int, height: int, px=None):
        self.width = width
        self.height = height
        self.px = px if px is not None else bytearray(width * height * 4)

    def pixel(self, x: int, y: int):
        i = (y * self.width + x) * 4
        return self.px[i:i + 4]

    def alpha(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0
        return self.px[(y * self.width + x) * 4 + 3]


def _chunks(data: bytes):
    if data[:8] != SIGNATURE:
        raise PngError("PNGではない")
    i = 8
    while i + 8 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        yield kind, data[i + 8:i + 8 + length]
        i += 12 + length


def _undo_filters(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """行ごとの予測を戻す。PNGはここが本体といっていい。"""
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if kind == 0:
            pass
        elif kind == 2:                      # 上と同じだけずらす。よく出るので先に。
            for x in range(stride):
                line[x] = (line[x] + previous[x]) & 255
        elif kind == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 255
        elif kind == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + previous[x]) >> 1)) & 255
        elif kind == 4:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                up = previous[x]
                corner = previous[x - channels] if x >= channels else 0
                guess = left + up - corner
                dl, du, dc = abs(guess - left), abs(guess - up), abs(guess - corner)
                if dl <= du and dl <= dc:
                    near = left
                elif du <= dc:
                    near = up
                else:
                    near = corner
                line[x] = (line[x] + near) & 255
        else:
            raise PngError(f"知らない行の予測: {kind}")
        out[y * stride:(y + 1) * stride] = line
        previous = line
    return out


def load(path) -> Image:
    """PNGを1枚読む。どんな色の種類でも、返すのは必ず8bit RGBA。"""
    data = path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read()
    width = height = depth = color = 0
    palette = alpha_table = None
    body = bytearray()
    for kind, chunk in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8:
                raise PngError(f"8bit以外は読まない: {depth}bit")
            if interlace:
                raise PngError("インターレースは読まない")
            if color not in CHANNELS:
                raise PngError(f"知らない色の種類: {color}")
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            alpha_table = chunk
        elif kind == b"IDAT":
            body += chunk
        elif kind == b"IEND":
            break
    if not width or not height:
        raise PngError("大きさが読めない")
    channels = CHANNELS[color]
    flat = _undo_filters(zlib.decompress(bytes(body)), width, height, channels)

    if color == RGBA:
        return Image(width, height, flat)

    px = bytearray(width * height * 4)
    count = width * height
    if color == PALETTE:
        if palette is None:
            raise PngError("パレットが無い")
        for i in range(count):
            index = flat[i]
            px[i * 4:i * 4 + 3] = palette[index * 3:index * 3 + 3]
            px[i * 4 + 3] = alpha_table[index] if alpha_table and index < len(alpha_table) else 255
    elif color == RGB:
        for i in range(count):
            px[i * 4:i * 4 + 3] = flat[i * 3:i * 3 + 3]
            px[i * 4 + 3] = 255
    elif color == GRAY:
        for i in range(count):
            value = flat[i]
            px[i * 4:i * 4 + 3] = bytes((value, value, value))
            px[i * 4 + 3] = 255
    else:                                     # GRAY_ALPHA
        for i in range(count):
            value = flat[i * 2]
            px[i * 4:i * 4 + 3] = bytes((value, value, value))
            px[i * 4 + 3] = flat[i * 2 + 1]
    return Image(width, height, px)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def save(path, image: Image) -> None:
    """8bit RGBAで書く。行の予測は使わない（素材の枚数なら大きさは問題にならない）。"""
    stride = image.width * 4
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)
        raw += image.px[y * stride:(y + 1) * stride]
    head = struct.pack(">IIBBBBB", image.width, image.height, 8, RGBA, 0, 0, 0)
    body = zlib.compress(bytes(raw), 9)
    out = SIGNATURE + _chunk(b"IHDR", head) + _chunk(b"IDAT", body) + _chunk(b"IEND", b"")
    if hasattr(path, "write_bytes"):
        path.write_bytes(out)
    else:
        with open(path, "wb") as handle:
            handle.write(out)


def bounds(image: Image, threshold: int = 8):
    """中身（不透明な部分）の外接矩形。素材の足元を揃えるのに使う。

    見つからなければ None。返すのは (左, 上, 右, 下) で、右下も含む。
    """
    left, top = image.width, image.height
    right = bottom = -1
    px, width = image.px, image.width
    for y in range(image.height):
        row = y * width * 4
        for x in range(width):
            if px[row + x * 4 + 3] > threshold:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                bottom = y
    if right < 0:
        return None
    return left, top, right, bottom
