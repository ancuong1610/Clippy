"""Hooky titles + captions per platform from what was SAID (transcript) and how CHAT reacted.

Borrowed from OpenShorts (full transcript -> platform-specific title/description/hook) and added
the chat signal OpenShorts doesn't have. Writes result into the clip's .json under "titles".
Needs OPENROUTER_API_KEY in .env (OPENROUTER_MODEL optional).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

PROMPT = """You write titles for a YouTube Shorts / TikTok / Reels clip channel ("Stream Chaos Clips")
that posts moments from big livestreamers. Clip below: streamer {streamer}, game/context: {context}.

WHAT WAS SAID in the clip (speech transcript, may include game/video audio):
{transcript}

LIVE CHAT right around the moment (how viewers reacted):
{chat}

Write for maximum views, like top clip channels: concrete, emotional, curiosity gap, names the streamer.
Rules:
- TRUE to the clip. Never promise something that isn't in it (YouTube removes misleading titles).
- No spoilers of the punchline; tease it.
- Same language as the transcript (default English).
- YouTube titles <= 70 chars (show fully on phones), no hashtags in titles, max 1 emoji.
- thumbnail_text: 1-4 words, ALL CAPS, mark the single payoff word with *asterisks*.

Return ONLY JSON:
{{
  "youtube_titles": ["<best>", "<alt 2>", "<alt 3>"],
  "youtube_description": "<2 sentences + credit to {streamer} + 3-5 hashtags>",
  "tiktok_caption": "<1 line hook + 3-5 hashtags, <= 150 chars>",
  "instagram_caption": "<1-2 lines + 3-5 hashtags>",
  "thumbnail_text": "<1-4 words e.g. HE GOT *CAUGHT*>",
  "what_happened": "<1 neutral sentence, for the human reviewer>"
}}"""


def _call(prompt: str) -> dict:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY missing in .env")
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": os.getenv("OPENROUTER_MODEL", "openrouter/auto"),
                         "messages": [{"role": "user", "content": prompt}],
                         "response_format": {"type": "json_object"}})
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def validate(t: dict) -> dict:
    titles = [s.strip() for s in t.get("youtube_titles", []) if isinstance(s, str) and s.strip()]
    if not titles:
        raise ValueError("model returned no titles")
    t["youtube_titles"] = [s[:100] for s in titles][:3]
    tt = " ".join(str(t.get("thumbnail_text", "")).split()[:4]).upper()
    t["thumbnail_text"] = tt
    t["tiktok_caption"] = str(t.get("tiktok_caption", ""))[:2200]
    return t


def build_prompt(streamer: str, context: str, transcript: str, chat: list[str]) -> str:
    return PROMPT.format(streamer=streamer, context=context,
                         transcript=transcript[:6000] or "(no speech)",
                         chat="\n".join(chat[-200:])[:6000] or "(no chat captured)")


def make_titles(clip: Path, streamer: str | None = None, context: str = "GTA 6 livestream") -> dict:
    """Uses <clip>.words.json (from `clipfarm edit`, transcribes if missing) + chat from <clip>.json."""
    words_f = clip.with_suffix(".words.json")
    if words_f.exists():
        words = json.loads(words_f.read_text())
    else:
        from .edit import transcribe
        words = [w.__dict__ for w in transcribe(clip)]
        words_f.write_text(json.dumps(words, ensure_ascii=False))
    transcript = " ".join(w["text"] for w in words)
    meta_f = clip.with_suffix(".json")
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    chat = meta.get("chat", [])
    streamer = streamer or (meta.get("event") or {}).get("channel") or clip.parent.name
    t = validate(_call(build_prompt(streamer, context, transcript, chat)))
    meta["titles"] = t
    meta_f.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return t
