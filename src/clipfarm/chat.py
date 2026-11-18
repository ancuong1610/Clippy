"""Live chat readers. Each yields ChatMessage with wall-clock receive time."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import AsyncIterator

from .detector import ChatMessage

log = logging.getLogger(__name__)


async def twitch_chat(login: str) -> AsyncIterator[ChatMessage]:
    """Anonymous read-only Twitch IRC (no account / token needed). Reconnects forever."""
    login = login.lower().lstrip("#")
    backoff = 2
    while True:
        try:
            reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{login}\r\n".encode())
            await writer.drain()
            log.info("[%s] twitch chat connected", login)
            backoff = 2
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=360)
                if not raw:
                    raise ConnectionError("chat socket closed")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue
                # :user!user@user.tmi.twitch.tv PRIVMSG #chan :message
                if " PRIVMSG #" in line and line.startswith(":"):
                    prefix, _, rest = line[1:].partition(" PRIVMSG #")
                    _, _, text = rest.partition(" :")
                    yield ChatMessage(time.time(), prefix.split("!", 1)[0], text)
        except (OSError, ConnectionError, asyncio.TimeoutError) as e:
            log.warning("[%s] twitch chat dropped (%s), retry in %ss", login, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def parse_yt_chat_line(line: str) -> list[ChatMessage]:
    """One line of yt-dlp's live_chat JSON -> messages (uses YouTube's own timestamp)."""
    import json
    try:
        obj = json.loads(line)
    except ValueError:
        return []
    acts = (obj.get("replayChatItemAction") or {}).get("actions") or obj.get("actions") or []
    out = []
    for a in acts:
        r = ((a.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
        if not r:
            continue
        parts = []
        for run in (r.get("message") or {}).get("runs", []):
            if "text" in run:
                parts.append(run["text"])
            elif "emoji" in run:
                sc = run["emoji"].get("shortcuts") or []
                parts.append(sc[0] if sc else "")
        ts = int(r.get("timestampUsec", 0)) / 1e6 or time.time()
        out.append(ChatMessage(ts, (r.get("authorName") or {}).get("simpleText", "?"), " ".join(parts)))
    return out


async def ytdlp_chat(url: str) -> AsyncIterator[ChatMessage]:
    """YouTube live chat via yt-dlp (maintained). Arrives in ~10s batches; timestamps are exact."""
    import sys
    import tempfile
    from pathlib import Path
    ytdlp = str(Path(sys.executable).parent / "yt-dlp")
    while True:
        tmp = Path(tempfile.mkdtemp(prefix="ytchat_"))
        started = time.time()
        proc = await asyncio.create_subprocess_exec(
            ytdlp, "--no-warnings", "-q", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
            "-o", str(tmp / "chat"), url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        pos = 0
        try:
            while True:
                files = sorted(tmp.glob("chat.live_chat.json*"))
                files = [f for f in files if "Frag" not in f.name]
                if files:
                    with files[0].open() as f:
                        f.seek(pos)
                        data = f.read()
                    # only consume complete lines
                    cut = data.rfind("\n") + 1
                    pos += len(data[:cut].encode())
                    for line in data[:cut].splitlines():
                        for m in parse_yt_chat_line(line):
                            if m.ts >= started - 5:  # skip backlog from before we joined
                                yield m
                if proc.returncode is not None:
                    break
                await asyncio.sleep(1)
        finally:
            if proc.returncode is None:
                proc.terminate()
        log.info("youtube chat %s ended/unavailable; retry in 30s", url)
        await asyncio.sleep(30)


CHAT_LAG_S = {"twitch": 0.0, "youtube": 12.0}  # detector runs this far behind to absorb batching


def chat_source(platform: str, channel: str, url: str) -> AsyncIterator[ChatMessage]:
    if platform == "twitch":
        return twitch_chat(channel)
    if platform == "youtube":
        return ytdlp_chat(url)
    raise ValueError(f"unsupported chat platform: {platform} (twitch, youtube)")
