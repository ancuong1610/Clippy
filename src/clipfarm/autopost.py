"""Unattended posting for the live run.

  live spike -> clip (hype >= auto_post_min_score) -> make -> safety gate -> queue/approved/
  rolling poster (every minute): if allowed, upload the BEST waiting highlight -> YouTube

Posting looks human, not like a bot: at least `auto_post_min_gap_min` between uploads, at most
`auto_post_max_per_hour` and `auto_post_max_per_day` (rolling, counting every upload incl. manual ones),
and a 6 h pause when YouTube reports the channel's daily upload limit.
To not look like a bot: uploads only inside `auto_post_window` (local time, e.g. "00:30-03:30" = US evening) and
each one waits a random extra 0..`auto_post_jitter_min` minutes, so posts never land on a fixed clock.
Clips the AI flags (music, sexual, hate, off-topic, ...) land in queue/pending/ for a human.
One clip is edited at a time so two streams spiking together don't overload the machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from pathlib import Path

from . import post as P

log = logging.getLogger("clipfarm")


def is_upload_limit(error) -> bool:
    e = str(error or "").lower()
    return ("upload limit" in e or "exceeded the number of videos" in e or "uploadlimitexceeded" in e
            or "video uploads per day" in e or "quotaexceeded" in e or "quota exceeded" in e)


_STAMP = re.compile(r"(\d{8}-\d{6})")


def moment_ts(item) -> float | None:
    """When the moment happened (live clips are named <YYYYmmdd-HHMMSS>_...)."""
    m = _STAMP.search(Path(item.source_clip or "").name)
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))


def parse_window(spec: str | None) -> tuple[int, int] | None:
    """"00:30-03:30" -> (30, 210) minutes after midnight; may cross midnight ("22:00-02:00")."""
    if not spec:
        return None
    a, b = (int(h) * 60 + int(m) for h, m in (x.strip().split(":") for x in spec.split("-")))
    return a, b


def in_window(window: tuple[int, int] | None, now: float) -> bool:
    if not window:
        return True
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    a, b = window
    return a <= m < b if a <= b else (m >= a or m < b)


def slot_window(slots: list[str], length_min: float, now: float) -> tuple[str, tuple[int, int]]:
    """Time-slot test: each posting day uses the next slot in turn (a day runs 06:00-06:00 local, so a 01:00 slot
    belongs to the evening before). Returns (slot, window)."""
    t = time.localtime(now - 6 * 3600)
    day = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) // 86400)
    slot = slots[day % len(slots)]
    h, m = (int(x) for x in slot.split(":"))
    a = h * 60 + m
    return slot, (a, int(a + length_min) % 1440)


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
        self.cfg = cfg
        self.pcfg = {k: v for k, v in cfg.get("post", {}).items() if k != "auto_approve_min_score"}
        self.min_score = self.pcfg.get("auto_post_min_score")
        self.max_per_hour = int(self.pcfg.get("auto_post_max_per_hour", 3))
        self.max_per_day = int(self.pcfg.get("auto_post_max_per_day", 12))
        self.min_gap_s = float(self.pcfg.get("auto_post_min_gap_min", 20)) * 60
        self.scene_s = float(self.pcfg.get("auto_post_scene_min", 3)) * 60  # one clip per scene
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
        self.window = parse_window(self.pcfg.get("auto_post_window"))
        self.slots = list(self.pcfg.get("auto_post_slots", []))  # time-slot test; overrides auto_post_window
        self.slot_len = float(self.pcfg.get("auto_post_slot_min", 60))
        self.jitter_s = float(self.pcfg.get("auto_post_jitter_min", 0)) * 60
        self.next_at: float | None = None  # random earliest time for the next upload

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
        if self.slots:
            slot, win = slot_window(self.slots, self.slot_len, now)
            if not in_window(win, now):
                return f"outside today's test slot ({slot}, {self.slot_len:.0f} min)"
        elif not in_window(self.window, now):
            return f"outside posting window ({self.pcfg.get('auto_post_window')})"
        if self.posted and now - self.posted[-1] < self.min_gap_s:
            return f"spacing ({self.min_gap_s / 60:.0f} min between posts)"
        if sum(now - t <= 3600 for t in self.posted) >= self.max_per_hour:
            return f"rate limit ({self.max_per_hour}/hour)"
        # slot test: the cap is per posting day (one slot), not a rolling 24 h, so slot 01:00 -> 19:00 isn't blocked
        recent = (sum(now - t <= (self.slot_len + 60) * 60 for t in self.posted) if self.slots else len(self.posted))
        if recent >= self.max_per_day:
            return f"daily cap ({self.max_per_day}/24 h)"
        if self.next_at is None:  # allowed now: pick a random moment within the jitter, then post
            self.next_at = now + random.uniform(0, self.jitter_s)
        if now < self.next_at:
            return f"random delay until {time.strftime('%H:%M', time.localtime(self.next_at))}"
        return None

    def rate_ok(self, now: float | None = None) -> bool:
        return self.wait_reason(now) is None

    def decide(self, titles: dict) -> str | None:
        """None = safe to post unattended; otherwise the reason it goes to human review."""
        risks = titles.get("autopost_risks")
        if risks is None:
            return "risk flagged: unchecked"
        risks = [r for r in risks if r != "music"]  # owner: music is fine (lyrics are never captioned)
        if risks:
            return f"risk flagged: {', '.join(risks)}"
        return None

    def _upload(self, item_id: str, streamer: str, title: str) -> dict:
        """Upload one approved item. On YouTube's daily limit: pause (persisted) and keep it in approved/."""
        res = P.post_composio(self.root, self.pcfg, only=item_id)[0]
        yt = res["platforms"].get("youtube", {})
        done = {p: v for p, v in res["platforms"].items() if v.get("ok")}
        if done:  # item is in posted/ now; a failed platform got a manual caption pack
            self.posted.append(time.time())
            self.next_at = None
            log.info("[%s] 🚀 posted (%s): %s  %s", streamer, ", ".join(done), title, yt.get("url") or "")
            return {"posted": True, "url": yt.get("url"), "platforms": list(done)}
        if is_upload_limit(yt.get("error")):
            self.paused_until = time.time() + 6 * 3600  # YouTube's window is a rolling 24 h; retry in 6 h
            self.pause_file.write_text(str(self.paused_until))
            P.move(self.root, item_id, "failed", "approved")
            log.error("[%s] ⛔ YouTube daily upload limit — uploads paused 6 h; edited clips wait in queue/approved/",
                      streamer)
            return {"posted": False, "reason": "YouTube daily upload limit"}
        log.error("[%s] upload failed (%s) — item in queue/failed/%s", streamer, yt.get("error"), item_id)
        return {"posted": False, "error": yt.get("error")}

    def dedupe_scenes(self) -> list[str]:
        """One clip per scene: highlights of the same streamer within `scene_s` of each other (a long funny scene
        makes chat spike again and again) -> keep the best, move the rest to queue/skipped/. A scene that was
        already posted is never posted again."""
        posted = [it for _, it in P.items(self.root, "posted")]
        waiting = sorted((it for _, it in P.items(self.root, "approved")),
                         key=lambda it: (-(it.score or 0), it.created))
        kept, skipped = [], []

        def same_scene(a, b) -> bool:
            ta, tb = moment_ts(a), moment_ts(b)
            return a.streamer == b.streamer and ta is not None and tb is not None and abs(ta - tb) <= self.scene_s

        for it in waiting:
            other = next((o for o in posted + kept if same_scene(it, o)), None)
            if other:
                P.move(self.root, it.id, "approved", "skipped")
                skipped.append(it.id)
                log.info("[%s] ⤼ skipped (same scene as %s, hype %s): %s", it.streamer, other.id, other.score,
                         it.title)
            else:
                kept.append(it)
        return skipped

    def post_next(self, now: float | None = None) -> dict | None:
        """Upload the single best waiting highlight if the spacing/caps allow it right now."""
        if self.wait_reason(now):
            return None
        self.dedupe_scenes()
        waiting = sorted(P.items(self.root, "approved"), key=lambda x: (-(x[1].score or 0), x[1].created))
        if not waiting:
            return None
        it = waiting[0][1]
        log.info("[%s] ⏫ posting best of %d waiting (hype %s): %s", it.streamer, len(waiting), it.score, it.title)
        out = self._upload(it.id, it.streamer, it.title)
        with (self.root / "autopost.jsonl").open("a") as f:
            slot = slot_window(self.slots, self.slot_len, time.time())[0] if self.slots else None
            f.write(json.dumps({"ts": time.time(), "streamer": it.streamer, "score": it.score, "id": it.id,
                                "title": it.title, "slot": slot, **out}, ensure_ascii=False) + "\n")
        return out

    async def run_poster(self, every_s: float = 60) -> None:
        """Rolling poster for the live run: one upload at a time, best first, spaced out."""
        last_clean = last_insights = 0.0
        while True:
            await asyncio.sleep(every_s)
            if self.cfg.get("cleanup", {}).get("auto") and time.time() - last_clean > 3600:
                last_clean = time.time()
                try:
                    from .cleanup import run as cleanup
                    acts, freed = await asyncio.to_thread(cleanup, self.cfg, True)
                    if acts:
                        log.info("🧹 cleanup: %d files, freed %.2f GB", len(acts), freed / 1e9)
                except Exception as e:  # noqa: BLE001 — never kill the live run
                    log.exception("cleanup failed: %s", e)
            if self.cfg.get("insights", {}).get("auto") and time.time() - last_insights > 86400:
                last_insights = time.time()
                try:
                    from . import insights as I
                    await asyncio.to_thread(I.collect, self.cfg)
                    out = await asyncio.to_thread(I.write_report, self.cfg)
                    log.info("📊 insights: %s", out)
                except Exception as e:  # noqa: BLE001 — never kill the live run
                    log.exception("insights failed: %s", e)
            if self.wait_reason():
                continue
            async with self.lock:
                try:
                    await asyncio.to_thread(self.post_next)
                except Exception as e:  # noqa: BLE001 — never kill the live run
                    log.exception("rolling poster failed: %s", e)

    def process(self, clip: Path, streamer: str, score: int) -> dict:
        from .campaigns import route
        from .make import make
        camp = route(streamer, self.cfg)
        log.info("[%s] ⚡ hype %d >= %d: editing (campaign: %s)", streamer, score, self.min_score,
                 (camp or {}).get("name", "none"))
        r = make(clip, streamer, self.context, out_dir=clip.parent / "shorts", campaign=camp)
        t = r["titles"]
        item = P.add(self.root, Path(r["short"]), t["youtube_titles"][0], streamer, self.pcfg,
                     score=score, source_clip=str(clip), titles=t, campaign=camp)
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
