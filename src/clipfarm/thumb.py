"""YouTube thumbnails from a clip: big reaction face + punchy action frame + 1-4 bold words.

Rules baked in (from MrBeast + top streamer-clip thumbnails, see .claude/skills/youtube-thumbnail):
  - one big face with emotion, cut from the facecam, as a white-bordered "sticker"
  - action frame zoomed in a bit (kills edge clutter like chat boxes), saturation/contrast up
  - 1-4 words, huge, white + black stroke; *word* in asterisks is highlighted yellow
  - optional red circle on the thing to look at
Outputs 1280x720 (long-form / Shorts search) and/or 1080x1920 (Shorts), JPG under 2 MB.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FONT = "/System/Library/Fonts/Supplemental/Impact.ttf"
YELLOW, WHITE, BLACK, RED = (255, 222, 0), (255, 255, 255), (0, 0, 0), (235, 30, 35)
SIZES = {"16:9": (1280, 720), "9:16": (1080, 1920)}


def grab_frame(video: Path, t: float) -> Image.Image:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    from io import BytesIO
    return Image.open(BytesIO(raw)).convert("RGB")


def duration(video: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(video)]).strip())


def sharpness(img: Image.Image) -> float:
    g = np.asarray(img.convert("L").resize((480, 270)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    bright = g.mean()
    penalty = 0.3 if bright < 40 or bright > 220 else 1.0  # avoid black / blown-out frames
    return float(lap.var()) * penalty


def best_frames(video: Path, center: float | None, n: int) -> list[tuple[float, Image.Image]]:
    """Sharpest frames near the moment (audio peak) — or across the clip if unknown."""
    dur = duration(video)
    if center is None:
        times = list(np.linspace(1, max(1.5, dur - 1), 16))
    else:
        times = [min(max(0.2, center + d), dur - 0.2) for d in np.arange(-2.5, 2.6, 0.5)]
    scored = sorted(((sharpness(f := grab_frame(video, t)), t, f) for t in times), key=lambda x: -x[0])
    picked: list[tuple[float, Image.Image]] = []
    for _, t, f in scored:  # spread picks out so variants differ
        if all(abs(t - p[0]) >= 1.0 for p in picked):
            picked.append((t, f))
        if len(picked) == n:
            break
    return picked


def crop_frac(img: Image.Image, rect: tuple[float, float, float, float]) -> Image.Image:
    x, y, w, h = rect
    W, H = img.size
    return img.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))


def cover(img: Image.Image, size: tuple[int, int], zoom: float = 1.0, focus=(0.5, 0.5)) -> Image.Image:
    tw, th = size
    s = max(tw / img.width, th / img.height) * zoom
    im = img.resize((math.ceil(img.width * s), math.ceil(img.height * s)), Image.LANCZOS)
    left = int((im.width - tw) * focus[0])
    top = int((im.height - th) * focus[1])
    return im.crop((left, top, left + tw, top + th))


def punch(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(1.35)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    return ImageEnhance.Sharpness(img).enhance(1.3)


def vignette(img: Image.Image, strength: float = 0.45) -> Image.Image:
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    mask = np.clip(1 - strength * np.clip(d - 0.55, 0, None) / 0.9, 0, 1)
    arr = np.asarray(img, dtype=np.float32) * mask[..., None]
    return Image.fromarray(arr.astype(np.uint8))


def sticker(face: Image.Image, height: int, border: int = 10) -> Image.Image:
    face = punch(face)
    face = face.resize((int(face.width * height / face.height), height), Image.LANCZOS)
    radius = 28
    mask = Image.new("L", face.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, *face.size], radius=radius, fill=255)
    out = Image.new("RGBA", (face.width + 2 * border, face.height + 2 * border), (0, 0, 0, 0))
    ImageDraw.Draw(out).rounded_rectangle([0, 0, *out.size], radius=radius + border, fill=WHITE + (255,))
    out.paste(face, (border, border), mask)
    return out


def paste_shadow(canvas: Image.Image, item: Image.Image, xy: tuple[int, int], blur: int = 18) -> None:
    shadow = Image.new("RGBA", item.size, (0, 0, 0, 0))
    shadow.putalpha(item.getchannel("A").point(lambda a: int(a * 0.7)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (xy[0] + 10, xy[1] + 14))
    canvas.alpha_composite(item, xy)


def parse_words(text: str) -> list[tuple[str, bool]]:
    """'HE GOT *CAUGHT*' -> [('HE',False),('GOT',False),('CAUGHT',True)]"""
    out = []
    for w in text.split():
        hl = w.startswith("*") and w.endswith("*") and len(w) > 2
        out.append((w.strip("*").upper(), hl))
    return out


def text_block(text: str, max_w: int, max_h: int, angle: float = -4) -> Image.Image:
    words = parse_words(text)
    lines: list[list[tuple[str, bool]]] = []
    # 1-2 words per line keeps letters huge
    per = 1 if len(words) <= 2 else 2
    for i in range(0, len(words), per):
        lines.append(words[i:i + per])
    size = 400
    while size > 40:
        f = ImageFont.truetype(FONT, size)
        stroke = max(6, size // 14)
        d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        widths = [sum(d.textlength(w + " ", font=f) for w, _ in ln) for ln in lines]
        lh = int(size * 1.02)
        if max(widths) + 2 * stroke <= max_w and lh * len(lines) + 2 * stroke <= max_h:
            break
        size -= 6
    img = Image.new("RGBA", (int(max(widths)) + 4 * stroke, lh * len(lines) + 4 * stroke), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        x = 2 * stroke + (max(widths) - widths[i]) / 2
        for w, hl in ln:
            d.text((x, 2 * stroke + i * lh), w, font=f, fill=YELLOW if hl else WHITE,
                   stroke_width=stroke, stroke_fill=BLACK)
            x += d.textlength(w + " ", font=f)
    return img.rotate(angle, expand=True, resample=Image.BICUBIC)


def red_circle(canvas: Image.Image, cx: float, cy: float, r: float) -> None:
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    R = r * min(W, H)
    for k in range(3):  # thick ring with a darker edge
        d.ellipse([cx * W - R - k, cy * H - R - k, cx * W + R + k, cy * H + R + k], outline=RED + (255,),
                  width=12 - 2 * k)


@dataclass
class ThumbSpec:
    text: str
    fmt: str = "16:9"
    facecam: tuple[float, float, float, float] | None = None
    face_side: str = "right"  # sticker side; text goes to the other side
    circle: tuple[float, float, float] | None = None  # x, y, radius as fractions of the thumbnail
    zoom: float = 1.35       # action zoom: crops away edge overlays (chat boxes, alerts)
    face_zoom: float = 1.6   # facecam boxes include the room; zoom toward the face
    face_focus: tuple[float, float] = (0.5, 0.3)  # where the face sits inside the facecam box


def compose(frame: Image.Image, spec: ThumbSpec) -> Image.Image:
    W, H = SIZES[spec.fmt]
    face = crop_frac(frame, spec.facecam) if spec.facecam else None
    action, focus = frame, (0.5, 0.45)
    if spec.facecam:
        # frame the action away from the facecam corner (chat/alerts usually sit next to it)
        fx, fy, fw, fh = spec.facecam
        focus = (0.85 if fx + fw / 2 < 0.5 else 0.15, 0.2 if fy + fh / 2 > 0.5 else 0.8)
    if spec.fmt == "16:9":
        canvas = vignette(punch(cover(action, (W, H), spec.zoom, focus))).convert("RGBA")
        if face:
            fh_px = int(H * 0.72)
            st = sticker(cover(face, (int(fh_px * 0.85), fh_px), spec.face_zoom, spec.face_focus), fh_px)
            x = W - st.width - 24 if spec.face_side == "right" else 24
            paste_shadow(canvas, st, (x, H - st.height + 8))  # bleed off the bottom edge
            tx_w = W - st.width - 70
            tb = text_block(spec.text, tx_w, int(H * 0.5))
            tx = 30 if spec.face_side == "right" else W - tb.width - 30
        else:
            tb = text_block(spec.text, int(W * 0.9), int(H * 0.42))
            tx = (W - tb.width) // 2
        if spec.circle:
            red_circle(canvas, *spec.circle)
        paste_shadow(canvas, tb, (tx, 18), blur=10)
    else:  # 9:16 — face big on top, action below, text across the seam
        top_h = int(H * 0.42)
        canvas = Image.new("RGBA", (W, H), BLACK + (255,))
        if face:
            canvas.paste(punch(cover(face, (W, top_h), spec.face_zoom, spec.face_focus)), (0, 0))
        canvas.paste(vignette(punch(cover(action, (W, H - top_h), spec.zoom, focus))), (0, top_h))
        if not face:
            canvas.paste(punch(cover(action, (W, top_h), 1.6)), (0, 0))
        if spec.circle:
            red_circle(canvas, *spec.circle)
        tb = text_block(spec.text, int(W * 0.92), int(H * 0.26), angle=-3)
        paste_shadow(canvas, tb, ((W - tb.width) // 2, top_h - tb.height // 2), blur=10)
    return canvas.convert("RGB")


def save_jpg(img: Image.Image, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    for q in (92, 85, 78, 70):  # YouTube thumbnail limit is 2 MB
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size < 2_000_000:
            break
    return out


def make_thumbnails(video: Path, spec: ThumbSpec, variants: int = 3, at: float | None = None,
                    out_dir: Path | None = None) -> list[Path]:
    center = at
    meta = video.with_suffix(".json")
    if center is None and meta.exists():
        center = (json.loads(meta.read_text()).get("score") or {}).get("peak_at_s")
    out_dir = out_dir or video.parent
    paths = []
    tag = spec.fmt.replace(":", "x")
    for i, (t, frame) in enumerate(best_frames(video, center, variants), 1):
        paths.append(save_jpg(compose(frame, spec), out_dir / f"{video.stem}_thumb_{tag}_v{i}.jpg"))
    return paths
