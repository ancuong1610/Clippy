"""Chat-spike detector.

Two triggers, both relative to the channel's own normal (adaptive EWMA baselines):
- volume: chat suddenly gets much faster (works for small and mid-size chats);
- wave:   a big share of chatters type the same word/emote at once ("OOOO", "HUH", "KEKW").
  Huge chats (xQc, Kai) are capped at ~12 msg/s by Twitch, so their volume can't spike —
  but the whole chat converging on one reaction still shows the moment.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field

DEFAULT_HYPE_TOKENS = {
    "kekw", "omegalul", "lul", "lmao", "lmfao", "lol", "w", "l", "pog", "poggers",
    "pogchamp", "pogu", "monkas", "clip", "clipit", "+2", "???", "nah", "wtf",
    "bro", "noway", "aware", "icant", "sadge", "deadass", "ratio", "💀", "😭", "😂",
}
_PHRASES = ("clip it", "clip that", "no way", "i cant", "i can't", "+2")
_WORD_RE = re.compile(r"[\w+?💀😭😂]+", re.UNICODE)
# Reactions spelled many ways count as ONE wave: half of chat laughing in 6 different emotes is still a laugh wave
_FAMILIES = {  # chat "moods": one reaction, many spellings
    "lol": {"lol", "lo", "lool", "loool", "lmao", "lmfao", "kekw", "omegalul", "icant", "pepelaugh", "lul", "xd",
            "kek", "lulw", "ahahah", "hahaha", "haha", "😂", "🤣", "😭", "💀"},
    "shock": {"wtf", "huh", "???", "monkas", "monka", "omg", "noway", "holy", "nahh", "d:",
              "omfg", "😱", "😳"},
    "hype": {"pog", "poggers", "pogu", "pogchamp", "letsgo", "w", "ez", "gigachad", "oooo", "ooooo", "pagman",
             "wicked", "insane", "clean", "goat", "🔥"},
    "o7": {"o7", "f", "rip", "ripbozo", "l", "gg"},
    "cringe": {"eww", "ew", "cringe", "weirdchamp", "yikes"},
    "clip": {"clip", "clipit", "clipped", "clips"},
}
_FAMILY_OF = {w: fam for fam, words in _FAMILIES.items() for w in words}
_STOP = {"the", "a", "an", "to", "is", "it", "he", "she", "you", "and", "of", "in", "on", "that", "this",
         "me", "my", "so", "for", "be", "was", "at", "just", "like", "what", "his", "her", "him", "we",
         "are", "not", "do", "no", "yes", "but", "if", "with", "has", "have", "they", "im", "u", "?"}


@dataclass
class ChatMessage:
    ts: float  # wall-clock epoch seconds (or VOD offset in offline mode)
    user: str
    text: str


@dataclass
class HypeEvent:
    channel: str
    ts: float  # time the spike was detected (chat exploded)
    score: float
    baseline: float
    ratio: float
    unique_users: int
    msgs: int
    hype_msgs: int = 0  # messages in the window containing a hype token
    top_tokens: list[tuple[str, int]] = field(default_factory=list)
    trigger: str = "volume"  # or "wave"
    wave_token: str = ""
    wave_share: float = 0.0


def tokens(text: str) -> frozenset[str]:
    """Distinct tokens of a message, with emote spellings folded into their mood ('KEKW' -> 'lol')."""
    return frozenset(_FAMILY_OF.get(t, t) for t in _WORD_RE.findall(text.lower())
                     if (len(t) >= 2 or t in _FAMILY_OF) and t not in _STOP)


MOODS = set(_FAMILIES) - {"clip"}


def mood_peak(chat_timed: list[dict], window: float = 10.0, min_users: int = 20) -> tuple[float, str]:
    """Strongest moment where chat answers in one mood: (share of chatters, mood) over any `window` seconds."""
    msgs = sorted(chat_timed, key=lambda m: m["t"])
    best, mood = 0.0, ""
    j = 0
    for i in range(len(msgs)):
        while msgs[j]["t"] < msgs[i]["t"] - window:
            j += 1
        win = msgs[j:i + 1]
        users = {m["user"] for m in win}
        if len(users) < min_users:
            continue
        by: dict[str, set[str]] = {}
        for m in win:
            for t in tokens(m["text"]) & MOODS:
                by.setdefault(t, set()).add(m["user"])
        for t, us in by.items():
            if len(us) / len(users) > best:
                best, mood = len(us) / len(users), t
    return round(best, 2), mood


def unison_bonus(share: float, lo: float = 0.35, hi: float = 0.60, max_pts: float = 15.0) -> float:
    """Chat answering as one (35 % -> 0 … 60 %+ of chatters in one mood -> +15)."""
    return round(min(max((share - lo) / (hi - lo), 0.0), 1.0) * max_pts, 1)


@dataclass
class DetectorConfig:
    window_s: float = 5.0  # rolling window scored each tick
    baseline_tau_s: float = 180.0  # EWMA time constant (~3 min memory)
    ratio: float = 3.0  # trigger when score >= ratio * baseline
    min_score: float = 8.0  # absolute floor so a quiet chat doesn't trigger on 3 msgs
    min_unique: int = 5  # different chatters in the window
    cooldown_s: float = 45.0
    warmup_s: float = 60.0  # learn a baseline before triggering
    hype_weight: float = 0.75  # extra weight for messages containing hype tokens
    caps_weight: float = 0.25  # extra weight for SHOUTING
    hype_tokens: set[str] = field(default_factory=lambda: set(DEFAULT_HYPE_TOKENS))
    # wave trigger (share of chatters typing the same token in wave_window_s)
    wave_window_s: float = 10.0
    wave_min_share: float = 0.30  # >= 30 % of chatters on one token ...
    wave_ratio: float = 2.0  # ... and >= 2x the usual top-token share
    wave_min_users: int = 20  # only meaningful in busy chats


def message_weight(text: str, cfg: DetectorConfig) -> tuple[float, list[str]]:
    low = text.lower()
    words = _WORD_RE.findall(low)
    hits = [w for w in words if w in cfg.hype_tokens]
    hits += [p for p in _PHRASES if p in low]
    w = 1.0
    if hits:
        w += cfg.hype_weight
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        w += cfg.caps_weight
    return w, hits


class SpikeDetector:
    def __init__(self, channel: str, cfg: DetectorConfig | None = None):
        self.channel = channel
        self.cfg = cfg or DetectorConfig()
        self._win: deque[tuple[float, str, float, list[str]]] = deque()
        self._wave: deque[tuple[float, str, frozenset[str]]] = deque()
        self._wave_base: float | None = None
        self._baseline: float | None = None
        self._last_tick: float | None = None
        self._start: float | None = None
        self._last_event: float = -math.inf

    @property
    def baseline(self) -> float:
        return self._baseline or 0.0

    def add(self, msg: ChatMessage) -> None:
        if self._start is None:
            self._start = msg.ts
        w, hits = message_weight(msg.text, self.cfg)
        self._win.append((msg.ts, msg.user, w, hits))
        self._wave.append((msg.ts, msg.user, tokens(msg.text)))

    def _wave_score(self, now: float) -> tuple[float, str, int]:
        """Largest share of distinct chatters who used the same token in the wave window."""
        cutoff = now - self.cfg.wave_window_s
        while self._wave and self._wave[0][0] < cutoff:
            self._wave.popleft()
        users: set[str] = set()
        by_tok: dict[str, set[str]] = {}
        for ts, user, toks in self._wave:
            if ts > now:
                continue
            users.add(user)
            for t in toks:
                by_tok.setdefault(t, set()).add(user)
        if not by_tok or not users:
            return 0.0, "", len(users)
        tok, us = max(by_tok.items(), key=lambda kv: len(kv[1]))
        return len(us) / len(users), tok, len(users)

    def _score(self, now: float) -> tuple[float, int, int, dict[str, int], int]:
        cutoff = now - self.cfg.window_s
        while self._win and self._win[0][0] < cutoff:
            self._win.popleft()
        score, users, tokens = 0.0, set(), {}
        n = hype = 0
        for ts, user, w, hits in self._win:
            if ts > now:
                continue
            n += 1
            score += w
            users.add(user)
            hype += bool(hits)
            for h in hits:
                tokens[h] = tokens.get(h, 0) + 1
        return score, len(users), n, tokens, hype

    def tick(self, now: float) -> HypeEvent | None:
        """Call regularly (e.g. every 1s). Returns an event when chat explodes."""
        if self._start is None:
            self._start = now
        score, uniq, n, tokens, hype = self._score(now)
        cfg = self.cfg

        event = None
        base = self._baseline
        warmed = now - self._start >= cfg.warmup_s
        if (
            base is not None
            and warmed
            and score >= cfg.min_score
            and uniq >= cfg.min_unique
            and score >= cfg.ratio * max(base, 1e-6)
            and now - self._last_event >= cfg.cooldown_s
        ):
            self._last_event = now
            event = HypeEvent(
                channel=self.channel, ts=now, score=round(score, 2), baseline=round(base, 2),
                ratio=round(score / max(base, 1e-6), 2), unique_users=uniq, msgs=n, hype_msgs=hype,
                top_tokens=sorted(tokens.items(), key=lambda kv: -kv[1])[:5],
            )

        share, tok, wusers = self._wave_score(now)
        wb = self._wave_base
        if (
            event is None
            and wb is not None
            and warmed
            and wusers >= cfg.wave_min_users
            and share >= cfg.wave_min_share
            and share >= cfg.wave_ratio * max(wb, 0.02)
            and now - self._last_event >= cfg.cooldown_s
        ):
            self._last_event = now
            event = HypeEvent(
                channel=self.channel, ts=now, score=round(score, 2), baseline=round(base or 0, 2),
                ratio=round(share / max(wb, 0.02), 2), unique_users=uniq, msgs=n, hype_msgs=hype,
                top_tokens=sorted(tokens.items(), key=lambda kv: -kv[1])[:5],
                trigger="wave", wave_token=tok, wave_share=round(share, 2),
            )
        if wusers < cfg.wave_min_users:
            pass  # too few chatters for a share to mean anything (e.g. first seconds)
        elif wb is None:
            self._wave_base = share
        else:
            dt = max(0.0, now - (self._last_tick or now))
            alpha = 1 - math.exp(-dt / cfg.baseline_tau_s) if dt else 0.0
            self._wave_base = wb + alpha * (min(share, max(wb, 0.05) * 1.5) - wb)

        # Update baseline. During warm-up: plain running mean (learn fast).
        # After warm-up: slow EWMA with clamped input so a spike doesn't inflate "normal".
        if base is None:
            self._warm_n = 1
            self._baseline = score
        elif not warmed:
            self._warm_n += 1
            self._baseline = base + (score - base) / self._warm_n
        else:
            dt = max(0.0, now - (self._last_tick or now))
            alpha = 1 - math.exp(-dt / cfg.baseline_tau_s) if dt else 0.0
            clamped = min(score, max(base, 1.0) * 1.5)
            self._baseline = base + alpha * (clamped - base)
        self._last_tick = now
        return event


def detect_offline(messages: list[ChatMessage], channel: str = "vod",
                   cfg: DetectorConfig | None = None, step_s: float = 1.0) -> list[HypeEvent]:
    """Run the detector over a recorded chat log (timestamps = VOD offsets)."""
    if not messages:
        return []
    messages = sorted(messages, key=lambda m: m.ts)
    det = SpikeDetector(channel, cfg)
    events: list[HypeEvent] = []
    i, t = 0, messages[0].ts
    end = messages[-1].ts + step_s
    while t <= end:
        while i < len(messages) and messages[i].ts <= t:
            det.add(messages[i])
            i += 1
        ev = det.tick(t)
        if ev:
            events.append(ev)
        t += step_s
    return events
