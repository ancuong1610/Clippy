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

WHAT WAS SAID in the clip ({duration:.0f}s long), with timestamps in seconds [start] (may include game/video audio):
{transcript}

LIVE CHAT right around the moment (how viewers reacted):
{chat}

Write for maximum views, like top clip channels: concrete, emotional, curiosity gap, names the streamer.
How the most-viewed GTA 6 streamer clips are titled (Speedy Boykins, xQc Clips, Nov 2026):
  "iShowSpeed Watches An Iguana Get RUN OVER In GTA 6" (142K), "xQc flies HEAD FIRST into a deck and EXPLODES"
  (44K), "iShowSpeed FILMS A Road Rage Fight Between Two NPCs" (124K), "xQc is in LOVE with this CHINESE RADIO
  CHANNEL" (431K), "Literally WHAT is GOING ON in GTA 6??" (216K).
  Pattern: streamer + a strong verb + the SPECIFIC thing on screen (iguana, deck, radio channel, cows, bridge),
  2-3 key words in CAPS. Generic titles ("LOST HIS MIND 😂🔥", "funny reaction") get almost no views.
Rules:
- TRUE to the clip. Never promise something that isn't in it (YouTube removes misleading titles).
- No spoilers of the punchline; tease it.
- Name the concrete object/place/animal/vehicle from the clip. Never write "Chat Loses It", "Chat Erupts",
  "Instantly", "Goes Wrong", "Chaos" or "Backfires" unless nothing more specific is true: say WHAT happens.
- Same language as the transcript (default English).
- YouTube titles <= 70 chars (show fully on phones), no hashtags in titles, max 1 emoji.
- thumbnail_text: 2-4 words, ALL CAPS, mark the single key word with *asterisks*. It is also the on-screen
  hook over the first 4 s, which show the build-up just BEFORE the payoff. It must open a question the viewer
  can only answer by watching to the end, and must NOT reveal the outcome. It must make sense on its own to
  someone who knows nothing: name the thing at stake ("HE TRUSTED THE *BRIDGE*", "WATCH THE *COWS*",
  "WAIT FOR THE *LANDING*", "5 STARS IN *10 SECONDS*"), never a fragment like "BEFORE EVERYONE" and never
  the answer ("HE *CRASHED*").
- label: a plain statement of what the viewer is watching, 3-7 words in ALL CAPS. No question, teaser,
  or answer-spoiling beyond what the opening hook already shows. Examples: "SPEED REACTS TO THE GTA 6 ENDING",
  "LACY GETS SHOVED OUT OF HIS CAR". Name the specific thing that makes this clip worth watching (the stunt,
  the stakes, the weird event: "STABLERONALDO RIDES HIS CAR ON A WALL"), never a generic activity
  ("DRIVES IN GTA 6", "PLAYS GTA 6", "LAUNCH STREAM") and never the stream itself. Context plus a lean: when the
  clip has a stake (a number, money, risk, a first time, something unexpected), put it in the label
  ("LACY DROPS THE $90K BAG"), without giving away the outcome.
- best_start/best_end: the tightest {min_len}-{max_len}s window that contains the setup AND the payoff.
  best_start must include the setup a stranger needs to understand the payoff (who/what is happening),
  typically 3-10 s before the event, with no dead air. Modern story arc (owner 2026-11-24): the first 5 s of
  the window must already make the situation clear (start at 70/100, not at 0) and the first peak should land
  within about 10 s; if the setup is long, start later and let the banner give the context instead.
  Start on the first word of a sentence, end right after the payoff/reaction. Seconds within 0-{duration:.0f}.
- chat_highlights: up to 6 chat lines copied EXACTLY as "user: message" from the LIVE CHAT above, that best
  show the crowd reacting to the payoff (funny, shocked, hype). NEVER pick slurs, hate, sexual content,
  harassment of anyone, personal info, links, or spam walls. [] if chat is empty or nothing qualifies.
