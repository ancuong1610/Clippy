"""One shot: raw clip -> reviewed-ready Short.

  transcribe -> AI titles + best window + caption fixes -> trim to payoff -> detect facecam
  -> render (stack if facecam found, else blur) with hook banner + fixed captions -> thumbnails
Writes <out>.mp4, <out>.json (titles, window, layout, score) and thumbnails next to it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .edit import Word, make_short, transcribe
from .face import detect_facecam
from .thumb import ThumbSpec, make_thumbnails
from .titles import apply_fixes, drop_lyrics, make_titles

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
         layout: str = "auto", captions: str = "karaoke", out_dir: Path | None = None,
         chat: str = "auto", chat_min_score: int = 70, campaign: dict | None = None,
         titles: dict | None = None, hook_s: float = 1.5, banner_s: float = 3.0, hook_lead: float = 1.0,
         impact_lead: float = 0.3) -> dict:
    """campaign: from campaigns.route() -> its logo is burned in. titles: reuse earlier AI output (no API call).
    hook_s: cold open with a `hook_s` s tease that ends `hook_lead` s before the loudest moment (0 = off);
    the hook banner shows for hook_s + banner_s."""
    from .campaigns import logo_path
    out_dir = out_dir or clip.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    words_f = clip.with_suffix(".words.json")
    if not words_f.exists():
        words_f.write_text(json.dumps([w.__dict__ for w in transcribe(clip)], ensure_ascii=False))
    words = json.loads(words_f.read_text())

    t = titles or make_titles(clip, streamer, context)            # titles, hook, window, fixes
    fixed = drop_lyrics(apply_fixes(words, t.get("caption_fixes")), t.get("lyric_spans"))
    start, end = t["window"]
    stem = clip.stem
    trimmed = trim(clip, start, end, out_dir / f"{stem}_cut.mp4")
    cut_words = shift_words(fixed, start, end)

    face = detect_facecam(trimmed) if layout in ("auto", "stack") else None
    box, focus = face if face else (None, None)
    chosen = "stack" if box else ("blur" if layout in ("auto", "stack") else layout)

    meta_raw = json.loads(clip.with_suffix(".json").read_text()) if clip.with_suffix(".json").exists() else {}
    score = (meta_raw.get("judge") or {}).get("hype") or (meta_raw.get("score") or {}).get("total") or 0
    use_chat = chat == "on" or (chat == "auto" and score >= chat_min_score)
    events = chat_events_for(meta_raw, t, start, end) if use_chat else []

    src, hook_at = trimmed, None
    if hook_s and end - start > hook_s * 4:
        hook_at = tease_start(loudest(trimmed, hook_s), hook_s, hook_lead)
        jd = meta_raw.get("judge") or {}
        if isinstance(jd.get("payoff_at"), (int, float)) and start <= jd["payoff_at"] <= end:
            # the judge saw the payoff in a frame; it began at most one frame step earlier. Look again with dense
            # frames for the moment of contact and end the tease right before it ("right before the disaster").
            from .judge import impact_time
            step = jd.get("frame_step") or 3.0
            hit = impact_time(clip, jd.get("payoff") or "the payoff", jd["payoff_at"] - step, jd["payoff_at"] + 0.4)
            tease_end = (hit - impact_lead) if hit is not None else (jd["payoff_at"] - step)
            hook_at = round(max(0.0, tease_end - start - hook_s), 2)
        src = cold_open(trimmed, hook_at, hook_s, out_dir / f"{stem}_hook.mp4")
        hook_words = [Word(round(w.start - hook_at, 3), round(w.end - hook_at, 3), w.text) for w in cut_words
                      if w.start >= hook_at - 0.05 and w.end <= hook_at + hook_s + 0.05]
        cut_words = hook_words + [Word(w.start + hook_s, w.end + hook_s, w.text) for w in cut_words]
        events = [{**m, "show": m["show"] + hook_s, "hide": m["hide"] + hook_s} for m in events]
    short = make_short(src, out_dir / f"{stem}_short.mp4", t["thumbnail_text"], chosen, box,
                       captions=captions, words_override=cut_words, chat_events=events,
                       logo=logo_path(campaign), title_until=(hook_s + banner_s) if hook_at is not None else None)
    thumbs = make_thumbnails(trimmed, ThumbSpec(t["thumbnail_text"], "16:9", box,
                                                face_focus=focus or (0.5, 0.3)), variants=2, out_dir=out_dir)

    result = {"source": str(clip), "short": str(short), "thumbnails": [str(p) for p in thumbs],
              "window": [start, end], "layout": chosen, "facecam": box, "titles": t,
              "score": meta_raw.get("score"), "event": meta_raw.get("event"),
              "caption_fixes": t.get("caption_fixes"), "chat_overlay": events, "hook_at": hook_at,
              "campaign": (campaign or {}).get("name")}
    Path(short).with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
