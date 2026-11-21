"""Paid clipping campaigns (Content Rewards): which clip goes to which campaign, and what that campaign demands.

Each `[[campaign]]` in channels.toml lists the streamers it accepts ("*" = any GTA 6 streamer), the hashtags the
caption must carry, and an optional logo that must stay on screen for the whole clip. A clip goes to exactly ONE
campaign: the first match in file order (e.g. Kai Cenat's campaign bans logos, ClipUp requires one).
"""

from __future__ import annotations

from pathlib import Path


def route(streamer: str, cfg: dict) -> dict | None:
    s = (streamer or "").lower().replace(" ", "")
    for c in cfg.get("campaign", []):
        if c.get("enabled", True) is False:
            continue
        accepts = c.get("streamers", "*")
        if accepts == "*" or s in [a.lower().replace(" ", "") for a in accepts]:
            return c
    return None


def logo_path(campaign: dict | None) -> Path | None:
    p = (campaign or {}).get("logo")
    return Path(p) if p and Path(p).exists() else None


def with_hashtags(text: str, campaign: dict | None) -> str:
    """Append the campaign's required hashtags that the text doesn't already contain (case-insensitive)."""
    tags = [t for t in (campaign or {}).get("hashtags", []) if t.lower() not in (text or "").lower()]
    return f"{text.rstrip()} {' '.join(tags)}".strip() if tags else text
