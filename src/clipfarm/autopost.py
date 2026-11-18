"""Unattended posting for the live run: highlight clips go straight to YouTube.

  live spike -> clip (score >= auto_post_min_score) -> make -> safety gate -> rate limit -> upload

Clips the AI flags (music, sexual, hate, off-topic, ...) or that miss the AI check land in queue/pending/ for a
human. Safe clips over the hourly cap or during a YouTube upload-limit pause wait in queue/approved/ and are
posted (best score first) as soon as uploads are allowed again.
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


def is_upload_limit(error) -> bool:
    e = str(error or "").lower()
    return "upload limit" in e or "exceeded the number of videos" in e or "uploadlimitexceeded" in e


class AutoPoster:
    def __init__(self, cfg: dict):
        self.pcfg = {k: v for k, v in cfg.get("post", {}).items() if k != "auto_approve_min_score"}
        self.min_score = self.pcfg.get("auto_post_min_score")
        self.max_per_hour = int(self.pcfg.get("auto_post_max_per_hour", 2))
        self.context = self.pcfg.get("context", "GTA 6 livestream")
        self.root = Path(self.pcfg.get("queue_dir", "queue"))
        self.lock = asyncio.Lock()
        self.posted: deque[float] = deque()
        # set when YouTube says the channel's daily upload limit is reached; survives restarts
        self.pause_file = self.root / "upload_paused_until"
        try:
            self.paused_until = float(self.pause_file.read_text())
        except (OSError, ValueError):
            self.paused_until = 0.0
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
        if (now or time.time()) < self.paused_until:
            return "YouTube daily upload limit (paused; post later with `clipfarm post`)"
        if not self.rate_ok(now):
            return f"rate limit ({self.max_per_hour}/hour)"
        return None

    def _upload(self, item_id: str, streamer: str, title: str) -> dict:
        """Upload one approved item. On YouTube's daily limit: pause (persisted) and keep it in approved/."""
        res = P.post_composio(self.root, self.pcfg, only=item_id)[0]
        yt = res["platforms"].get("youtube", {})
        if yt.get("ok"):
            self.posted.append(time.time())
            log.info("[%s] 🚀 posted: %s  %s", streamer, title, yt.get("url"))
            return {"posted": True, "url": yt.get("url")}
        if is_upload_limit(yt.get("error")):
            self.paused_until = time.time() + 6 * 3600  # YouTube's window is a rolling 24 h; retry in 6 h
            self.pause_file.write_text(str(self.paused_until))
            P.move(self.root, item_id, "failed", "approved")
            log.error("[%s] ⛔ YouTube daily upload limit — uploads paused 6 h; edited clips wait in queue/approved/",
                      streamer)
            return {"posted": False, "reason": "YouTube daily upload limit"}
        log.error("[%s] upload failed (%s) — item in queue/failed/%s", streamer, yt.get("error"), item_id)
        return {"posted": False, "error": yt.get("error")}

    def flush_backlog(self, skip: str | None = None) -> None:
        """Post clips approved while uploads were paused or capped: best score first, within the hourly cap."""
        for _, it in sorted(P.items(self.root, "approved"), key=lambda x: -(x[1].score or 0)):
            if it.id == skip:
                continue
            if time.time() < self.paused_until or not self.rate_ok():
                return
            log.info("[%s] ⏫ posting backlog: %s", it.streamer, it.title)
            self._upload(it.id, it.streamer, it.title)

    def process(self, clip: Path, streamer: str, score: int) -> dict:
        from .make import make
        log.info("[%s] ⚡ score %d >= %d: editing for auto-post", streamer, score, self.min_score)
        r = make(clip, streamer, self.context, out_dir=clip.parent / "shorts")
        t = r["titles"]
        item = P.add(self.root, Path(r["short"]), t["youtube_titles"][0], streamer, self.pcfg,
                     score=score, source_clip=str(clip), titles=t)
        reason = self.decide(t)
        out = {"id": item.id, "title": item.title, "posted": False, "reason": reason}
        waiting = bool(reason) and reason.startswith(("YouTube daily upload limit", "rate limit"))
        if waiting:
            P.move(self.root, item.id, "pending", "approved")  # passed the safety gate: post when allowed
            log.info("[%s] ⏸ approved, waiting (%s): %s (%s)", streamer, reason, item.title, item.id)
        elif reason:
            log.info("[%s] 👀 needs review (%s): %s  -> clipfarm queue approve %s", streamer, reason, item.title, item.id)
        else:
            P.move(self.root, item.id, "pending", "approved")
            out.update(self._upload(item.id, streamer, item.title))
        if not reason or waiting:
            self.flush_backlog(skip=item.id)
        with (self.root / "autopost.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "streamer": streamer, "score": score, **out}, ensure_ascii=False) + "\n")
        return out
