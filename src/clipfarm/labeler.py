"""Optional: ask an LLM (via OpenRouter) what happened, for a title + keep score.
Only runs when OPENROUTER_API_KEY is set in the environment / .env."""

from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger(__name__)

PROMPT = """You label livestream highlight candidates for a YouTube Shorts clip channel.
Streamer: {channel}. Game: GTA 6. Below is the live chat from ~30s before to ~10s after chat exploded.
Infer what most likely happened on stream. Reply ONLY with JSON:
{{"what_happened": "<1 sentence>", "title": "<Shorts title, <70 chars, hooky, no clickbait lies>",
"keep_score": <0-10, how likely this is a genuinely clip-worthy moment>}}

CHAT:
{chat}"""


async def label(channel: str, chat_lines: list[str]) -> dict | None:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    model = os.getenv("OPENROUTER_MODEL", "openrouter/auto")
    chat = "\n".join(chat_lines[-300:])
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [
                    {"role": "user", "content": PROMPT.format(channel=channel, chat=chat)}],
                    "response_format": {"type": "json_object"}})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start:end + 1])
    except Exception as e:  # noqa: BLE001 - labeling is best-effort
        log.warning("labeler failed: %s", e)
        return None
