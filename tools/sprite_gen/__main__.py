"""ことはの絵を ComfyUI で機械的に作る。

    python -m tools.sprite_gen                  # poses.py の全部を out/ に作る
    python -m tools.sprite_gen --only happy,nap # 名前を絞る
    python -m tools.sprite_gen --variants 4     # Seed を 4 つずらして候補を出す（選ぶ用）
    python -m tools.sprite_gen --accept a,b     # 出来のよい絵を台帳（accepted.json）に載せる
    python -m tools.sprite_gen --retry a,b      # だめな絵だけ Seed をずらして作り直す（台帳から外す）
    python -m tools.sprite_gen --apply          # 台帳にある絵を img/dot/ に置く（旧絵は img/dot/old/ へ）
    python -m tools.sprite_gen --sheet          # out/ の見比べ1枚を out/sheet.png に書く

生成・白抜きは全部 ComfyUI 側（tools/sprite_gen/workflow_restyle.json）。ここでやるのは、
元絵を白い 1024 角に置くこと、プロンプトの穴埋め、投げて受け取ること、だけ。

流れ（poses.py の source を参照）:
  self:  img/dot/<name>.png → 白キャンバス → 筆致そろえ(Denoise 0.6, pixel art) → 白抜き
  他:    描き直し済みの <source>（out/<source>/final_*_raw.png、無ければ img/dot/<source>.png）
         → ポーズ起こし(poses.POSE_DENOISE。LoRA ありは 1.0, pixel art) → 白抜き。**1段だけ。**
コマ（frames）は、本体を元に窓の中だけ描き直した1枚ずつ（まばたきと同じ作り）。
まばたきは、出来た本体を元に目の窓だけ塗り直した1枚（窓の外は本体そのもの）。
"""

import argparse
import json
import pathlib
import shutil
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

from . import poses as P
from . import tone

HOST = "http://127.0.0.1:8188"
HERE = pathlib.Path(__file__).resolve().parent
BASE_DIR = HERE.parent.parent
DOT_DIR = BASE_DIR / "img" / "dot"
OUT_DIR = HERE / "out"
WORKFLOW = HERE / "workflow_restyle.json"
# 採用の台帳。{名前: {"seed": 使った Seed}}。**ここにある絵だけ img/dot/ に置く。**
# 全部いっぺんに揃えなくてよい。良いものから載せ、だめなものだけ作り直す。
ACCEPTED = HERE / "accepted.json"


def _ledger() -> dict:
    return json.loads(ACCEPTED.read_text(encoding="utf-8")) if ACCEPTED.exists() else {}


def _save_ledger(ledger: dict) -> None:
    ACCEPTED.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")


CANVAS = 1024
# ドットに落とすときの一辺。既存の絵は 400〜420px 相当。
PIXEL_SIZE = 400
# 絵柄の参照に使うモデル（ComfyUI の models/ipadapter と clip_vision にあるもの）。
IPADAPTER_FILE = "ip-adapter-plus_sdxl_vit-h.safetensors"
CLIP_VISION_FILE = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"


# ---- ComfyUI とのやりとり ----------------------------------------------------

