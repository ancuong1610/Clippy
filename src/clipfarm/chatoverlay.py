"""Twitch-style chat column for hype moments.

Rules: only for high-score clips, only in the seconds after the payoff, 4-6 real messages,
curated (AI pick + local safety filter), drawn inside the content panel so it never covers the
face, the hook or the captions. Usernames shown in Twitch colors.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
TWITCH_COLORS = [(255, 69, 0), (30, 144, 255), (0, 255, 127), (255, 105, 180), (154, 205, 50),
                 (218, 165, 32), (138, 43, 226), (95, 158, 160), (255, 127, 80), (0, 191, 255)]

# Second safety gate behind the AI pick: slurs/hate/sexual/doxx patterns never reach the screen.
_BLOCK = re.compile(r"(n[i1!]gg|f[a@]gg?[o0]t|f[a@]g\b|r[e3]t[a@]rd|tr[a@]nn|k[i1]ke|ch[i1]nk|sp[i1]c\b|"
                    r"wetback|c[o0]{2}n\b|kys\b|kill\s*yo?urself|rape|r[a@]p[i1]st|porn|nude|n[u\*]d[e3]s|"
                    r"onlyfans|cum\b|dick|pussy|tits|whore|slut|https?://|www\.|\.com\b|@\w+|"
                    r"\b\d{3}[\s.-]?\d{3}[\s.-]?\d{4}\b)", re.I)


def clean(text: str, max_len: int = 34) -> str:
    """Drop emoji / symbols Arial can't draw, collapse spaces, shorten."""
    out = "".join(ch for ch in text if unicodedata.category(ch)[0] in "LNPZ" or ch in "?!'\".,:;-+()#&%$")
    out = re.sub(r"\s+", " ", out).strip()
    words = out.split()
    if len(words) >= 3 and len(set(w.lower() for w in words)) == 1:  # "KEKW KEKW KEKW KEKW"
        out = " ".join(words[:3])
    return out if len(out) <= max_len else out[: max_len - 1].rstrip() + "…"


def safe(text: str) -> bool:
    return bool(text) and not _BLOCK.search(text)


def color_for(user: str) -> tuple:
    return TWITCH_COLORS[int(hashlib.md5(user.lower().encode()).hexdigest(), 16) % len(TWITCH_COLORS)]


def select(chat_timed: list[dict], picks: list[str] | None, spike_t: float, n: int = 6,
           window: float = 6.0) -> list[dict]:
    """Messages to show: AI picks first (matched to real messages), else hype-heavy lines near the spike.
    Returns [{"t","user","text"}] sorted by time, each cleaned and safety-checked."""
    pool = [m for m in chat_timed if safe(m["text"]) and len(clean(m["text"])) >= 1]
    chosen: list[dict] = []
    for p in picks or []:
        if ":" not in p:
            continue
        user, text = (x.strip() for x in p.split(":", 1))
        hit = next((m for m in pool if m["user"].lower() == user.lower() and m["text"].strip() == text), None)
        if hit and hit not in chosen:
            chosen.append(hit)
    if len(chosen) < 3:  # fallback: distinct hype lines right after the spike
        seen = {c["text"].lower() for c in chosen}
        for m in sorted(pool, key=lambda m: abs(m["t"] - (spike_t + 1.5))):
            k = clean(m["text"]).lower()
            if k not in seen and spike_t - 1 <= m["t"] <= spike_t + window:
                chosen.append(m)
                seen.add(k)
            if len(chosen) >= n:
                break
    chosen = sorted(chosen, key=lambda m: m["t"])[:n]
    return [{**m, "text": clean(m["text"])} for m in chosen]


CHAT_LEAD_S = 1.0     # chat starts just before the payoff peaks
CHAT_WINDOW_S = 9.0   # how long the burst keeps building after the spike
CHAT_GAP_S = 0.8      # min time between two new lines (readability)


def schedule(msgs: list[dict], spike_t: float, duration: float, window: float = CHAT_WINDOW_S,
             has_timestamps: bool = True, lead: float = CHAT_LEAD_S, gap: float = CHAT_GAP_S) -> list[dict]:
    """Assign on-screen times inside [spike_t-lead, spike_t+window] (real times when we have them).
    Lines stay up until the clip ends (or 1.5 s after the window)."""
    if not msgs:
        return []
    start, end = max(0.0, spike_t - lead), min(duration - 0.2, spike_t + window)
    if end <= start:
        return []
    if has_timestamps:
        ts = [min(max(m["t"], start), end - 0.3) for m in msgs]
        # keep them at least `gap` apart so each line is readable
        for i in range(1, len(ts)):
            ts[i] = max(ts[i], ts[i - 1] + gap)
    else:
        step = (end - start - 0.4) / max(1, len(msgs))
        ts = [start + i * step for i in range(len(msgs))]
    hide = round(min(duration, end + 1.5), 2)
    return [{**m, "show": round(t, 2), "hide": hide} for m, t in zip(msgs, ts) if t < end]


def draw_chat(img: Image.Image, lines: list[dict], box: tuple[int, int, int, int], max_lines: int = 5) -> None:
    """Twitch-looking chat box: translucent dark panel, colored bold usernames, white text, newest at bottom."""
    if not lines:
        return
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = ImageFont.truetype(FONT_BOLD, 40)
    lh = 58
    lines = lines[-max_lines:]
    h = lh * len(lines) + 36
    top = y1 - h
    d.rounded_rectangle([x0, top, x1 - 1, y1 - 1], radius=22, fill=(14, 14, 18, 190), outline=(145, 70, 255, 220), width=4)
    for i, m in enumerate(lines):
        y = top + 18 + i * lh
        name = m["user"][:18] + ":"
        d.text((x0 + 26, y), name, font=f, fill=color_for(m["user"]) + (255,))
        nx = x0 + 26 + d.textlength(name + " ", font=f)
        text, room = m["text"], x1 - 22 - nx
        while d.textlength(text, font=f) > room and len(text) > 2:
            text = text[:-2].rstrip() + "…"
        d.text((nx, y), text, font=f, fill=(255, 255, 255, 255))
    img.alpha_composite(layer)
