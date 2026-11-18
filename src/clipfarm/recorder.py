"""Rolling video buffer: streamlink -> ffmpeg segmenter -> buffer/<channel>/seg_<epoch>.ts"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def venv_bin(name: str) -> str:
    """Prefer the tool installed next to this Python (works without `uv run` / activated venv)."""
    import shutil
    import sys
    p = Path(sys.executable).parent / name
    return str(p) if p.exists() else (shutil.which(name) or name)


class Recorder:
    def __init__(self, name: str, url: str, buffer_dir: Path, quality: str = "best",
                 segment_s: int = 10, keep_s: int = 900):
        self.name, self.url, self.quality = name, url, quality
        self.dir = buffer_dir / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.segment_s, self.keep_s = segment_s, keep_s
        self.live = False

    def segments(self) -> list[tuple[float, Path]]:
        """Sorted (start_epoch, path) of finished-or-growing segments."""
        out = []
        for p in self.dir.glob("seg_*.ts"):
            try:
                out.append((float(p.stem[4:]), p))
            except ValueError:
                continue
        return sorted(out)

    async def run(self) -> None:
        while True:
            sl = ff = None
            try:
                sl = await asyncio.create_subprocess_exec(
                    venv_bin("streamlink"), "--stdout", "--twitch-low-latency",
                    "--retry-open", "3", self.url, self.quality,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                ff = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                    "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                    "-f", "segment", "-segment_time", str(self.segment_s),
                    "-reset_timestamps", "1", "-strftime", "1",
                    str(self.dir / "seg_%s.ts"),
                    stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                log.info("[%s] recorder started (%s)", self.name, self.quality)
                first = True
                while True:
                    chunk = await sl.stdout.read(1 << 16)
                    if not chunk:
                        break
                    if first:
                        self.live, first = True, False
                        log.info("[%s] stream is LIVE, buffering", self.name)
                    ff.stdin.write(chunk)
                    await ff.stdin.drain()
                err = (await sl.stderr.read()).decode(errors="replace").strip().splitlines()
                log.info("[%s] stream ended/offline: %s", self.name, err[-1] if err else "no output")
            except (BrokenPipeError, ConnectionResetError) as e:
                log.warning("[%s] recorder pipe error: %s", self.name, e)
            finally:
                self.live = False
                if sl and sl.returncode is None:
                    sl.terminate()
                if ff and ff.returncode is None and ff.stdin:
                    ff.stdin.close()  # lets ffmpeg finalize the current segment
                for p in (sl, ff):
                    if p and p.returncode is None:
                        try:
                            await asyncio.shield(asyncio.wait_for(p.wait(), 5))
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            p.kill()
            await asyncio.sleep(60)  # offline: check again in a minute

    async def janitor(self) -> None:
        while True:
            cutoff = time.time() - self.keep_s
            for start, p in self.segments():
                if start < cutoff:
                    p.unlink(missing_ok=True)
            await asyncio.sleep(30)
