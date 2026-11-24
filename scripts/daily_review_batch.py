"""Scan finished Twitch VODs and prepare one day's clips for owner review."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from clipfarm import clip_window, cut_vod, detect_offline, detector_config, load_chat_json, load_config
from clipfarm import post as P
from clipfarm.campaigns import route
from clipfarm.catchup import Vod, finished_vods, full_chat
from clipfarm.judge import final_score, judge
from clipfarm.make import make
from clipfarm.review import (BATCHES, MUTED_MAX, PREVIEWS, make_preview, muted_share, review_doc,
                             source_metadata, write_json)

SCENE_S = 180


@dataclass(frozen=True)
class Candidate:
    clip: Path
    streamer: str
    vod: str | None
    spike: float | None
    verdict: dict

    @property
    def hype(self) -> int:
        return self.verdict["hype"]


def active_channels(channels: list[dict], today: str) -> list[dict]:
    return [c for c in channels if c.get("platform", "twitch") == "twitch"
            and (not c.get("until") or str(c["until"]) >= today)]


def scan_plan(channels: list[dict], scanned: dict[str, str], game: str | None,
              limit: int, min_vod: int = 0) -> list[tuple[dict, Vod]]:
    if limit <= 0:
        return []
    match = re.compile(game, re.IGNORECASE) if game else None
    archives = deque()
    for channel in channels:
        try:
            vods = finished_vods(channel["name"], n=None)
        except (subprocess.CalledProcessError, RuntimeError, ValueError) as exc:
            print(f"{channel['name']}: VOD discovery failed ({exc})", file=sys.stderr)
            continue
        eligible = deque(vod for vod in vods if vod.id not in scanned and int(vod.id) >= min_vod
                         and (not match or match.search(vod.title)))
        if eligible:
            archives.append((channel, eligible))
    plan, seen = [], set(scanned)
    while archives and len(plan) < limit:
        channel, vods = archives.popleft()
        vod = vods.popleft()
        if vod.id not in seen:
            plan.append((channel, vod))
            seen.add(vod.id)
        if vods:
            archives.append((channel, vods))
    return plan


def scan_vod(channel: dict, vod: Vod, cfg: dict) -> tuple[int, int, int]:
    streamer = channel["name"]
    out = Path(cfg.get("output_dir", "clips")) / streamer / "catchup" / vod.id
    chat_file = out / f"chat_{vod.id}_{int(vod.duration)}.json"
    if not chat_file.is_file():
        full_chat(vod.url, vod.duration, chat_file)
    msgs = load_chat_json(chat_file)
    clip_cfg = cfg["clip"]
    det_cfg = detector_config({**cfg["detector"], **channel.get("detector", {})})
    events = sorted(detect_offline(msgs, streamer, det_cfg), key=lambda e: -max(e.ratio / 3, e.wave_share / 0.3))
    _, post = clip_window(clip_cfg)
    settings = cfg["calibration"]
    pre = settings.get("vod_pre_s", 45)
    cut_vod(vod.url, events[:settings["spikes_per_vod"]], pre, post, out, msgs)
    judged = errors = 0
    for clip in sorted(out.glob("*_s*.mp4")):
        meta = source_metadata(clip)
        signal = (meta.get("score") or {}).get("total", 0)
        if meta.get("judge") or meta.get("judge_error") or meta.get("muted") or signal < clip_cfg.get("min_score", 35):
            continue
        muted = muted_share(clip)
        if muted >= MUTED_MAX:  # Twitch muted this VOD part: never judge or review it
            meta["muted"] = round(muted, 2)
            write_json(clip.with_suffix(".json"), meta)
            continue
        try:
            meta["judge"] = final_score(judge(clip, meta), meta.get("chat_timed") or [])
            judged += 1
        except (Exception, SystemExit) as exc:  # Record the judge's missing-key exit like any other failure.
            meta["judge_error"] = str(exc)[:300]
            errors += 1
        write_json(clip.with_suffix(".json"), meta)
    return len(events), judged, errors


def candidate(clip: Path, streamer: str, meta: dict) -> Candidate:
    vod = (meta.get("vod") or "").rstrip("/").rsplit("/", 1)[-1]
    if not vod and clip.parent.name.isdecimal() and clip.parent.parent.name == "catchup":
        vod = clip.parent.name
    spike = (meta.get("event") or {}).get("ts")
    return Candidate(clip, streamer, vod or None, spike, meta.get("judge") or {})


def existing_candidates(output_dir: Path, channels: list[dict]) -> list[Candidate]:
    found = []
    for channel in channels:
        streamer = channel["name"]
        catchup = output_dir / streamer / "catchup"
        paths = list(catchup.glob("*_s*.mp4"))
        paths.extend(p for p in catchup.glob("*/*_s*.mp4") if p.parent.name.isdecimal())
        for clip in paths:
            meta = source_metadata(clip)
            if not meta.get("judge_error") and not meta.get("muted") and (meta.get("judge") or {}).get("hype") is not None:
                found.append(candidate(clip, streamer, meta))
    return found


def queued_candidates(root: Path) -> list[Candidate]:
    used = []
    for state in P.STATES:
        if not (root / state).is_dir():
            continue
        for _, item in P.items(root, state):
            if item.source_clip:
                used.append(candidate(Path(item.source_clip), item.streamer, source_metadata(item.source_clip)))
    return used


def select_candidates(candidates: list[Candidate], queued: list[Candidate], min_hype: int,
                      limit: int) -> list[Candidate]:
    if limit <= 0:
        return []
    paths = {c.clip.resolve() for c in queued}
    scenes: dict[str, list[float]] = {}
    for c in queued:
        if c.vod and c.spike is not None:
            scenes.setdefault(c.vod, []).append(c.spike)
    selected = []
    for c in sorted(candidates, key=lambda c: (-c.hype, str(c.clip))):
        if c.hype < min_hype:
            break
        source = c.clip.resolve()
        if source in paths:
            continue
        if c.vod and c.spike is not None:
            spikes = scenes.setdefault(c.vod, [])
            if any(abs(c.spike - spike) <= SCENE_S for spike in spikes):
                continue
            spikes.append(c.spike)
        paths.add(source)
        selected.append(c)
        if len(selected) == limit:
            break
    return selected


def queue_candidate(c: Candidate, cfg: dict, root: Path, today: str, docs: Path) -> None:
    pcfg = {k: v for k, v in cfg.get("post", {}).items() if k != "auto_approve_min_score"}
    campaign = route(c.streamer, cfg)
    result = make(c.clip, c.streamer, pcfg.get("context", "GTA 6 livestream"),
                  out_dir=c.clip.parent / "shorts", campaign=campaign)
    titles, short = result["titles"], Path(result["short"])
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    with (TemporaryDirectory(dir=PREVIEWS, prefix=".batch-") as tmp_preview,
          TemporaryDirectory(dir=root, prefix=".batch-") as tmp_queue):
        preview = Path(tmp_preview) / "preview.mp4"
        make_preview(short, preview)
        # Stage the queue item until its preview and review doc are ready.
        staging = Path(tmp_queue)
        item = P.add(staging, short, titles["youtube_titles"][0], c.streamer, pcfg, score=c.hype,
                     source_clip=str(c.clip), titles=titles, campaign=campaign)
        duplicate = P.find_duplicate(root, item.video_hash, item.source_clip)
        if duplicate:
            raise P.DuplicateItem(f"already {duplicate[0]}: {duplicate[1].id}")
        final = PREVIEWS / f"{item.id}.mp4"
        doc_path = docs / f"{item.id}.json"
        dest = P.queue_dir(root, "pending") / item.id
        try:
            preview.replace(final)
            write_json(doc_path, review_doc(item, pcfg, "pending", today, final, c.verdict))
            (staging / "pending" / item.id).rename(dest)
        except BaseException:
            if not dest.exists():
                doc_path.unlink(missing_ok=True)
                final.unlink(missing_ok=True)
            raise


def load_state(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {"scanned_vods": {}, "batches": []}


def execute(cfg: dict, channels: list[dict], root: Path, state_path: Path, today: str, from_existing: bool) -> None:
    load_dotenv(Path.cwd() / ".env")
    state = load_state(state_path)
    settings = cfg["calibration"]
    summary = {"date": today, "vods_scanned": 0, "spikes": 0, "judged": 0, "judge_errors": 0,
               "queued": 0, "scan_errors": 0, "queue_errors": 0}
    if not from_existing:
        scanned = state["scanned_vods"]
        limit = settings["max_vods_per_day"] - sum(day == today for day in scanned.values())
        for channel, vod in scan_plan(channels, scanned, cfg["clip"].get("game"), limit,
                                         int(settings.get("skip_vods_before", 0))):
            print(f"{channel['name']} {vod.id}  {vod.title}", flush=True)
            try:
                spikes, judged, errors = scan_vod(channel, vod, cfg)
            except (subprocess.CalledProcessError, OSError, ValueError) as exc:
                print(f"{channel['name']} {vod.id}: scan failed ({exc})", file=sys.stderr)
                summary["scan_errors"] += 1
                continue
            summary["vods_scanned"] += 1
            summary["spikes"] += spikes
            summary["judged"] += judged
            summary["judge_errors"] += errors
            scanned[vod.id] = today
            write_json(state_path, state)
    docs = BATCHES / today / "docs"
    candidates = existing_candidates(Path(cfg.get("output_dir", "clips")), channels)
    histogram = Counter(c.hype // 10 * 10 for c in candidates)
    summary["hype_histogram"] = {f"{low}–{low + 9}" if low < 100 else "100": histogram[low]
                                 for low in sorted(histogram)}
    previous = sum(b["queued"] for b in state["batches"] if b["date"] == today)
    already_queued = max(previous, sum(1 for _ in docs.glob("*.json")))
    selected = select_candidates(candidates, queued_candidates(root), settings["min_hype"],
                                 settings["per_day"] - already_queued)
    for c in selected:
        try:
            queue_candidate(c, cfg, root, today, docs)
        except (Exception, SystemExit) as exc:  # One bad render should not stop the owner's batch.
            print(f"{c.clip}: could not prepare review ({exc})", file=sys.stderr)
            summary["queue_errors"] += 1
        else:
            summary["queued"] += 1
    state["batches"].append(summary)
    write_json(state_path, state)
    print(f"{today}: {summary['vods_scanned']} VODs scanned, {summary['spikes']} spikes, {summary['judged']} judged, "
          f"{summary['queued']} queued. Hype histogram: {summary['hype_histogram']}. "
          f"Errors (scan/judge/queue): {summary['scan_errors']}/{summary['judge_errors']}/{summary['queue_errors']}.")


def main(argv: list[str] | None = None, *, today: str | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="list eligible VODs without downloading or writing files")
    ap.add_argument("--from-existing", action="store_true", help="batch already judged catchup clips")
    args = ap.parse_args(argv)
    cfg = load_config(Path("channels.toml"))
    settings = cfg["calibration"]
    today = today or date.today().isoformat()
    if today > settings["until"]:
        print(f"Calibration ended on {settings['until']}.")
        return

    channels = active_channels(cfg.get("channel", []), today)
    root = Path(cfg.get("post", {}).get("queue_dir", "queue"))
    state_path = root / "calibration_state.json"
    if args.dry_run:
        scanned = load_state(state_path)["scanned_vods"]
        limit = settings["max_vods_per_day"] - sum(day == today for day in scanned.values())
        plan = [] if args.from_existing else scan_plan(channels, scanned, cfg["clip"].get("game"), limit,
                                         int(settings.get("skip_vods_before", 0)))
        print(f"{today}: would scan {len(plan)} finished VOD(s).")
        for channel, vod in plan:
            print(f"  {channel['name']}  {vod.url}  {vod.title}")
        return
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".calibration.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A daily review batch is already running.") from None
        execute(cfg, channels, root, state_path, today, args.from_existing)


if __name__ == "__main__":
    main()
