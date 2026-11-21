"""Raw 16:9 clip -> 1080x1920 Short with word-by-word captions (local Whisper, no API key).

Layouts:
  blur  - full frame centered over a blurred zoomed copy (works for any streamer, no setup)
  stack  - streamer facecam on top, gameplay center-crop below (needs facecam rect per streamer)
  square - center square of the frame on blurred fill (good for gameplay action in the middle)
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .fonts import font

log = logging.getLogger(__name__)

W, H = 1080, 1920
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
YELLOW, WHITE, BLACK = (255, 222, 0, 255), (255, 255, 255, 255), (0, 0, 0, 255)
GREEN = (83, 252, 24, 255)
LOGO_W = 520  # campaign logo width on the 1080-wide Short


@dataclass(frozen=True)
class CaptionStyle:
    words_per_line: int
    size: int
    active: str  # "color" = active word recolored, "pill" = active word on a colored pill
    active_color: tuple
    base_color: tuple = WHITE
    stroke: int = 9


CAPTION_PRESETS = {
    "karaoke": CaptionStyle(2, 96, "color", GREEN),            # default (ClipSpeed/streamer clips)
    "hormozi": CaptionStyle(2, 92, "pill", YELLOW, stroke=8),  # active word: black on yellow pill
    "classic": CaptionStyle(3, 88, "color", YELLOW),           # our first version
}


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
    return font("black", size)


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


def paste_logo(img: Image.Image, logo: Path, y: int, width: int = LOGO_W) -> None:
    """Campaign logo, unaltered except for one uniform scale (campaign rule: no stretch/recolor/crop)."""
    lg = Image.open(logo).convert("RGBA")
    lg = lg.resize((width, round(lg.height * width / lg.width)), Image.LANCZOS)
    img.alpha_composite(lg, ((W - width) // 2, y))


def render_overlay(title: str, words: list[Word] | None, active: int, cap_y: int, title_y: int,
                   style: CaptionStyle = CAPTION_PRESETS["karaoke"], logo: Path | None = None,
                   logo_y: int = 0) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if logo:
        paste_logo(img, logo, logo_y)
    if title and "*" in title:  # hook banner: big words, *payoff* word in yellow
        from .thumb import parse_words
        toks = parse_words(title)
        lines = [toks[:2], toks[2:]] if len(toks) > 2 else [toks]
        size = 112
        while size > 50:
            f = _font(size)
            if max(sum(d.textlength(w + " ", font=f) for w, _ in ln) for ln in lines if ln) <= W - 90:
                break
            size -= 6
        lh = int(size * 1.08)
        top = title_y - (len([l for l in lines if l]) * lh) // 2
        for i, ln in enumerate(l for l in lines if l):
            widths = [d.textlength(w, font=f) for w, _ in ln]
            sp = d.textlength(" ", font=f)
            x = (W - (sum(widths) + sp * (len(ln) - 1))) / 2
            for (w, hl), wd in zip(ln, widths):
                d.text((x, top + i * lh), w, font=f, fill=YELLOW if hl else WHITE,
                       stroke_width=max(8, size // 11), stroke_fill=BLACK)
                x += wd + sp
    elif title:
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
        size = style.size
        f = _font(size)
        toks = [w.text.upper() for w in words]
        space = d.textlength(" ", font=f)
        widths = [d.textlength(t, font=f) for t in toks]
        total = sum(widths) + space * (len(toks) - 1)
        if total > W - 100:  # shrink long words to fit
            size = int(size * (W - 100) / total)
            f = _font(size)
            space = d.textlength(" ", font=f)
            widths = [d.textlength(t, font=f) for t in toks]
            total = sum(widths) + space * (len(toks) - 1)
        x = (W - total) / 2
        for i, t in enumerate(toks):
            is_active = i == active
            if is_active and style.active == "pill":
                pad = int(size * 0.18)
                top, bottom = cap_y - int(size * 0.95), cap_y + int(size * 0.22)
                d.rounded_rectangle([x - pad, top, x + widths[i] + pad, bottom], radius=pad,
                                    fill=style.active_color)
                d.text((x, cap_y), t, font=f, fill=BLACK, anchor="ls")
            else:
                color = style.active_color if is_active and style.active == "color" else style.base_color
                d.text((x, cap_y), t, font=f, fill=color, stroke_width=style.stroke,
                       stroke_fill=BLACK, anchor="ls")
            x += widths[i] + space
    return img


LOGO_BAND = (560, 780)  # with a campaign logo: cam 560 px, gameplay 780 px, black band below holds the logo
                        # (the layout ClipUp's own clippers use: logo never covers the game or the face)


def _filter(layout: str, facecam: tuple[float, float, float, float] | None,
            band: bool = False) -> tuple[str, int, int]:
    """Returns (filter_complex producing [base], caption_y, title_y)."""
    if layout == "stack":
        if not facecam:
            raise SystemExit("stack layout needs --facecam x,y,w,h (fractions of the frame)")
        fx, fy, fw, fh = facecam
        top_h, bot_h = LOGO_BAND if band else (640, H - 640)
        f = (f"[0:v]split=2[a][b];"
             f"[a]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top_h}:force_original_aspect_ratio=increase,"
             f"crop={W}:{top_h}[cam];"
             f"[b]crop=ih*{W}/{bot_h}:ih,scale={W}:{bot_h}[game];"
             f"[cam][game]vstack=inputs=2,pad={W}:{H}:0:0:black,setsar=1[base]")
        return f, top_h + 270, top_h  # banner centred on the seam: covers desk/chin strip, not the face
    if layout == "square":  # center square of the frame on blurred fill
        top = (H - W) // 2 - 60
        f = (f"[0:v]split=2[a][b];"
             f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.15[bg];"
             f"[b]crop=ih:ih,scale={W}:{W}[fg];[bg][fg]overlay=0:{top},setsar=1[base]")
        return f, top + W + 170, top - 120
    # blur
    fg_h = round(W * 9 / 16)
    top = (H - fg_h) // 2
    f = (f"[0:v]split=2[a][b];"
         f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.15[bg];"
         f"[b]scale={W}:{fg_h}[fg];[bg][fg]overlay=0:{top},setsar=1[base]")
    return f, top + fg_h + 170, top - 140


def make_short(src: Path, out: Path, title: str = "", layout: str = "blur",
               facecam: tuple[float, float, float, float] | None = None,
               model: str = WHISPER_MODEL, captions: str = "karaoke",
               words_override: list[Word] | None = None, chat_events: list[dict] | None = None,
               logo: Path | None = None, title_until: float | None = None) -> Path:
    """title_until: show the hook banner only for the first N seconds (None = whole clip)."""
    style = CAPTION_PRESETS[captions]
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)]).strip())
    cache = src.with_suffix(".words.json")  # transcribe once per clip, re-render freely
    if words_override is not None:
        words = words_override
    elif cache.exists():
        words = [Word(**w) for w in json.loads(cache.read_text())]
    else:
        words = transcribe(src, model)
        cache.write_text(json.dumps([w.__dict__ for w in words], ensure_ascii=False))
    log.info("%d caption words", len(words))
    base_filter, cap_y, title_y = _filter(layout, facecam, band=bool(logo))

    # caption timeline: (start, end, chunk, active_index)
    cap: list[tuple[float, float, list[Word] | None, int]] = []
    t = 0.0
    for ch in chunk(words, style.words_per_line):
        for i, w in enumerate(ch):
            s = max(w.start, t)
            e = ch[i + 1].start if i + 1 < len(ch) else w.end + 0.15
            if e > s:
                cap.append((s, e, ch, i))
                t = e
    chat_events = chat_events or []
    # merge caption + chat change points into one overlay timeline
    cuts = sorted({0.0, dur, *([title_until] if title_until else []), *[c[0] for c in cap], *[c[1] for c in cap],
                   *[m["show"] for m in chat_events], *[m["hide"] for m in chat_events]})
    cuts = [c for c in cuts if 0 <= c <= dur]
    states = []
    for s, e in zip(cuts, cuts[1:]):
        if e - s < 0.001:
            continue
        mid = (s + e) / 2
        cur = next((c for c in cap if c[0] <= mid < c[1]), None)
        vis = tuple(i for i, m in enumerate(chat_events) if m["show"] <= mid < m["hide"])
        on = title_until is None or mid < title_until
        states.append((s, e, cur[2] if cur else None, cur[3] if cur else -1, vis, on))
    if layout == "stack":  # logo centred in the black band under the gameplay, clear of the app's bottom UI
        band_top = sum(LOGO_BAND)
        logo_y = band_top + 30
        chat_box = (60, cap_y + 110, W - 60, band_top - 20) if logo else (60, H - 560, W - 60, H - 150)
    else:  # blur/square: logo in the empty blurred band at the top
        logo_y = 170
        chat_box = (60, H - 430, W - 60, H - 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        cache: dict[tuple, Path] = {}
        lines = ["ffconcat version 1.0"]
        for n, (s, e, ch, act, vis, on) in enumerate(states):
            key = (tuple(w.text for w in ch) if ch else (), act, vis, on)
            if key not in cache:
                p = tmpd / f"o{len(cache):04d}.png"
                img = render_overlay(title if on else "", ch, act, cap_y, title_y, style, logo, logo_y)
                if vis:
                    from .chatoverlay import draw_chat
                    draw_chat(img, [chat_events[i] for i in vis], chat_box)
                img.save(p)
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
    return out
