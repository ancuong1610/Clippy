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
from .titles import apply_fixes, make_titles

log = logging.getLogger(__name__)


def trim(src: Path, start: float, end: float, out: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{end - start:.2f}",
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
         chat: str = "auto", chat_min_score: int = 70) -> dict:
    out_dir = out_dir or clip.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    words_f = clip.with_suffix(".words.json")
    if not words_f.exists():
        words_f.write_text(json.dumps([w.__dict__ for w in transcribe(clip)], ensure_ascii=False))
    words = json.loads(words_f.read_text())

    t = make_titles(clip, streamer, context)                      # titles, hook, window, fixes
    fixed = apply_fixes(words, t.get("caption_fixes"))
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

    short = make_short(trimmed, out_dir / f"{stem}_short.mp4", t["thumbnail_text"], chosen, box,
                       captions=captions, words_override=cut_words, chat_events=events)
    thumbs = make_thumbnails(trimmed, ThumbSpec(t["thumbnail_text"], "16:9", box,
                                                face_focus=focus or (0.5, 0.3)), variants=2, out_dir=out_dir)

    result = {"source": str(clip), "short": str(short), "thumbnails": [str(p) for p in thumbs],
              "window": [start, end], "layout": chosen, "facecam": box, "titles": t,
              "score": meta_raw.get("score"), "event": meta_raw.get("event"),
              "caption_fixes": t.get("caption_fixes"), "chat_overlay": events}
    Path(short).with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
