"""Catch up on a stream that is live right now: scan only the last N minutes of its VOD.

Downloading a whole 7 h chat log takes ~45 min; the last 40 min takes seconds. Live tracking
(`clipfarm run --only <channel>`) then covers everything from now on, so nothing is scanned twice.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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