def _api(path, data=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def _upload(path: pathlib.Path) -> str:
    """ComfyUI の input に置く。**名前は置き場所ごとに一意にする**（out/<名前>/work/ref.png が
    別のポーズと同じ名前で上書きされないように。2本並べて回すとここで混ざる）。"""
    unique = "_".join(path.resolve().parts[-3:]).replace(" ", "_")
    boundary = "----kotoha-sprite-gen"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{unique}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + path.read_bytes() + (
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
        f"\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(HOST + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return json.load(urllib.request.urlopen(req, timeout=60))["name"]


def _run(workflow: dict) -> dict:
    """投げて、終わるまで待つ。戻りは {node_id: [image, ...]}。"""
    pid = _api("/prompt", {"prompt": workflow})["prompt_id"]
    while True:
        hist = _api(f"/history/{pid}")
        if pid in hist:
            h = hist[pid]
            if h["status"].get("status_str") == "error":
                sys.exit("ComfyUI error: " + json.dumps(h["status"], ensure_ascii=False)[:1200])
            return {n: o["images"] for n, o in h["outputs"].items() if "images" in o}
        time.sleep(1.0)


def _fetch(image: dict, dest: pathlib.Path) -> pathlib.Path:
    q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image["subfolder"],
                                "type": image["type"]})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(HOST + "/view?" + q, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def _workflow(positive: str, ref: str, seed: int, denoise: float, prefix: str,
              pixelate: bool = False, mask: str = None, style: str = None,
              style_weight: float = None, style_type: str = None) -> dict:
    """土台の JSON に穴埋めをして、必要なら節を差し込む。

    pixelate: 元絵を一度ドットに落としてから渡す（線画の1段目をドット調に寄せるため）。
              nearest で 400px に縮めて 1024 に戻す。**Animagine は元絵の質感に強く従う**ので、
              ここでドットにしておくと2段目がドット絵として整える。
    mask:     目のまわりだけ塗り直す（まばたき差分）。窓の外は元絵のまま残る。
    style:    絵柄の参照（IP-Adapter）。**ポーズを変えても筆致を talk に留める**ための節。
              これが無いと、新しいポーズは線が太い線画に流れた（第1弾の新規8枚）。
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    text = (text.replace("%POSITIVE%", json.dumps(positive)[1:-1])
                .replace("%NEGATIVE%", json.dumps(P.NEGATIVE)[1:-1])
                .replace("%REF%", ref)
                .replace('"%SEED%"', str(seed))
                .replace('"%DENOISE%"', str(denoise))
                .replace("%PREFIX%", prefix))
    wf = json.loads(text)
    if P.LORA:
        # 本人 LoRA。土台の直後に挟み、model と clip を全部こちらに付け替える
        wf["30"] = {"class_type": "LoraLoader", "inputs": {"lora_name": P.LORA, "strength_model": P.LORA_STRENGTH,
                                                           "strength_clip": P.LORA_STRENGTH, "model": ["1", 0], "clip": ["1", 1]}}
        for n in ("2", "3"):
            wf[n]["inputs"]["clip"] = ["30", 1]
        wf["5"]["inputs"]["model"] = ["30", 0]
    if pixelate:
        wf["12"] = {"class_type": "ImageScale", "inputs": {"image": ["9", 0], "upscale_method": "nearest-exact",
                                                            "width": PIXEL_SIZE, "height": PIXEL_SIZE, "crop": "disabled"}}
        wf["13"] = {"class_type": "ImageScale", "inputs": {"image": ["12", 0], "upscale_method": "nearest-exact",
                                                            "width": CANVAS, "height": CANVAS, "crop": "disabled"}}
        wf["4"]["inputs"]["pixels"] = ["13", 0]
    if style:
        wf["20"] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": IPADAPTER_FILE}}
        wf["21"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": CLIP_VISION_FILE}}
        wf["22"] = {"class_type": "LoadImage", "inputs": {"image": style}}
        wf["23"] = {"class_type": "IPAdapterAdvanced", "inputs": {
            "model": wf["5"]["inputs"]["model"], "ipadapter": ["20", 0], "image": ["22", 0], "clip_vision": ["21", 0],
            "weight": style_weight if style_weight is not None else P.STYLE_WEIGHT,
            "weight_type": style_type or P.STYLE_TYPE, "combine_embeds": "concat",
            "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"}}
        wf["5"]["inputs"]["model"] = ["23", 0]
    if mask:
        wf["14"] = {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}}
        wf["15"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["4", 0], "mask": ["14", 0]}}
        wf["5"]["inputs"]["latent_image"] = ["15", 0]
    return wf


# ---- 元絵の用意 --------------------------------------------------------------

def _canvas(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """透明PNGを白い 1024 角に置く。高さ 1000 に合わせ、足元を下に寄せる。"""
    im = Image.open(src).convert("RGBA")
    im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
    scale = min(1000 / im.height, 1000 / im.width)
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    bg = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 255))
    bg.alpha_composite(im, ((CANVAS - im.width) // 2, CANVAS - im.height - 12))
    dest.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(dest)
    return dest


def _eye_mask(cut_png: pathlib.Path, dest: pathlib.Path, window=(0.10, 0.90, 0.12, 0.50)) -> pathlib.Path:
    """白抜き済みの絵から、中身の高さ 12〜50% に目の窓を作る（build_sprites の EYE_WINDOW と同じ）。"""
    im = Image.open(cut_png).convert("RGBA")
    left, top, right, bottom = im.getbbox()
    w, h = right - left, bottom - top
    dest.parent.mkdir(parents=True, exist_ok=True)
    mask = Image.new("L", im.size, 0)
    box = (round(left + w * window[0]), round(top + h * window[2]),
           round(left + w * window[1]), round(top + h * window[3]))
    mask.paste(255, box)
    mask.save(dest)
    return dest


def _keep_main_island(png: pathlib.Path) -> None:
    """白抜き後、いちばん大きな塊だけ残す。

    プロンプトに「三日月の髪飾り」があると、背景に三日月を1つ浮かせて描くことがある。
    体につながっていないものは落とす（吹き出しの点や汗も落ちるが、絵の同一性のほうを取る）。
    """
    from PIL import ImageDraw
    im = Image.open(png).convert("RGBA")
    alpha = im.getchannel("A").point(lambda v: 255 if v > 8 else 0)
    label = alpha.copy()
    px = label.load()
    islands = []  # (画素数, 塗った値)
    value = 254
    for y in range(0, label.height, 4):
        for x in range(0, label.width, 4):
            if px[x, y] == 255:
                ImageDraw.floodfill(label, (x, y), value)
                islands.append((label.histogram()[value], value))
                value -= 1
                if value < 128:
                    break
    if len(islands) <= 1:
        return
    keep = max(islands)[1]
    mask = label.point(lambda v, keep=keep: 255 if v == keep else 0)
    # 縮小した縁の半透明も残したいので、元のアルファに塊のマスクを掛ける
    new_alpha = Image.composite(im.getchannel("A"), Image.new("L", im.size, 0), mask)
    im.putalpha(new_alpha)
    im.save(png)


# ---- 1枚ぶん -----------------------------------------------------------------

def _style_ref() -> pathlib.Path:
    """絵柄の参照。描き直した talk（白抜き前）があればそれ、無ければ img/dot の talk を白に置く。"""
    done = sorted((OUT_DIR / P.STYLE_REF).glob("final_*_raw.png"))
    if done:
        return done[-1]
    return _canvas(DOT_DIR / f"{P.STYLE_REF}.png", OUT_DIR / "_style" / "ref.png")


def _stage(name: str, pose: str, ref_png: pathlib.Path, seed: int, denoise: float,
           style: str, tag: str, pixelate: bool = False, mask_png: pathlib.Path = None,
           style_weight: float = None, style_type: str = None, drop: str = "") -> tuple[pathlib.Path, pathlib.Path]:
    """1段回す。戻りは (白抜き済み, 白抜き前) のパス。drop は共通プロンプトから外す言葉。"""
    ref = _upload(ref_png)
    mask = _upload(mask_png) if mask_png else None
    style_ref = _upload(_style_ref()) if P.STYLE_WEIGHT else None
    positive = P.POSITIVE.replace(drop, "") if drop else P.POSITIVE
    wf = _workflow(positive.format(style=style, pose=pose), ref, seed, denoise, f"kotoha_{name}_{tag}",
                   pixelate=pixelate, mask=mask, style=style_ref,
                   style_weight=style_weight, style_type=style_type)
    t = time.time()
    outs = _run(wf)
    cut = _fetch(outs["7"][0], OUT_DIR / name / f"{tag}_{seed}.png")
    _keep_main_island(cut)
    raw = _fetch(outs["11"][0], OUT_DIR / name / f"{tag}_{seed}_raw.png")
    print(f"  {name} {tag} seed={seed} denoise={denoise} {time.time() - t:.0f}s")
    return cut, raw


def _restyled(name: str, work: pathlib.Path) -> pathlib.Path:
    """描き直し済みの絵（白抜き前）。無ければ img/dot の絵を白に置いたもの。"""
    done = sorted((OUT_DIR / name).glob("final_*_raw.png"))
    return done[-1] if done else _canvas(DOT_DIR / f"{name}.png", work / f"{name}_base.png")


def make(name: str, spec: dict, seed: int, only_blink: bool = False) -> None:
    pose, source = spec["pose"], spec["source"]
    if source != "self":
        pose += P.PROPORTION
    work = OUT_DIR / name / "work"
    done = sorted((OUT_DIR / name).glob("final_*_raw.png"))
    if only_blink and done:
        final_raw = done[-1]
        final = final_raw.with_name(final_raw.name.replace("_raw", ""))
    elif source == "self":
        ref = _canvas(DOT_DIR / f"{name}.png", work / "ref.png")
        final, final_raw = _stage(name, pose, ref, seed, P.RESTYLE_DENOISE, P.PIXEL_STYLE, "final")
    else:
        final, final_raw = _stage(name, pose, _restyled(source, work), seed,
                                  spec.get("denoise", P.POSE_DENOISE), P.PIXEL_STYLE, "final")
    shutil.copy(final, OUT_DIR / name / f"{name}.png")
    # 色味は白抜き前ではなく出来た絵で測り、本体・まばたき・コマに同じ値を使う（tone.py）
    toned = tone.measure(final, DOT_DIR / f"{P.TONE_REF}.png") if P.TONE_REF and source != "self" else None
    if toned:
        tone.apply(OUT_DIR / name / f"{name}.png", toned)
    if spec.get("blink", True):
        # 出来た本体を元に、目の窓だけ塗り直す。窓の外は本体そのもの。
        mask = _eye_mask(final, work / "eye_mask.png", spec.get("eye_window", P.EYE_WINDOW))
        blink, _ = _stage(name, f"{pose}, {P.BLINK}", final_raw, seed, P.BLINK_DENOISE, P.PIXEL_STYLE, "blink",
                          mask_png=mask, drop=P.BLINK_DROP)
        shutil.copy(blink, OUT_DIR / name / f"{name}-blink.png")
        if toned:
            tone.apply(OUT_DIR / name / f"{name}-blink.png", toned)
    if only_blink:
        return
    for tag, words in spec.get("frames", {}).items():
        # 動きのコマ。本体を元に、窓の中（足など）だけ描き直す。窓の外は本体そのもの。
        mask = _eye_mask(final, work / f"{tag}_mask.png", spec.get("frame_window", P.LEGS_WINDOW))
        frame, _ = _stage(name, f"{pose}, {words}", final_raw, seed, P.FRAME_DENOISE, P.PIXEL_STYLE, tag,
                          mask_png=mask)
        shutil.copy(frame, OUT_DIR / name / f"{name}-{tag}.png")
        if toned:
            tone.apply(OUT_DIR / name / f"{name}-{tag}.png", toned)


# ---- まとめ ------------------------------------------------------------------

def sheet(names) -> pathlib.Path:
    """見比べ用。1行に 本体・まばたき を並べる。小さくして枚数が多くても1枚に収める。"""
    cell = 256
    rows = []
    for name in names:
        row = [OUT_DIR / name / f"{name}.png", OUT_DIR / name / f"{name}-blink.png"]
        row += [OUT_DIR / name / f"{name}-{tag}.png" for tag in P.POSES[name].get("frames", {})]
        row += sorted((OUT_DIR / name).glob("pose_*_raw.png"))[:1]
        rows.append((name, [p for p in row if p.exists()]))
    width = cell * max(len(r) for _, r in rows)
    out = Image.new("RGBA", (width, cell * len(rows)), (200, 200, 200, 255))
    for j, (name, files) in enumerate(rows):
        for i, path in enumerate(files):
            im = Image.open(path).convert("RGBA")
            im.thumbnail((cell - 8, cell - 8))
            out.alpha_composite(im, (i * cell + 4, j * cell + cell - 4 - im.height))
    dest = OUT_DIR / "sheet.png"
    out.save(dest)
    return dest


def apply(names) -> None:
    """台帳にある絵を img/dot/ に置く。差し替え前の絵は img/dot/old/ に退避する。"""
    ledger = _ledger()
    names = [n for n in names if n in ledger]
    old = DOT_DIR / "old"
    old.mkdir(exist_ok=True)
    for name in names:
        for suffix in ("", "-blink", *(f"-{tag}" for tag in P.POSES[name].get("frames", {}))):
            src = OUT_DIR / name / f"{name}{suffix}.png"
            dst = DOT_DIR / f"{name}{suffix}.png"
            if dst.exists() and not (old / dst.name).exists():
                shutil.copy(dst, old / dst.name)
            if not src.exists():
                # out 側に無い差分（消した失敗まばたきなど）は、古いものを残さない。
                # 古い差分が新しい本体に重なると、目を閉じるたびに別人の顔になる。
                if suffix and dst.exists():
                    dst.unlink()
                    print(f"  消した: {dst.relative_to(BASE_DIR)}（out に無い）")
                continue
            shutil.copy(src, dst)
            print(f"  {dst.relative_to(BASE_DIR)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="名前をカンマ区切りで")
    ap.add_argument("--seed", type=int, default=P.SEED)
    ap.add_argument("--variants", type=int, default=1, help="Seed をずらして候補を何枚出すか")
    ap.add_argument("--blink-only", action="store_true", help="本体はそのまま、まばたき（とコマ）だけ作り直す")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--accept", help="台帳に載せる名前（カンマ区切り）")
    ap.add_argument("--retry", help="Seed をずらして作り直す名前（カンマ区切り）。台帳から外す")
    ap.add_argument("--sheet", action="store_true")
    a = ap.parse_args(argv)

    names = [n.strip() for n in a.only.split(",")] if a.only else list(P.POSES)
    unknown = [n for n in names if n not in P.POSES]
    if unknown:
        sys.exit(f"poses.py に無い名前: {unknown}")

    ledger = _ledger()
    if a.accept:
        for name in a.accept.split(","):
            ledger[name.strip()] = {"seed": ledger.get(name.strip(), {}).get("seed", a.seed)}
        _save_ledger(ledger)
        print("採用:", ", ".join(sorted(ledger)))
        return 0
    if a.retry:
        names = [n.strip() for n in a.retry.split(",")]
        for name in names:
            tries = ledger.pop(name, {}).get("tries", 0)
            ledger.setdefault("_tries", {})[name] = tries + 1
        _save_ledger(ledger)
        for name in names:
            seed = a.seed + 1000 * ledger["_tries"][name]
            make(name, P.POSES[name], seed, only_blink=a.blink_only)
            print(f"  作り直した: {name} seed={seed}")
        print(sheet(names))
        return 0
    if a.apply:
        apply(names)
        return 0
    if a.sheet:
        print(sheet(names))
        return 0

    for name in names:
        for i in range(a.variants):
            make(name, P.POSES[name], a.seed + i, only_blink=a.blink_only)
    print(sheet(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
