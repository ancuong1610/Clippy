"""AI hype judge: looks at the clip (frames) + how chat reacted and rates it 0-100.

Chat volume and loudness can't tell a plane crash from a gym animation; seeing the frames can.
Anchored on a benchmark: xQc's GTA 6 seaplane crash (barrel rolls -> crash into palm trees -> dies ->
stash impounded, chat "o7 crash" / "lost everything") = a 95. Only clips at that level are highlights.
Needs OPENROUTER_API_KEY; model: OPENROUTER_JUDGE_MODEL (default google/gemini-3.5-flash-lite).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tomllib
from pathlib import Path

import httpx

EXAMPLES = Path("judge_examples.toml")
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"

RUBRIC = """You are the strict editor of a YouTube Shorts channel that posts GTA 6 livestream highlights.
You see {n} frames from a {dur:.0f}s stream clip (in order, with their time in seconds) and the live chat.
Rate how strong this clip is as a standalone Short for people who never watch this streamer.

Reference moments rated by the channel owner (match your scores to these):
{examples}

Scale (be harsh; most stream moments are NOT highlights):
- 90-100: gameplay with a visible, dramatic payoff a stranger instantly gets: spectacular crash/explosion/death,
  insane stunt or escape, a chase that ends in chaos, a dangerous animal attack or close call, epic fail or win.
- 70-89: genuinely funny or notable but smaller: a great reaction to something mild, a near-miss, a clever play.
- 40-69: mild: gym/workout animations, shopping, outfits, NPC small talk, driving or swimming around,
  looting, settings or menus, the streamer just talking.
- 0-39: nothing visible happens, loading/black screens, technical issues.
STORY CUTSCENES are allowed (the streamer reacts over them): rate them by how big the story beat is. You can't
hear the joke, so the chat is your evidence: chat erupting together (laughing, shock) at a cutscene = a beat
that landed (70-85); routine dialogue with calm chat = 20-40.
Chat tells you WHAT happened (e.g. "shark", "o7 crash") — use it to find the moment in the frames. For gameplay,
chat spamming memes alone does NOT make a highlight; the payoff must be visible on screen.

LIVE CHAT (seconds from clip start):
{chat}

Return ONLY JSON: {{"hype": <0-100>, "cutscene": <true|false>, "payoff": "<what visibly happens, max 15 words>",
"why": "<one sentence: why this score vs the reference moments>"}}"""


def examples_text(path: Path = EXAMPLES) -> str:
    try:
        ex = tomllib.loads(path.read_text()).get("example", [])
    except (OSError, tomllib.TOMLDecodeError):
        ex = []
    if not ex:
        return "- 95: seaplane barrel rolls then crashes into palm trees, explodes, 'Wasted' (benchmark)"
    return "\n".join(f"- {e['score']}: {e['what']}" for e in sorted(ex, key=lambda e: -e["score"]))


def _duration(video: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(video)]).strip())


def frames(video: Path, n: int = 12, width: int = 640) -> list[tuple[float, str]]:
    """n evenly spaced JPEG frames as (time, base64)."""
    dur = _duration(video)
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        jpg = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                              "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "-"],
                             capture_output=True).stdout
        if jpg:
            out.append((round(t, 1), base64.b64encode(jpg).decode()))
    return out


def chat_lines(meta: dict, limit: int = 80) -> str:
    timed = meta.get("chat_timed") or []
    if not timed:
        return "\n".join((meta.get("chat") or [])[-limit:]) or "(no chat)"
    step = max(1, len(timed) // limit)  # even sample across the clip, keeps the reaction shape
    return "\n".join(f"[{m['t']:.0f}] {m['user']}: {m['text'][:80]}" for m in timed[::step][:limit])


def final_score(verdict: dict, chat_timed: list[dict]) -> dict:
    """Owner's rule: a hype moment is also chat spiking AND answering in one mood. Judge (what's on screen)
    + up to +15 for chat unison. Applies to cutscenes too (owner: fine to post, the streamer reacts over them)."""
    from .detector import mood_peak, unison_bonus
    share, mood = mood_peak(chat_timed)
    bonus = unison_bonus(share)
    return {**verdict, "visual": verdict["hype"], "unison": share, "mood": mood, "bonus": bonus,
            "hype": int(min(100, round(verdict["hype"] + bonus)))}


def judge(video: Path, meta: dict | None = None, model: str | None = None) -> dict:
    meta = meta if meta is not None else (json.loads(video.with_suffix(".json").read_text())
                                          if video.with_suffix(".json").exists() else {})
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY missing in .env")
    fr = frames(video)
    content: list[dict] = [{"type": "text", "text": RUBRIC.format(n=len(fr), dur=_duration(video),
                                                                  examples=examples_text(), chat=chat_lines(meta))}]
    for t, b64 in fr:
        content.append({"type": "text", "text": f"frame at {t}s"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    model = model or os.getenv("OPENROUTER_JUDGE_MODEL", DEFAULT_MODEL)
    last = None
    for _ in range(3):
        try:
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": model, "messages": [{"role": "user", "content": content}],
                                 "response_format": {"type": "json_object"}, "temperature": 0})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            out = json.loads(text[text.find("{"): text.rfind("}") + 1])
            out["hype"] = max(0, min(100, int(out["hype"])))
            out["model"] = model
            return out
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            last = e
    raise RuntimeError(f"judge failed: {last}")
