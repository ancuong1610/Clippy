"""Raw 16:9 clip -> 1080x1920 Short with word-by-word captions (local Whisper, no API key).

Layouts:
  blur  - full frame centered over a blurred zoomed copy (works for any streamer, no setup)
  stack - streamer facecam on top, gameplay center-crop below (needs facecam rect per streamer)
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

W, H = 1080, 1920
FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
YELLOW, WHITE, BLACK = (255, 222, 0, 255), (255, 255, 255, 255), (0, 0, 0, 255)


@dataclass
class Word:
    start: float
    end: float
    text: str


def transcribe(video: Path, model: str = WHISPER_MODEL) -> list[Word]:
    import mlx_whisper
    res = mlx_whisper.transcribe(str(video), path_or_hf_repo=model, word_timestamps=True,
                                 condition_on_previous_text=False)
    words = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            t = w["word"].strip()
            if t:
                words.append(Word(float(w["start"]), float(w["end"]), t))
    return words


def chunk(words: list[Word], max_words: int = 3, max_gap: float = 0.6) -> list[list[Word]]:
    out: list[list[Word]] = []
    for w in words:
        if out and len(out[-1]) < max_words and w.start - out[-1][-1].end <= max_gap \
                and not out[-1][-1].text.endswith((".", "?", "!")):
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def render_overlay(title: str, words: list[Word] | None, active: int, cap_y: int, title_y: int) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if title:
        f = _font(54)
        lines = _wrap(d, title, f, W - 160)
        lh = 70
        top = title_y - len(lines) * lh // 2
        box_w = max(d.textlength(l, font=f) for l in lines) + 60
        d.rounded_rectangle([(W - box_w) / 2, top - 22, (W + box_w) / 2, top + len(lines) * lh + 14],
                            radius=22, fill=WHITE)
        for i, l in enumerate(lines):
            d.text((W / 2, top + i * lh), l, font=f, fill=BLACK, anchor="ma")
    if words:
        f = _font(88)
        toks = [w.text.upper() for w in words]
        space = d.textlength(" ", font=f)
        widths = [d.textlength(t, font=f) for t in toks]
        total = sum(widths) + space * (len(toks) - 1)
        scale = min(1.0, (W - 80) / total)
        if scale < 1.0:
            f = _font(int(88 * scale))
            space = d.textlength(" ", font=f)
            widths = [d.textlength(t, font=f) for t in toks]
            total = sum(widths) + space * (len(toks) - 1)
        x = (W - total) / 2
        for i, t in enumerate(toks):
            d.text((x, cap_y), t, font=f, fill=YELLOW if i == active else WHITE,
                   stroke_width=9, stroke_fill=BLACK, anchor="ls")
            x += widths[i] + space
    return img


def _filter(layout: str, facecam: tuple[float, float, float, float] | None) -> tuple[str, int, int]:
    """Returns (filter_complex producing [base], caption_y, title_y)."""
    if layout == "stack":
        if not facecam:
            raise SystemExit("stack layout needs --facecam x,y,w,h (fractions of the frame)")
        fx, fy, fw, fh = facecam
        top_h, bot_h = 640, H - 640
        f = (f"[0:v]split=2[a][b];"
             f"[a]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top_h}:force_original_aspect_ratio=increase,"
             f"crop={W}:{top_h}[cam];"
             f"[b]crop=ih*{W}/{bot_h}:ih,scale={W}:{bot_h}[game];"
             f"[cam][game]vstack=inputs=2,setsar=1[base]")
        return f, top_h + 40, 150
    # blur
    fg_h = round(W * 9 / 16)
    top = (H - fg_h) // 2
    f = (f"[0:v]split=2[a][b];"
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.15[bg];"
         f"[b]scale={W}:{fg_h}[fg];[bg][fg]overlay=0:{top},setsar=1[base]")
    return f, top + fg_h + 170, top - 140


def make_short(src: Path, out: Path, title: str = "", layout: str = "blur",
               facecam: tuple[float, float, float, float] | None = None,
               model: str = WHISPER_MODEL) -> Path:
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)]).strip())
    words = transcribe(src, model)
    log.info("transcribed %d words", len(words))
    base_filter, cap_y, title_y = _filter(layout, facecam)

    # timeline of overlay states: (start, end, chunk, active_index)
    states: list[tuple[float, float, list[Word] | None, int]] = []
    t = 0.0
    for ch in chunk(words):
        for i, w in enumerate(ch):
            s = max(w.start, t)
            e = ch[i + 1].start if i + 1 < len(ch) else w.end + 0.15
            if s > t:
                states.append((t, s, None, -1))
            if e > s:
                states.append((s, e, ch, i))
                t = e
    if t < dur:
        states.append((t, dur, None, -1))

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        cache: dict[tuple, Path] = {}
        lines = ["ffconcat version 1.0"]
        for n, (s, e, ch, act) in enumerate(states):
            key = (tuple(w.text for w in ch) if ch else (), act)
            if key not in cache:
                p = tmpd / f"o{len(cache):04d}.png"
                render_overlay(title, ch, act, cap_y, title_y).save(p)
                cache[key] = p
            lines += [f"file '{cache[key]}'", f"duration {max(e - s, 0.001):.3f}"]
        lines.append(f"file '{cache[key]}'")  # concat demuxer needs the last file repeated
        (tmpd / "ov.txt").write_text("\n".join(lines))
        fc = f"{base_filter};[1:v]format=rgba[ov];[base][ov]overlay=0:0:eof_action=repeat,format=yuv420p[v]"
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-f", "concat", "-safe", "0",
               "-i", str(tmpd / "ov.txt"), "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
               "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "aac", "-b:a", "160k", "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True)
    out.with_suffix(".words.json").write_text(json.dumps([w.__dict__ for w in words], ensure_ascii=False))
    return out
