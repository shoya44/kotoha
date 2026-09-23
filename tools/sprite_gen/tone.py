"""髪と肌の色味を、元の絵（poses.TONE_REF）に寄せる。

LoRA で起こした絵は、元の絵より髪が明るく白っぽく出る（髪の明るさの中央値が
talk 0.875 に対して 0.91 前後。happy / coffee も同じ）。暖色で明るい画素（髪と肌）
だけを選び、明るさの平均と散らばりを元の絵に合わせる。

**画素ごとの分位で写すと粒が立つ**（試して捨てた）ので、1本の直線で写す。
彩度も写すと黄色みが増えたので、明るさと色相だけにしてある。
まばたきやコマは、**本体で測った値をそのまま使う。** 別々に測ると、窓の中だけ
色が変わって、目を閉じるたびに髪がちらつく。
"""

import colorsys
import statistics

from PIL import Image

# 暖色（髪・肌）の色相の幅と、明るさの下限。黒いパーカーや紫の髪飾りは入らない。
HUE = (5 / 360, 50 / 360)
MIN_LIGHT = 0.55


def _warm(im):
    """暖色で明るい画素の (位置, 色相, 明るさ, 彩度)。"""
    found = []
    for i, (r, g, b, a) in enumerate(im.getdata()):
        if a < 128:
            continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if HUE[0] <= h <= HUE[1] and l >= MIN_LIGHT:
            found.append((i, h, l, s))
    return found


def measure(png, ref_png):
    """本体を元の絵に寄せる値。(元の平均, 元の散らばり, 本体の平均, 本体の散らばり, 色相のずれ)。"""
    ref = _warm(Image.open(ref_png).convert("RGBA"))
    own = _warm(Image.open(png).convert("RGBA"))
    lights_ref, lights_own = [p[2] for p in ref], [p[2] for p in own]
    return (statistics.mean(lights_ref), statistics.pstdev(lights_ref),
            statistics.mean(lights_own), statistics.pstdev(lights_own) or 1.0,
            statistics.median(p[1] for p in ref) - statistics.median(p[1] for p in own))


def apply(png, values) -> None:
    """measure の値で写して、上書きする。"""
    mean_ref, sd_ref, mean_own, sd_own, hue_shift = values
    im = Image.open(png).convert("RGBA")
    data = list(im.getdata())
    for i, h, l, s in _warm(im):
        light = min(1.0, max(0.0, (l - mean_own) * sd_ref / sd_own + mean_ref))
        r, g, b = colorsys.hls_to_rgb(h + hue_shift, light, s)
        data[i] = (round(r * 255), round(g * 255), round(b * 255), data[i][3])
    im.putdata(data)
    im.save(png)