- autopost_risks: things that make this clip unsafe to post WITHOUT a human check. List any that apply:
  "music" (a copyrighted song/music video is the main audio or the reason chat reacts), "sexual" (nudity,
  strip club, sexual acts or talk), "real_violence" (real-world, not in-game), "hate" (slurs, hateful speech),
  "harassment" (targeting a real person), "minor" (a child on screen), "unclear" (nothing happens / can't tell),
  "off_topic" (the clip is not about {context}).
  In-game GTA violence is fine. [] if none.
- lyric_spans: [[start, end], ...] seconds (transcript timestamps) where the transcript is SONG LYRICS (sung or
  rapped music, radio songs), so they are never shown as captions. [] if none. Speech is never lyrics.
- caption_fixes: speech-to-text mistakes in names/terms (streamers, games, slang) as [wrong, right] single
  words, e.g. [["fortnight","Fortnite"],["Automator","Automata"]]. Only clear mistakes; [] if none.

Return ONLY JSON:
{{
  "youtube_titles": ["<best>", "<alt 2>", "<alt 3>"],
  "youtube_description": "<2 sentences + credit to {streamer} + 3-5 hashtags>",
  "tiktok_caption": "<1 line hook + 3-5 hashtags, <= 150 chars>",
  "instagram_caption": "<1-2 lines + 3-5 hashtags>",
  "thumbnail_text": "<1-4 words e.g. HE GOT *CAUGHT*>",
  "label": "<3-7 word statement>",
  "what_happened": "<1 neutral sentence, for the human reviewer>",
  "best_start": <seconds>,
  "best_end": <seconds>,
  "caption_fixes": [["wrong", "right"]],
  "autopost_risks": [],
  "lyric_spans": [],
  "chat_highlights": ["user: message"]
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


def timestamped(words: list[dict], every: float = 3.0) -> str:
    """'[0.0] come on bro [3.1] they are so lucky ...' — compact but lets the model pick a window."""
    out, last = [], -every
    for w in words:
        if w["start"] - last >= every:
            out.append(f"[{w['start']:.1f}]")
            last = w["start"]
        out.append(w["text"])
    return " ".join(out)


def drop_lyrics(words: list[dict], spans) -> list[dict]:
    """Owner's rule: music in a clip is fine, but song lyrics are never captioned."""
    spans = spans or []
    return [w for w in words if not any(a - 0.2 <= w["start"] <= b + 0.2 for a, b in spans)]


def apply_fixes(words: list[dict], fixes) -> list[dict]:
    """Replace mis-heard words (case-insensitive, punctuation kept)."""
    table = {}
    for pair in fixes or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, str) for x in pair):
            if pair[0].strip() and " " not in pair[0].strip():
                table[pair[0].strip().lower()] = pair[1].strip()
    out = []
    for w in words:
        core = w["text"].strip(".,!?;:'\"")
        rep = table.get(core.lower())
        out.append({**w, "text": w["text"].replace(core, rep) if rep else w["text"]})
    return out


def pick_window(t: dict, duration: float, words: list[dict], min_len: float = 12, max_len: float = 35
                ) -> tuple[float, float]:
    """Validate the model's window and snap it to word boundaries; fall back to the whole clip."""
    try:
        s, e = float(t.get("best_start")), float(t.get("best_end"))
    except (TypeError, ValueError):
        return 0.0, duration
    s, e = max(0.0, s), min(duration, e)
    if not (min_len <= e - s <= max_len + 5):
        return 0.0, duration
    starts = [w["start"] for w in words if w["start"] <= s + 0.5]
    ends = [w["end"] for w in words if w["end"] >= e - 0.5]
    s = max(0.0, (max(starts) if starts else s) - 0.25)
    e = min(duration, (min(ends) if ends else e) + 0.5)
    return round(s, 2), round(e, 2)


def label_text(t: dict) -> str:
    """Normalize a statement label, or shorten the best YouTube title for older metadata."""
    def words(text: str) -> list[str]:
        return [word.strip("*_#") for word in text.split() if any(c.isalnum() for c in word)]

    value = t.get("label")
    if isinstance(value, str) and not any(mark in value for mark in "?？"):
        label = words(value)
        if 3 <= len(label) <= 7:
            return " ".join(label).upper()
    candidates = t.get("youtube_titles") or []
    for title in candidates:
        if isinstance(title, str) and title.strip():
            return " ".join(words(title)[:7]).rstrip(".,!?;:？").upper()
    return ""


def validate(t: dict) -> dict:
    titles = [s.strip() for s in t.get("youtube_titles", []) if isinstance(s, str) and s.strip()]
    if not titles:
        raise ValueError("model returned no titles")
    t["youtube_titles"] = [s[:100] for s in titles][:3]
    t["label"] = label_text(t)
    words = str(t.get("thumbnail_text", "")).split()
    if not 1 <= len(words) <= 6:  # never chop a hook mid-sentence ("GTA 6 NPCS ARE"): ask again
        raise ValueError(f"hook has {len(words)} words")
    t["thumbnail_text"] = " ".join(words).upper()
    t["tiktok_caption"] = str(t.get("tiktok_caption", ""))[:2200]
    risks = t.get("autopost_risks")
    # missing/garbled answer counts as a risk: unattended posting must fail closed
    t["autopost_risks"] = [str(r) for r in risks] if isinstance(risks, list) else ["unchecked"]
    spans = t.get("lyric_spans")
    t["lyric_spans"] = [[float(a), float(b)] for a, b in spans
                        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a] \
        if isinstance(spans, list) and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in spans) else []
    return t


def build_prompt(streamer: str, context: str, transcript: str, chat: list[str], duration: float = 38,
                 min_len: int = 15, max_len: int = 30) -> str:
    return PROMPT.format(streamer=streamer, context=context, duration=duration, min_len=min_len,
                         max_len=max_len, transcript=transcript[:6000] or "(no speech)",
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
    dur = float(__import__("subprocess").check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)]).strip())
    transcript = timestamped(words)
    meta_f = clip.with_suffix(".json")
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    chat = meta.get("chat", [])
    streamer = streamer or (meta.get("event") or {}).get("channel") or clip.parent.name
    prompt = build_prompt(streamer, context, transcript, chat, dur)
    for attempt in range(3):  # models occasionally return broken JSON / no titles
        try:
            t = validate(_call(prompt))
            break
        except (ValueError, KeyError, httpx.HTTPError):
            if attempt == 2:
                raise
    t["window"] = pick_window(t, dur, words)
    meta["titles"] = t
    meta_f.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return t
