"""Rolling video buffer: streamlink -> ffmpeg segmenter -> buffer/<channel>/seg_<epoch>.ts"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)


def venv_bin(name: str) -> str:
    """Prefer the tool installed next to this Python (works without `uv run` / activated venv)."""
    import shutil
    import sys
    p = Path(sys.executable).parent / name
    return str(p) if p.exists() else (shutil.which(name) or name)


async def stream_info(url: str, timeout: float = 45) -> dict | None:
    """streamlink metadata {category, title, author} of a live stream; None when offline/unreachable."""
    try:
        p = await asyncio.create_subprocess_exec(venv_bin("streamlink"), "--json", url,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        d = json.loads(out or b"{}")
    except (asyncio.TimeoutError, ValueError, OSError):
        return None
    return None if "error" in d else (d.get("metadata") or {})


def plays(info: dict, game: str | None) -> bool:
    """Does the stream play the wanted game? Checks category + title (YouTube's category is just 'Gaming')."""
    if not game:
        return True
    cat = (info.get("category") or "").strip()
    if cat.lower() in ("", "gaming"):  # YouTube: category is generic -> trust the title
        return bool(re.search(game, info.get("title") or "", re.I))
    return bool(re.search(game, cat, re.I))  # Twitch: the real game category decides


class Recorder:
    def __init__(self, name: str, url: str, buffer_dir: Path, quality: str = "best",
                 segment_s: int = 10, keep_s: int = 900, game: str | None = None):
        self.name, self.url, self.quality, self.game = name, url, quality, game
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

    async def _game_guard(self, sl) -> None:
        """While recording, re-check the game every 10 min; stop if the streamer switched away."""
        while sl.returncode is None:
            await asyncio.sleep(600)
            info = await stream_info(self.url)
            if info is not None and not plays(info, self.game):
                log.info("[%s] switched to %r — stop recording", self.name, info.get("category"))
                sl.terminate()
                return

    async def run(self) -> None:
        while True:
            sl = ff = guard = None
            if self.game:
                info = await stream_info(self.url)
                if info is None:
                    await asyncio.sleep(60)  # offline: check again in a minute
                    continue
                if not plays(info, self.game):
                    log.info("[%s] live but not the game (%r / %.40s) — skip, re-check in 5 min",
                             self.name, info.get("category"), info.get("title") or "")
                    await asyncio.sleep(300)
                    continue
            try:
                sl = await asyncio.create_subprocess_exec(
                    venv_bin("streamlink"), "--stdout", "--twitch-low-latency", "--twitch-disable-ads",
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
                if self.game:
                    guard = asyncio.create_task(self._game_guard(sl))
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
                if guard:
                    guard.cancel()
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
