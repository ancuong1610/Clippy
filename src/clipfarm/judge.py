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
Rate how strong this clip is as a standalone Short for people who never watch this streamer and scroll past
anything they don't get in 3 seconds with the sound off.

Reference moments rated by the channel owner (match your scores to these):
{examples}

What the owner's approvals and the most-viewed GTA 6 streamer clips have in common:
- a VISIBLE event a stranger gets instantly: crash, explosion, death/"Wasted", stunt, chase ending in chaos,
  animal attack, the streamer's own fail (misclick, lost progress, mission failed);
- or a WEIRD/UNEXPECTED world moment (iguana run over, NPCs in a road-rage fight, a bizarre radio channel);
- or STAKES: the streamer loses something, does something for the first time, or it connects to his own life.
What the owner rejected: story cutscenes and dialogue (strangers don't know the plot and can't follow the
joke), NPC small talk, menus, the streamer just talking, chat spamming LOL at something you can't see.

Score four parts 0-10 each:
- payoff: how big and clear the on-screen event is (10 = plane explodes into a dock; 2 = people talking).
- stakes: what the streamer risks or loses, first time ever, a mistake he caused, or personal connection.
- legible: can a stranger tell what happened from the picture alone in 3 s (10) or needs context/audio (0)?
- surprise: unexpected, weird or "did that just happen" (10) vs routine gameplay (0).
Then the overall hype 0-100:
- 90-100: big visible payoff AND legible AND (stakes or surprise). Rare: most clips are NOT this.
- 70-89: a clear funny/notable moment that is smaller or needs a little context.
- 40-69: mild: gym, shopping, outfits, driving around, NPC talk, menus, the streamer just talking.
- 0-39: nothing visible happens, loading/black screens, technical issues.
STORY CUTSCENES: max 80, and usually 30-60, because strangers can't follow them. Only when the streamer
himself causes the chaos (shoots the characters mid-scene) may a cutscene clip go above 80.
OFF-TOPIC: this channel only posts GTA 6. If the clip is not GTA 6, score it 20 or less and set "gta6": false.
Chat tells you WHAT happened (e.g. "shark", "o7 crash") — use it to find the moment in the frames. Chat
spamming memes alone does NOT make a highlight; the payoff must be visible on screen.

LIVE CHAT (seconds from clip start):
{chat}

Return ONLY JSON: {{"hype": <0-100>, "payoff_pts": <0-10>, "stakes": <0-10>, "legible": <0-10>, "surprise": <0-10>,
"gta6": <true|false>, "cutscene": <true|false>, "streamer_caused": <true|false>,
"kind": "<crash_fail|stunt|weird_world|streamer_lore|first_time|action|cutscene|talk|idle>",
"payoff_at": <time in seconds of the FIRST frame where the payoff is visible, or null>,
"payoff": "<what visibly happens, max 15 words>", "why": "<one sentence: why this score vs the reference moments>"}}"""


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
    return frames_at(video, [dur * (i + 0.5) / n for i in range(n)], width)


def frames_at(video: Path, times: list[float], width: int = 640) -> list[tuple[float, str]]:
    """JPEG frames at the given times as (time, base64)."""
    out = []
    for t in times:
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
    """Judge (what's on screen) + up to +15 when chat answers as one, but only for gameplay that already shows
    a real payoff (visual >= 80). Owner's review of 79 clips (2026-11-21): the unison bonus pushed cutscene
    dialogue that chat laughed at over the bar (approved 7/35 cutscenes vs 19/31 gameplay), so cutscenes get no
    bonus and stay <= 80 unless the streamer caused the chaos himself."""
    from .detector import mood_peak, unison_bonus
    share, mood = mood_peak(chat_timed)
    visual = verdict["hype"]
    cut = verdict.get("cutscene") is True
    ok = verdict.get("gta6") is not False and not cut and visual >= 80
    bonus = unison_bonus(share) if ok else 0.0
    hype = min(100, round(visual + bonus))
    if cut and not verdict.get("streamer_caused"):
        hype = min(hype, 80)
    return {**verdict, "visual": visual, "unison": share, "mood": mood, "bonus": bonus, "hype": int(hype)}


IMPACT = """These {n} frames are {step:.2f}s apart, from a GTA 6 stream clip, just before and during this event:
"{payoff}". Find the frame where the event HAPPENS: the first contact/impact/hit/explosion (the vehicle touches
the cows, the plane touches the pole, the car hits the wall). Not the approach, not the aftermath.
Return ONLY JSON: {{"impact_frame": <0-based index of that frame, or null if none shows it>}}"""


def impact_time(video: Path, payoff: str, lo: float, hi: float, step: float = 0.4,
                model: str | None = None) -> float | None:
    """Exact second the payoff starts (first contact), from dense frames in [lo, hi]. The judge's payoff_at is
    only one frame step precise; the hook tease must end right before contact, so look again closer."""
    key = os.getenv("OPENROUTER_API_KEY")
    lo = max(0.0, lo)
    times = [round(lo + i * step, 2) for i in range(int((hi - lo) / step) + 1)][:16]
    fr = frames_at(video, times, width=480)
    if not key or len(fr) < 2:
        return None
    content: list[dict] = [{"type": "text", "text": IMPACT.format(n=len(fr), step=step, payoff=payoff)}]
    for i, (t, b64) in enumerate(fr):
        content.append({"type": "text", "text": f"frame {i}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    try:
        r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": model or os.getenv("OPENROUTER_JUDGE_MODEL", DEFAULT_MODEL),
                             "messages": [{"role": "user", "content": content}],
                             "response_format": {"type": "json_object"}, "temperature": 0})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        i = json.loads(text[text.find("{"): text.rfind("}") + 1]).get("impact_frame")
        return fr[int(i)][0] if isinstance(i, (int, float)) and 0 <= int(i) < len(fr) else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


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
            if out.get("gta6") is False:  # off-topic for a GTA 6 channel, whatever happens in it
                out["hype"] = min(out["hype"], 20)
            out["model"] = model
            out["frame_step"] = round(_duration(video) / max(1, len(fr)), 2)  # payoff_at is only this precise
            return out
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            last = e
    raise RuntimeError(f"judge failed: {last}")
