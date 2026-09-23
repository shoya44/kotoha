"""学習した LoRA を epoch ごとに見比べる。ComfyUI で t2i を回し、1枚の並び画像にまとめる。

    python -m tools.sprite_gen.lora.compare              # Models/Lora/kotoha/*.safetensors 全部
    python -m tools.sprite_gen.lora.compare --only 3,6   # epoch を絞る
    python -m tools.sprite_gen.lora.compare --strength 0.8

行 = LoRA（先頭は LoRA 無し）、列 = ポーズ。Seed は固定。出力は tools/sprite_gen/lora/compare.png。
生成は全部 ComfyUI 側。ここでは並べるだけ。
"""
import argparse
import pathlib
import sys

from PIL import Image, ImageDraw

from .. import poses as P
from ..__main__ import _fetch, _run

LORA_DIR = pathlib.Path(r"C:\StabilityMatrix\Data\Models\Lora\kotoha")
HERE = pathlib.Path(__file__).resolve().parent
CKPT = "animagineXL40_v4Opt.safetensors"
SEED = P.SEED
TRIGGER = "kotoha"
TAIL = "pixel art, chibi, 1girl, solo, plain white background, full body, clean thick outlines, flat pastel colors"
NEGATIVE = P.NEGATIVE
SAMPLES = {
    "wave":  "standing, waving one hand, cheerful",
    "book":  "sitting, reading a book held in both hands",
    "walk":  "walking to the right, side view, mid step",
    "yawn":  "stretching both arms up above head, eyes closed, big yawn",
}
CELL = 256


def _workflow(lora: str | None, pose: str, strength: float, prefix: str) -> dict:
    model, clip = ["1", 0], ["1", 1]
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["7", 0]}},
    }
    if lora:
        wf["9"] = {"class_type": "LoraLoader", "inputs": {"lora_name": lora, "strength_model": strength,
                                                          "strength_clip": strength, "model": model, "clip": clip}}
        model, clip = ["9", 0], ["9", 1]
    wf["2"] = {"class_type": "CLIPTextEncode", "inputs": {"text": f"{TRIGGER}, {TAIL}, {pose}", "clip": clip}}
    wf["3"] = {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": clip}}
    wf["5"] = {"class_type": "KSampler", "inputs": {"seed": SEED, "steps": 28, "cfg": 6.0, "sampler_name": "euler_ancestral",
                                                   "scheduler": "normal", "denoise": 1.0, "model": model,
                                                   "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}}
    return wf


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="epoch 番号をカンマ区切り（例 3,6）。final は 0")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--no-base", action="store_true", help="LoRA 無しの行を省く")
    a = ap.parse_args(argv)

    loras = sorted(LORA_DIR.glob("kotoha*.safetensors"))
    if a.only:
        want = {int(x) for x in a.only.split(",")}
        loras = [p for p in loras if (int(p.stem.split("-")[-1]) if "-" in p.stem else 0) in want]
    rows = ([] if a.no_base else [None]) + ["kotoha\\" + p.name for p in loras]  # ComfyUI は区切りをバックスラッシュで返す
    if not rows:
        sys.exit(f"LoRA が無い: {LORA_DIR}")

    work = HERE / "compare_work"
    sheet = Image.new("RGB", (CELL * len(SAMPLES) + 120, CELL * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)
    for r, lora in enumerate(rows):
        label = "base" if lora is None else pathlib.Path(lora).stem.replace("kotoha", "") or "final"
        draw.text((4, r * CELL + 4), label, fill="black")
        for c, (name, pose) in enumerate(SAMPLES.items()):
            prefix = f"kotoha_lora_cmp/{label.strip('-')}_{name}"
            out = _run(_workflow(lora, pose, a.strength, prefix))
            img = next(iter(out.values()))[0]
            png = _fetch(img, work / f"{label.strip('-')}_{name}.png")
            sheet.paste(Image.open(png).convert("RGB").resize((CELL, CELL), Image.LANCZOS), (120 + c * CELL, r * CELL))
            print(label, name, "ok")
    dest = HERE / "compare.png"
    sheet.save(dest)
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
