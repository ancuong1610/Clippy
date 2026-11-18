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


def make(clip: Path, streamer: str | None = None, context: str = "GTA 6 livestream",
         layout: str = "auto", captions: str = "karaoke", out_dir: Path | None = None) -> dict:
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

    short = make_short(trimmed, out_dir / f"{stem}_short.mp4", t["thumbnail_text"], chosen, box,
                       captions=captions, words_override=cut_words)
    thumbs = make_thumbnails(trimmed, ThumbSpec(t["thumbnail_text"], "16:9", box,
                                                face_focus=focus or (0.5, 0.3)), variants=2, out_dir=out_dir)

    meta_raw = json.loads(clip.with_suffix(".json").read_text()) if clip.with_suffix(".json").exists() else {}
    result = {"source": str(clip), "short": str(short), "thumbnails": [str(p) for p in thumbs],
              "window": [start, end], "layout": chosen, "facecam": box, "titles": t,
              "score": meta_raw.get("score"), "event": meta_raw.get("event"),
              "caption_fixes": t.get("caption_fixes")}
    Path(short).with_suffix(".json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
