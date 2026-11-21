"""Disk cleanup: drop the video of clips that are done (posted) or will never be posted (skipped / rejected).

Rules (days come from [cleanup] in channels.toml):
- posted/<id>: after `posted_days` (time for the manual TikTok/Instagram post) delete short.mp4 + thumbnails.
- skipped/<id>: after `skipped_days` delete short.mp4 + thumbnails.
- the source clip of a posted/skipped item is SHRUNK to 640 px wide, not deleted: the owner has seen it, so it is a
  labelled example scripts/backtest_judge.py can re-judge (the judge only looks at 640 px frames). Its rendered
  Shorts/thumbnails in clips/<streamer>/shorts*/ are deleted.
- raw clips that never entered the queue (below the bar): after `raw_days` delete the .mp4 and its renders.
Every .json / .words.json stays (stats, dedupe, judge verdicts, titles). approved/, pending/ and failed/ are never
touched, nor any source clip an approved/pending item still uses, nor clips/benchmark/.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SHRINK_W = 640
DONE = ("posted", "skipped")
LIVE = ("approved", "pending", "failed")


@dataclass
class Action:
    kind: str  # "delete" | "shrink"
    path: Path
    size: int
    why: str


def _width(p: Path) -> int:
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width", "-of", "csv=p=0", str(p)], text=True)
        return int(out.strip().splitlines()[0])
    except (subprocess.CalledProcessError, ValueError, IndexError):
        return 0


def _renders(src: Path) -> list[Path]:
    """Rendered Shorts / thumbnails made from this source clip (json kept)."""
    out = []
    for d in src.parent.glob("shorts*"):
        out += [p for p in d.glob(f"{src.stem}_*") if p.suffix in (".mp4", ".jpg", ".png")]
    return out


def _age_days(p: Path, now: float) -> float:
    return (now - p.stat().st_mtime) / 86400


def plan(queue: Path, clips: Path, posted_days: float = 3, skipped_days: float = 1, raw_days: float = 2,
         now: float | None = None) -> list[Action]:
    now = now or time.time()
    acts: list[Action] = []
    sources: dict[Path, set[str]] = {}  # source clip -> queue states that use it
    items = []
    for state in DONE + LIVE:
        for pj in (queue / state).glob("*/post.json"):
            src = (json.loads(pj.read_text()).get("source_clip") or "").strip()
            if src:
                sources.setdefault(Path(src).resolve(), set()).add(state)
            items.append((state, pj.parent, Path(src).resolve() if src else None))

    def drop(p: Path, why: str) -> None:
        if p.exists():
            acts.append(Action("delete", p, p.stat().st_size, why))

    shrink: set[Path] = set()
    for state, d, src in items:
        if state not in DONE:
            continue
        days = posted_days if state == "posted" else skipped_days
        stamp = d / ("result.json" if (d / "result.json").exists() else "post.json")
        if _age_days(stamp, now) < days:
            continue
        for p in [d / "short.mp4", *d.glob("thumb*")]:
            drop(p, f"{state} > {days:g}d")
        if src and src.exists() and not (sources[src] & set(LIVE)):
            shrink.add(src)
    for src in sorted(shrink):
        for p in _renders(src):
            drop(p, "render of a finished clip")
        if _width(src) > SHRINK_W:
            acts.append(Action("shrink", src, src.stat().st_size, f"labelled example -> {SHRINK_W}px"))

    for p in sorted(clips.rglob("*.mp4")):
        if p.parent.name.startswith("shorts") or "benchmark" in p.parts or p.resolve() in sources:
            continue
        if p.parent.name == "low" or _age_days(p, now) < raw_days:
            continue
        drop(p, f"never queued > {raw_days:g}d")
        for r in _renders(p):
            drop(r, "render of a never-queued clip")
    return acts


def apply(acts: list[Action]) -> int:
    """Run the plan; returns bytes freed."""
    freed = 0
    for a in acts:
        if not a.path.exists():
            continue
        if a.kind == "delete":
            a.path.unlink()
            freed += a.size
            continue
        tmp = a.path.with_name(a.path.stem + ".small.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a.path), "-vf", f"scale={SHRINK_W}:-2",
                        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "64k",
                        str(tmp)], check=True)
        freed += a.size - tmp.stat().st_size
        tmp.replace(a.path)
    return freed


def run(cfg: dict, do_apply: bool = False) -> tuple[list[Action], int]:
    c = cfg.get("cleanup", {})
    queue = Path(cfg.get("post", {}).get("queue_dir", "queue"))
    acts = plan(queue, Path(c.get("clips_dir", "clips")), c.get("posted_days", 3), c.get("skipped_days", 1),
                c.get("raw_days", 2))
    return acts, (apply(acts) if do_apply else 0)
