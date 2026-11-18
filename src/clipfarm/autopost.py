"""Unattended posting for the live run.

  live spike -> clip (hype >= auto_post_min_score) -> make -> safety gate -> queue/approved/
  rolling poster (every minute): if allowed, upload the BEST waiting highlight -> YouTube

Posting looks human, not like a bot: at least `auto_post_min_gap_min` between uploads, at most
`auto_post_max_per_hour` and `auto_post_max_per_day` (rolling, counting every upload incl. manual ones),
and a 6 h pause when YouTube reports the channel's daily upload limit.
Clips the AI flags (music, sexual, hate, off-topic, ...) land in queue/pending/ for a human.
One clip is edited at a time so two streams spiking together don't overload the machine.
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


def recent_posts(root: Path, within_s: float = 86400) -> list[float]:
    """Upload times of the last 24 h (queue/posted/*/result.json), so limits survive restarts."""
    now = time.time()
    out = []
    for f in (root / "posted").glob("*/result.json"):
        t = f.stat().st_mtime
        if now - t <= within_s:
            out.append(t)
    return sorted(out)


class AutoPoster:
    def __init__(self, cfg: dict):
        self.pcfg = {k: v for k, v in cfg.get("post", {}).items() if k != "auto_approve_min_score"}
        self.min_score = self.pcfg.get("auto_post_min_score")
        self.max_per_hour = int(self.pcfg.get("auto_post_max_per_hour", 3))
        self.max_per_day = int(self.pcfg.get("auto_post_max_per_day", 12))
        self.min_gap_s = float(self.pcfg.get("auto_post_min_gap_min", 20)) * 60
        self.context = self.pcfg.get("context", "GTA 6 livestream")
        self.root = Path(self.pcfg.get("queue_dir", "queue"))
        self.lock = asyncio.Lock()
        self.posted: deque[float] = deque(recent_posts(self.root))
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

    def wait_reason(self, now: float | None = None) -> str | None:
        """None = an upload is allowed right now; otherwise why not."""
        now = now or time.time()
        while self.posted and now - self.posted[0] > 86400:
            self.posted.popleft()
        if now < self.paused_until:
            return "YouTube daily upload limit (paused)"
        if self.posted and now - self.posted[-1] < self.min_gap_s:
            return f"spacing ({self.min_gap_s / 60:.0f} min between posts)"
        if sum(now - t <= 3600 for t in self.posted) >= self.max_per_hour:
            return f"rate limit ({self.max_per_hour}/hour)"
        if len(self.posted) >= self.max_per_day:
            return f"daily cap ({self.max_per_day}/24 h)"
        return None

    def rate_ok(self, now: float | None = None) -> bool:
        return self.wait_reason(now) is None

    def decide(self, titles: dict) -> str | None:
        """None = safe to post unattended; otherwise the reason it goes to human review."""
        risks = titles.get("autopost_risks")
        if risks is None or risks:
            return f"risk flagged: {', '.join(risks or ['unchecked'])}"
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

    def post_next(self, now: float | None = None) -> dict | None:
        """Upload the single best waiting highlight if the spacing/caps allow it right now."""
        if self.wait_reason(now):
            return None
        waiting = sorted(P.items(self.root, "approved"), key=lambda x: (-(x[1].score or 0), x[1].created))
        if not waiting:
            return None
        it = waiting[0][1]
        log.info("[%s] ⏫ posting best of %d waiting (hype %s): %s", it.streamer, len(waiting), it.score, it.title)
        out = self._upload(it.id, it.streamer, it.title)
        with (self.root / "autopost.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "streamer": it.streamer, "score": it.score, "id": it.id,
                                "title": it.title, **out}, ensure_ascii=False) + "\n")
        return out

    async def run_poster(self, every_s: float = 60) -> None:
        """Rolling poster for the live run: one upload at a time, best first, spaced out."""
        while True:
            await asyncio.sleep(every_s)
            if self.wait_reason():
                continue
            async with self.lock:
                try:
                    await asyncio.to_thread(self.post_next)
                except Exception as e:  # noqa: BLE001 — never kill the live run
                    log.exception("rolling poster failed: %s", e)

    def process(self, clip: Path, streamer: str, score: int) -> dict:
        from .make import make
        log.info("[%s] ⚡ hype %d >= %d: editing", streamer, score, self.min_score)
        r = make(clip, streamer, self.context, out_dir=clip.parent / "shorts")
        t = r["titles"]
        item = P.add(self.root, Path(r["short"]), t["youtube_titles"][0], streamer, self.pcfg,
                     score=score, source_clip=str(clip), titles=t)
        reason = self.decide(t)
        out = {"id": item.id, "title": item.title, "posted": False, "reason": reason}
        if reason:
            log.info("[%s] 👀 needs review (%s): %s  -> clipfarm queue approve %s", streamer, reason, item.title, item.id)
        else:
            P.move(self.root, item.id, "pending", "approved")  # the rolling poster picks the best one
            n = len(P.items(self.root, "approved"))
            log.info("[%s] ⏸ queued for posting (hype %d, %d waiting; %s): %s", streamer, score, n,
                     self.wait_reason() or "next poster tick", item.title)
        with (self.root / "autopost.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "streamer": streamer, "score": score, **out}, ensure_ascii=False) + "\n")
        return out
