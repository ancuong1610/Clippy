"""Cut a clip [t0, t1] (wall-clock epoch) out of the rolling segment buffer."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def pick_segments(segs: list[tuple[float, Path]], t0: float, t1: float,
                  segment_s: float) -> list[tuple[float, Path]]:
    picked = []
    for i, (start, p) in enumerate(segs):
        end = segs[i + 1][0] if i + 1 < len(segs) else start + segment_s
        if end > t0 and start < t1:
            picked.append((start, p))
    return picked


async def cut(segs: list[tuple[float, Path]], t0: float, t1: float, out: Path,
              segment_s: float) -> Path | None:
    picked = pick_segments(segs, t0, t1, segment_s)
    if not picked:
        log.warning("no buffered video covers %.0f-%.0f", t0, t1)
        return None
    offset = max(0.0, t0 - picked[0][0])
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for _, p in picked:
            f.write(f"file '{p.resolve()}'\n")
        listfile = f.name
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", listfile,
        "-ss", f"{offset:.2f}", "-t", f"{t1 - t0:.2f}",
        "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart",
        str(out), stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    Path(listfile).unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        log.error("ffmpeg cut failed: %s", err.decode(errors="replace")[-500:])
        return None
    return out
