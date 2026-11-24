"""Catch up on a stream that is live right now: scan only the last N minutes of its VOD.

Downloading a whole 7 h chat log takes ~45 min; the last 40 min takes seconds. Live tracking
(`clipfarm run --only <channel>`) then covers everything from now on, so nothing is scanned twice.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .recorder import venv_bin

TDL = "tools/TwitchDownloaderCLI"


@dataclass
class Vod:
    id: str
    url: str
    duration: float  # seconds so far (keeps growing while live)
    title: str


def latest_vod(channel: str) -> Vod:
    """Newest archive of a Twitch channel — the live stream's VOD while it is live."""
    out = subprocess.run([venv_bin("yt-dlp"), "--flat-playlist", "--playlist-end", "1", "--print",
                          "%(id)s\t%(duration)s\t%(title)s",
                          f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        raise SystemExit(f"no VODs found for {channel} (VODs disabled or subscriber-only?)")
    vid, dur, title = out.splitlines()[0].split("\t", 2)
    vid = vid.lstrip("v")
    return Vod(vid, f"https://www.twitch.tv/videos/{vid}", float(dur), title)


def tdl_path() -> str:
    p = Path(TDL)
    if p.exists():
        return str(p)
    found = shutil.which("TwitchDownloaderCLI")
    if not found:
        raise SystemExit("TwitchDownloaderCLI not found: put it in tools/ (see README)")
    return found


def recent_chat(vod: Vod, minutes: float, out: Path | None = None) -> Path:
    """Chat of only the last `minutes` of the VOD (offsets stay relative to the VOD start)."""
    start = max(0.0, vod.duration - minutes * 60)
    out = out or Path(tempfile.mkdtemp()) / f"{vod.id}_last{minutes:.0f}m.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([tdl_path(), "chatdownload", "-u", vod.url, "-b", f"{start:.0f}s", "-e", f"{vod.duration:.0f}s",
                    "-o", str(out), "--collision", "Overwrite"], check=True, capture_output=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit("chat download returned nothing")
    json.loads(out.read_text())  # fail loudly on a truncated file
    return out


def finished_vods(channel: str, n: int | None = 3, *, runner=None) -> list[Vod]:
    """List the newest finished Twitch archives. Pass n=None to list all available archives."""
    if n is not None and n <= 0:
        return []
    run = runner or subprocess.run
    yt = [venv_bin("yt-dlp"), "--ignore-config", "--skip-download"]
    url = f"https://www.twitch.tv/{channel}"
    cmd = [*yt, "--flat-playlist", "--print", "%(id)s\t%(duration)s\t%(title)s"]
    if n is not None:
        cmd += ["--playlist-end", str(n + 1)]
    archives = run([*cmd, f"{url}/videos?filter=archives&sort=time"], check=True,
                   capture_output=True, text=True).stdout.splitlines()
    if not archives:
        return []

    live_cmd = [*yt, "--print", "is_live", url]
    live = run(live_cmd, capture_output=True, text=True)
    if live.returncode and "The channel is not currently live" not in live.stderr:
        raise subprocess.CalledProcessError(live.returncode, live_cmd, live.stdout, live.stderr)
    if not live.returncode:
        value = live.stdout.strip()
        if value == "True":
            archives = archives[1:]
        elif value != "False":
            raise RuntimeError(f"could not determine whether {channel} is live")

    vods = []
    for row in archives:
        ident, duration, title = row.split("\t", 2)
        if duration == "NA":
            continue
        vid, seconds = ident.removeprefix("v"), float(duration)
        if vid.isascii() and vid.isdecimal() and math.isfinite(seconds) and seconds > 0:
            vods.append(Vod(vid, f"https://www.twitch.tv/videos/{vid}", seconds, title))
    return vods[:n]


def full_chat(url: str, duration: float, out: Path, piece_s: int = 1800, min_msgs: int = 1500,
              tries: int = 3, *, runner=None, sleep=None) -> Path:
    """Download one chat range at a time and keep the fullest result for each range."""
    if not math.isfinite(duration) or duration <= 0 or piece_s <= 0 or tries <= 0:
        raise ValueError("duration, piece_s and tries must be positive")
    run = runner or subprocess.run
    pause = sleep or time.sleep
    downloader = tdl_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    merged, comments = None, {}
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".chat-") as tmp:
        part = Path(tmp) / "part.json"
        for start in range(0, math.ceil(duration), piece_s):
            end = min(math.ceil(duration), start + piece_s)
            best = None
            for attempt in range(tries):
                if attempt:
                    pause(20)
                part.unlink(missing_ok=True)
                try:
                    run([downloader, "chatdownload", "-u", url, "-b", f"{start}s", "-e", f"{end}s",
                         "-o", str(part), "--collision", "Overwrite"], check=True, capture_output=True)
                    data = json.loads(part.read_text())
                    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
                        raise ValueError("unrecognised TwitchDownloader chat JSON")
                except (subprocess.CalledProcessError, OSError, ValueError):
                    if attempt == tries - 1 and best is None:
                        raise
                    continue
                if best is None or len(data["comments"]) > len(best["comments"]):
                    best = data
                if len(data["comments"]) >= min_msgs:
                    break
            if merged is None:
                merged = best
            for comment in best["comments"]:
                comments.setdefault(comment["_id"], comment)
            print(f"  chat {start // 60}–{end / 60:g} min: {len(best['comments'])} messages", flush=True)
        merged["comments"] = sorted(comments.values(), key=lambda c: c["content_offset_seconds"])
        part.write_text(json.dumps(merged))
        part.replace(out)
    return out
