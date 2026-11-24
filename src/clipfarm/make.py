"""One shot: raw clip -> reviewed-ready Short.

  transcribe -> AI titles + best window + caption fixes -> trim to payoff -> detect facecam
  -> render (stack if facecam found, else blur) with a topic label and fixed captions -> thumbnails
Writes <out>.mp4, <out>.json (titles, window, layout, score) and thumbnails next to it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from . import load_config
from .edit import Word, make_short, transcribe
from .face import detect_facecam
from .thumb import ThumbSpec, make_thumbnails
from .titles import apply_fixes, drop_lyrics, label_text, make_titles

log = logging.getLogger(__name__)


def trim(src: Path, start: float, end: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{end - start:.2f}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


def loudest(video: Path, win: float = 1.5, skip_frac: float = 0.25) -> float:
    """Start of the loudest `win` seconds (RMS), ignoring the first `skip_frac` of the clip (the setup)."""
    import numpy as np
    sr = 8000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = int(win * sr)
    if len(a) < n * 2:
        return 0.0
    energy = np.convolve(a * a, np.ones(n, dtype=np.float32), mode="valid")[::sr // 10]  # 0.1 s steps
    lo = int(len(energy) * skip_frac)
    return (lo + int(np.argmax(energy[lo:]))) / 10


def tease_start(peak: float, length: float, lead: float = 1.0) -> float:
    """Start of the hook tease: the `length` s that end `lead` s BEFORE the loudest moment (the scream usually
    lands with the crash/death), so the hook shows the build-up and the viewer stays to see how it ends.
    Owner's review 2026-11-21: 'use the second before he dies as the hook, don't show the death'."""
    return round(max(0.0, peak - lead - length), 2)


def cold_open(src: Path, at: float, length: float, out: Path) -> Path:
    """Buffer hook: a `length` s tease from `at` first, then the clip from the start."""
    fc = (f"[0:v]trim={at:.2f}:{at + length:.2f},setpts=PTS-STARTPTS[v1];"
          f"[0:a]atrim={at:.2f}:{at + length:.2f},asetpts=PTS-STARTPTS[a1];"
          f"[0:v]setpts=PTS-STARTPTS[v2];[0:a]asetpts=PTS-STARTPTS[a2];"
          f"[v1][a1][v2][a2]concat=n=2:v=1:a=1[v][a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart", str(out)], check=True)
    return out


DARK_LUMA = 25.0   # mean luma (16-235) below this = black/loading game screen; owner 2026-11-24 render check: ~19


def frame_luma(src: Path, start: float, seconds: float, fps: float = 2.0) -> list[float]:
    """Mean luma of `fps` frames per second from `start` for `seconds`."""
    import re
    err = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{seconds:.2f}",
                          "-vf", f"fps={fps},signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True).stdout
    return [float(x) for x in re.findall(r"YAVG=([\d.]+)", err)]


def skip_dark_start(src: Path, start: float, end: float, *, min_len: float = 12.0, max_skip: float = 8.0,
                    step: float = 0.5, luma=None) -> float:
    """Move the window start past a black/loading game screen at its beginning (dead air for a stranger).
    Keeps the window at least `min_len` s; returns the original start when nothing is dark."""
    values = (luma or frame_luma)(src, start, max_skip, 1 / step)
    dark = 0
    for v in values:
        if v >= DARK_LUMA:
            break
        dark += 1
    if not dark or dark == len(values):  # nothing dark, or dark the whole probe (leave it to the judge)
        return start
    new = round(start + dark * step, 2)
    return new if end - new >= min_len else start


PROFANITY = {"fuck": "f*ck", "fucking": "f*cking", "fucked": "f*cked", "motherfucker": "motherf*cker",
             "shit": "sh*t", "bitch": "b*tch", "nigga": "n*gga", "niggas": "n*ggas", "nigger": "n****r",
             "cunt": "c*nt", "dick": "d*ck", "pussy": "p*ssy", "asshole": "a**hole"}


def censor(text: str) -> str:
    """Soften swear words in burned-in text (reach on TikTok/IG); keeps punctuation and case of the first letter."""
    import re

    def sub(m: re.Match) -> str:
        word = m.group(0)
        out = PROFANITY[word.lower()]
        return out.upper() if word.isupper() else (out[0].upper() + out[1:] if word[0].isupper() else out)
    return re.sub(r"[A-Za-z]+", lambda m: sub(m) if m.group(0).lower() in PROFANITY else m.group(0), text)


def shift_words(words: list[dict], start: float, end: float) -> list[Word]:
    return [Word(round(w["start"] - start, 3), round(w["end"] - start, 3), w["text"])
            for w in words if w["start"] >= start - 0.05 and w["end"] <= end + 0.3]


def chat_events_for(meta: dict, titles: dict, start: float, end: float) -> list[dict]:
    """Timed chat lines for the trimmed clip (spread evenly if the clip predates timestamps)."""
    from .chatoverlay import CHAT_WINDOW_S, schedule, select
    timed = meta.get("chat_timed")
    has_ts = bool(timed)
    if not timed:  # old clips: "user: text" strings only
        timed = [{"t": 0.0, "user": u.strip(), "text": x.strip()}
                 for u, _, x in (c.partition(":") for c in meta.get("chat", [])) if x.strip()]
    spike = meta.get("spike_at")
    if spike is None and meta.get("event") and meta.get("window"):
        spike = meta["event"]["ts"] - meta["window"][0]
    dur = end - start
    spike_t = (spike if spike is not None else start + dur * 0.6) - start
    spike_t = min(max(spike_t, 1.5), max(1.5, dur - CHAT_WINDOW_S + 1))  # keep ~8 s of chat inside the cut
    shifted = [{**m, "t": m["t"] - start} for m in timed] if has_ts else [{**m, "t": spike_t} for m in timed]
    msgs = select(shifted, titles.get("chat_highlights"), spike_t)
    return schedule(msgs, spike_t, dur, has_timestamps=has_ts)


def make(clip: Path, streamer: str | None = None, context: str = "GTA 6 livestream",
         layout: str = "auto", captions: str | None = None, out_dir: Path | None = None,
         chat: str = "auto", chat_min_score: int = 70, campaign: dict | None = None,
         titles: dict | None = None, hook_s: float = 1.5, banner_s: float = 3.0, hook_lead: float = 1.0,
         impact_lead: float = 0.3, *, config_path: Path = Path("channels.toml")) -> dict:
    """campaign: from campaigns.route() -> its logo is burned in. titles: reuse earlier AI output (no API call).
    hook_s: cold open with a `hook_s` s tease that ends `hook_lead` s before the loudest moment (0 = off);
    when the label is disabled, the hook banner shows for hook_s + banner_s."""
    from .campaigns import logo_path
    edit_cfg = load_config(config_path).get("edit", {}) if config_path.exists() else {}
    captions = edit_cfg.get("captions", "punch") if captions is None else captions
    out_dir = out_dir or clip.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    words_f = clip.with_suffix(".words.json")
    if not words_f.exists():
        words_f.write_text(json.dumps([w.__dict__ for w in transcribe(clip)], ensure_ascii=False))
    words = json.loads(words_f.read_text())

    t = titles or make_titles(clip, streamer, context)            # titles, hook, window, fixes
    label = label_text(t) if edit_cfg.get("label", True) else ""
    fixed = drop_lyrics(apply_fixes(words, t.get("caption_fixes")), t.get("lyric_spans"))
    start, end = t["window"]
    if edit_cfg.get("skip_dark", True):
        start = skip_dark_start(clip, start, end)
        t["window"] = [start, end]
    stem = clip.stem
    trimmed = trim(clip, start, end, out_dir / f"{stem}_cut.mp4")
    cut_words = shift_words(fixed, start, end)
    if edit_cfg.get("censor", True):
        cut_words = [Word(w.start, w.end, censor(w.text)) for w in cut_words]
        label = censor(label)

    face = detect_facecam(trimmed) if layout in ("auto", "stack") else None
    box, focus = face if face else (None, None)
    chosen = "stack" if box else ("blur" if layout in ("auto", "stack") else layout)

    meta_raw = json.loads(clip.with_suffix(".json").read_text()) if clip.with_suffix(".json").exists() else {}
    jd = meta_raw.get("judge") or {}
    score = jd.get("hype") or (meta_raw.get("score") or {}).get("total") or 0
    use_chat = chat == "on" or (chat == "auto" and score >= chat_min_score)
    events = chat_events_for(meta_raw, t, start, end) if use_chat else []

    src, hook_at = trimmed, None
    payoff = jd.get("payoff_at")
    has_payoff = isinstance(payoff, (int, float)) and not isinstance(payoff, bool) and start <= payoff <= end
    peak_seconds = float(edit_cfg.get("peak_fullscreen_s", 2.0))
    use_peak = chosen == "stack" and end - start >= 8 and edit_cfg.get("peak_fullscreen", True) and peak_seconds > 0
    audio_peak = None
    if hook_s and end - start > hook_s * 4:
        audio_peak = loudest(trimmed, hook_s)
        hook_at = tease_start(audio_peak, hook_s, hook_lead)
        if has_payoff:
            # the judge saw the payoff in a frame; it began at most one frame step earlier. Look again with dense
            # frames for the moment of contact and end the tease right before it ("right before the disaster").
            from .judge import impact_time
            step = jd.get("frame_step") or 3.0
            hit = impact_time(clip, jd.get("payoff") or "the payoff", payoff - step, payoff + 0.4)
            tease_end = (hit - impact_lead) if hit is not None else (payoff - step)
            hook_at = round(max(0.0, tease_end - start - hook_s), 2)
        src = cold_open(trimmed, hook_at, hook_s, out_dir / f"{stem}_hook.mp4")
        hook_words = [Word(round(w.start - hook_at, 3), round(w.end - hook_at, 3), w.text) for w in cut_words
                      if w.start >= hook_at - 0.05 and w.end <= hook_at + hook_s + 0.05]
        cut_words = hook_words + [Word(w.start + hook_s, w.end + hook_s, w.text) for w in cut_words]
        events = [{**m, "show": m["show"] + hook_s, "hide": m["hide"] + hook_s} for m in events]
    peak_at = None
    if use_peak:
        if has_payoff and payoff < end:
            peak_at = payoff - start
        else:
            peak_at = audio_peak if audio_peak is not None else loudest(trimmed, hook_s or 1.5)
        peak_at = round(peak_at + (hook_s if hook_at is not None else 0), 3)
    short = make_short(src, out_dir / f"{stem}_short.mp4", t["thumbnail_text"], chosen, box,
                       captions=captions, words_override=cut_words, chat_events=events,
                       logo=logo_path(campaign), title_until=(hook_s + banner_s) if hook_at is not None else None,
                       label=label, peak_at=peak_at, peak_fullscreen_s=peak_seconds)
    thumbs = make_thumbnails(trimmed, ThumbSpec(t["thumbnail_text"], "16:9", box,
                                                face_focus=focus or (0.5, 0.3)), variants=2, out_dir=out_dir)

    result = {"source": str(clip), "short": str(short), "thumbnails": [str(p) for p in thumbs],
              "window": [start, end], "layout": chosen, "facecam": box, "titles": t,
              "score": meta_raw.get("score"), "event": meta_raw.get("event"),
              "caption_fixes": t.get("caption_fixes"), "chat_overlay": events, "hook_at": hook_at,
              "peak_at": peak_at,
              "campaign": (campaign or {}).get("name")}
    Path(short).with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
