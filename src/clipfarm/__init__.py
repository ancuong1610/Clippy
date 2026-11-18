"""clipfarm — live chat-spike auto-clipper.

  clipfarm run [--config channels.toml] [--test-trigger SECONDS]
  clipfarm analyze CHAT.json [--pre 30 --post 8]   # offline: TwitchDownloader / chat-downloader JSON
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import time
import tomllib
from collections import deque
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .chat import CHAT_LAG_S, chat_source
from .clipper import cut
from .detector import ChatMessage, DetectorConfig, HypeEvent, SpikeDetector, detect_offline
from .labeler import label
from .recorder import Recorder, venv_bin
from .score import moment_score

log = logging.getLogger("clipfarm")


def load_config(path: Path) -> dict:
    cfg = tomllib.loads(path.read_text())
    cfg.setdefault("clip", {})
    cfg.setdefault("detector", {})
    return cfg


LENGTH_PRESETS = {"auto": (30, 8), "15-30": (20, 6), "30-60": (40, 12)}  # (pre_s, post_s)


def clip_window(clip: dict) -> tuple[float, float]:
    """Length preset sets pre/post; explicit pre_s / post_s override it."""
    length = clip.get("length", "auto")
    if length not in LENGTH_PRESETS:
        raise SystemExit(f"clip.length must be one of {list(LENGTH_PRESETS)}")
    pre, post = LENGTH_PRESETS[length]
    return clip.get("pre_s", pre), clip.get("post_s", post)


def detector_config(over: dict) -> DetectorConfig:
    names = {f.name for f in fields(DetectorConfig)}
    unknown = set(over) - names
    if unknown:
        raise SystemExit(f"unknown detector settings: {sorted(unknown)}")
    return DetectorConfig(**over)


class Channel:
    def __init__(self, ch: dict, cfg: dict, out_dir: Path, buffer_dir: Path):
        self.name = ch["name"]
        self.platform = ch.get("platform", "twitch")
        self.url = ch.get("url") or f"https://twitch.tv/{self.name}"
        clip = cfg["clip"]
        self.pre, self.post = clip_window(clip)
        self.min_score = clip.get("min_score", 35)
        self.av_offset = clip.get("chat_video_offset_s", 0.0)  # +: video arrives later than chat
        self.segment_s = clip.get("segment_s", 10)
        self.out_dir = out_dir / self.name
        self.rec = Recorder(self.name, self.url, buffer_dir, ch.get("quality", clip.get("quality", "best")),
                            self.segment_s, int(clip.get("buffer_minutes", 15) * 60))
        self.det = SpikeDetector(self.name, detector_config({**cfg["detector"], **ch.get("detector", {})}))
        self.history: deque[ChatMessage] = deque()
        self.msg_count = 0

    async def read_chat(self):
        async for m in chat_source(self.platform, self.name, self.url):
            self.det.add(m)
            self.history.append(m)
            self.msg_count += 1

    async def tick(self):
        last_stat = time.time()
        while True:
            now = time.time()
            while self.history and self.history[0].ts < now - 180:
                self.history.popleft()
            ev = self.det.tick(now - CHAT_LAG_S.get(self.platform, 0.0))
            if ev:
                log.info("[%s] 🔥 SPIKE ratio=%.1fx msgs=%d users=%d tokens=%s",
                         self.name, ev.ratio, ev.msgs, ev.unique_users, ev.top_tokens)
                asyncio.create_task(self.make_clip(ev))
            if now - last_stat >= 60:
                log.info("[%s] live=%s chat=%d msg/min baseline=%.1f/%.0fs segs=%d",
                         self.name, self.rec.live, self.msg_count, self.det.baseline,
                         self.det.cfg.window_s, len(self.rec.segments()))
                self.msg_count, last_stat = 0, now
            await asyncio.sleep(1)

    async def make_clip(self, ev: HypeEvent):
        t0 = ev.ts - self.pre + self.av_offset
        t1 = ev.ts + self.post + self.av_offset
        await asyncio.sleep(max(0, t1 + self.segment_s + 3 - time.time()))  # let segments flush
        stamp = datetime.fromtimestamp(ev.ts).strftime("%Y%m%d-%H%M%S")
        tmp = self.out_dir / f"{stamp}.mp4"
        chat = [f"{m.user}: {m.text}" for m in self.history if ev.ts - self.pre - 5 <= m.ts <= ev.ts + self.post]
        video = await cut(self.rec.segments(), t0, t1, tmp, self.segment_s)
        ms = await asyncio.to_thread(moment_score, video, ev.ratio, ev.msgs, ev.hype_msgs,
                                     (max(0.0, self.pre - 25), self.pre + self.post))
        sub = self.out_dir if ms.total >= self.min_score else self.out_dir / "low"
        base = sub / f"{stamp}_s{ms.total:02d}"
        base.parent.mkdir(parents=True, exist_ok=True)
        if video:
            video = video.rename(base.with_suffix(".mp4"))
        meta = {"event": asdict(ev), "score": ms.as_dict(), "video": str(video) if video else None,
                "window": [t0, t1], "chat": chat, "label": await label(self.name, chat)}
        base.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        with (self.out_dir.parent / "events.jsonl").open("a") as f:
            f.write(json.dumps({k: meta[k] for k in ("event", "score", "video", "label")}, ensure_ascii=False) + "\n")
        title = (meta["label"] or {}).get("title", "")
        log.info("[%s] ✂️  score %d (chat %.0f + audio %.0f) %s %s", self.name, ms.total, ms.chat, ms.audio,
                 video or "(no video buffered)", title)


async def run(cfg: dict, test_trigger: float | None):
    root = Path(cfg.get("output_dir", "clips"))
    buffer_dir = Path(cfg.get("buffer_dir", "buffer"))
    chans = [Channel(c, cfg, root, buffer_dir) for c in cfg["channel"]]
    tasks = []
    for c in chans:
        tasks += [c.rec.run(), c.rec.janitor(), c.read_chat(), c.tick()]
    if test_trigger:
        async def fake():
            await asyncio.sleep(test_trigger)
            for c in chans:
                log.info("[%s] TEST trigger", c.name)
                await c.make_clip(HypeEvent(c.name, time.time(), 0, 0, 0, 0, 0))
        tasks.append(fake())
    log.info("watching: %s", ", ".join(f"{c.name}({c.platform})" for c in chans))
    main_task = asyncio.gather(*tasks)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):  # also works when started in background
        loop.add_signal_handler(sig, main_task.cancel)
    try:
        await main_task
    except asyncio.CancelledError:
        log.info("shutting down")


def load_chat_json(path: Path) -> list[ChatMessage]:
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "comments" in data:  # TwitchDownloader
        return [ChatMessage(c["content_offset_seconds"], (c.get("commenter") or {}).get("name", "?"),
                            (c.get("message") or {}).get("body", "")) for c in data["comments"]]
    if isinstance(data, list):  # chat-downloader
        return [ChatMessage(m["time_in_seconds"], (m.get("author") or {}).get("name", "?"),
                            m.get("message") or "") for m in data if "time_in_seconds" in m]
    raise SystemExit("unrecognised chat JSON (expected TwitchDownloader or chat-downloader format)")


def hms(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="clipfarm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="watch live channels and auto-clip chat spikes")
    r.add_argument("--config", default="channels.toml", type=Path)
    r.add_argument("--test-trigger", type=float, help="force one clip after N seconds (pipeline check)")
    a = sub.add_parser("analyze", help="find spikes in a downloaded chat log (VOD offsets)")
    a.add_argument("chat", type=Path)
    a.add_argument("--length", choices=list(LENGTH_PRESETS), default="auto")
    a.add_argument("--pre", type=float, default=None, help="override seconds before spike")
    a.add_argument("--post", type=float, default=None, help="override seconds after spike")
    a.add_argument("--ratio", type=float, default=None)
    a.add_argument("--vod", help="VOD URL (e.g. https://www.twitch.tv/videos/123): also cut each spike")
    a.add_argument("--top", type=int, default=10, help="with --vod: cut only the N strongest spikes")
    a.add_argument("--out", type=Path, default=Path("clips/vod"))
    e = sub.add_parser("edit", help="make a 1080x1920 Short with word captions from a raw clip")
    e.add_argument("clip", type=Path)
    e.add_argument("--layout", choices=["blur", "square", "stack"], default="blur")
    e.add_argument("--captions", choices=["karaoke", "hormozi", "classic"], default="karaoke")
    e.add_argument("--facecam", help="stack layout: x,y,w,h as fractions, e.g. 0,0.69,0.22,0.31")
    e.add_argument("--title", default=None, help="top text (default: label title from the clip's .json)")
    e.add_argument("--out", type=Path, default=None)
    th = sub.add_parser("thumb", help="YouTube thumbnails (3 variants) from a clip")
    th.add_argument("clip", type=Path)
    th.add_argument("--text", required=True, help="1-4 words; *word* = yellow highlight, e.g. 'HE GOT *CAUGHT*'")
    th.add_argument("--format", choices=["16:9", "9:16", "both"], default="16:9")
    th.add_argument("--facecam", help="x,y,w,h fractions of the facecam in the clip -> big face sticker")
    th.add_argument("--face-side", choices=["left", "right"], default="right")
    th.add_argument("--face-focus", default="0.5,0.3", help="x,y of the face inside the facecam box (0-1)")
    th.add_argument("--circle", help="x,y,r fractions of the thumbnail: red circle on the key thing")
    th.add_argument("--at", type=float, help="seconds into the clip to search around (default: audio peak)")
    th.add_argument("--variants", type=int, default=3)
    th.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if args.cmd == "thumb":
        from .thumb import ThumbSpec, make_thumbnails
        fc = tuple(float(x) for x in args.facecam.split(",")) if args.facecam else None
        circ = tuple(float(x) for x in args.circle.split(",")) if args.circle else None
        for fmt in (["16:9", "9:16"] if args.format == "both" else [args.format]):
            ff = tuple(float(x) for x in args.face_focus.split(","))
            spec = ThumbSpec(args.text, fmt, fc, args.face_side, circ, face_focus=ff)
            for p in make_thumbnails(args.clip, spec, args.variants, args.at, args.out):
                print(p)
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if args.cmd == "edit":
        from .edit import make_short
        title = args.title
        meta = args.clip.with_suffix(".json")
        if title is None and meta.exists():
            title = ((json.loads(meta.read_text()).get("label") or {}).get("title")) or ""
        fc = tuple(float(x) for x in args.facecam.split(",")) if args.facecam else None
        out = args.out or args.clip.with_name(f"{args.clip.stem}_short_{args.layout}_{args.captions}.mp4")
        print(make_short(args.clip, out, title or "", args.layout, fc, captions=args.captions))
        return
    if args.cmd == "run":
        try:
            asyncio.run(run(load_config(args.config), args.test_trigger))
        except KeyboardInterrupt:
            pass
    else:
        args.pre, args.post = clip_window({"length": args.length,
                                           **({"pre_s": args.pre} if args.pre is not None else {}),
                                           **({"post_s": args.post} if args.post is not None else {})})
        cfg = DetectorConfig() if args.ratio is None else DetectorConfig(ratio=args.ratio)
        events = detect_offline(load_chat_json(args.chat), cfg=cfg)
        ranked = sorted(events, key=lambda e: -e.ratio)
        print(f"{len(events)} spikes")
        for e in ranked:
            print(f"  {hms(e.ts - args.pre)} → {hms(e.ts + args.post)}  ratio={e.ratio:>5.1f}x "
                  f"msgs={e.msgs:<4} users={e.unique_users:<4} {e.top_tokens[:3]}")
        if args.vod:
            cut_vod(args.vod, ranked[:args.top], args.pre, args.post, args.out)


def cut_vod(vod: str, events: list[HypeEvent], pre: float, post: float, out: Path) -> None:
    """Download only the spike sections of a VOD with yt-dlp (no full VOD download)."""
    import subprocess
    out.mkdir(parents=True, exist_ok=True)
    for e in events:
        a, b = max(0, e.ts - pre), e.ts + post
        name = out / f"{hms(a).replace(':', '-')}_r{e.ratio:.0f}.mp4"
        cmd = [venv_bin("yt-dlp"), "-q", "--no-warnings", "-f", "best[height<=1080]/best",
               "--download-sections", f"*{a:.0f}-{b:.0f}", "--force-keyframes-at-cuts",
               "-o", str(name), vod]
        ok = subprocess.run(cmd).returncode == 0 and name.exists()
        ms = moment_score(name if ok else None, e.ratio, e.msgs, e.hype_msgs, (max(0, pre - 25), pre + post))
        if ok:
            final = name.with_name(f"{hms(a).replace(':', '-')}_s{ms.total:02d}.mp4")
            name = name.rename(final)
        print(f"  {'✂️ ' if ok else '❌'} score {ms.total:>3} (chat {ms.chat:.0f} + audio {ms.audio:.0f})  {name}")
        name.with_suffix(".json").write_text(json.dumps({"event": asdict(e), "score": ms.as_dict()},
                                                        indent=2, ensure_ascii=False))
