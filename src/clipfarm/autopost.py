"""Unattended posting for the live run: highlight clips go straight to YouTube.

  live spike -> clip (score >= auto_post_min_score) -> make -> safety gate -> rate limit -> upload

Anything that fails a gate is still edited and lands in queue/pending/ for a human, with the reason logged.
Gates: AI-flagged risks (music, sexual, hate, ...), a missing/garbled AI check, and max posts per hour.
One clip is processed at a time so two streams spiking together don't overload the machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

from . import post as P

log = logging.getLogger("clipfarm")


class AutoPoster:
    def __init__(self, cfg: dict):
        self.pcfg = {k: v for k, v in cfg.get("post", {}).items() if k != "auto_approve_min_score"}
        self.min_score = self.pcfg.get("auto_post_min_score")
        self.max_per_hour = int(self.pcfg.get("auto_post_max_per_hour", 2))
        self.context = self.pcfg.get("context", "GTA 6 livestream")
        self.root = Path(self.pcfg.get("queue_dir", "queue"))
        self.lock = asyncio.Lock()
        self.posted: deque[float] = deque()
        self.tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self.min_score is not None

    def submit(self, clip: Path, streamer: str, score: int) -> None:
        if not self.enabled or score < self.min_score:
            return
        task = asyncio.create_task(self._handle(clip, streamer, score))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _handle(self, clip: Path, streamer: str, score: int) -> None:
        async with self.lock:
            try:
                await asyncio.to_thread(self.process, clip, streamer, score)
            except Exception as e:  # noqa: BLE001 — never kill the live run
                log.exception("[%s] auto-post failed for %s: %s", streamer, clip, e)

    def rate_ok(self, now: float | None = None) -> bool:
        now = now or time.time()
        while self.posted and now - self.posted[0] > 3600:
            self.posted.popleft()
        return len(self.posted) < self.max_per_hour

    def decide(self, titles: dict, now: float | None = None) -> str | None:
        """None = post it; otherwise the reason it goes to human review."""
        risks = titles.get("autopost_risks")
        if risks is None or risks:
            return f"risk flagged: {', '.join(risks or ['unchecked'])}"
        if not self.rate_ok(now):
            return f"rate limit ({self.max_per_hour}/hour)"
        return None

    def process(self, clip: Path, streamer: str, score: int) -> dict:
        from .make import make
        log.info("[%s] ⚡ score %d >= %d: editing for auto-post", streamer, score, self.min_score)
        r = make(clip, streamer, self.context, out_dir=clip.parent / "shorts")
        t = r["titles"]
        item = P.add(self.root, Path(r["short"]), t["youtube_titles"][0], streamer, self.pcfg,
                     score=score, source_clip=str(clip), titles=t)
        reason = self.decide(t)
        out = {"id": item.id, "title": item.title, "posted": False, "reason": reason}
        if reason:
            log.info("[%s] 👀 needs review (%s): %s  -> clipfarm queue approve %s", streamer, reason, item.title, item.id)
        else:
            P.move(self.root, item.id, "pending", "approved")
            res = P.post_composio(self.root, self.pcfg, only=item.id)[0]
            yt = res["platforms"].get("youtube", {})
            out.update(posted=bool(yt.get("ok")), url=yt.get("url"), error=yt.get("error"))
            if yt.get("ok"):
                self.posted.append(time.time())
                log.info("[%s] 🚀 posted: %s  %s", streamer, item.title, yt.get("url"))
            else:
                log.error("[%s] upload failed (%s) — item in queue/failed/%s", streamer, yt.get("error"), item.id)
        with (self.root / "autopost.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "streamer": streamer, "score": score, **out}, ensure_ascii=False) + "\n")
        return out
