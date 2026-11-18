"""Chat-spike detector.

Scores a rolling window of chat against an adaptive (EWMA) baseline. Works the same
for small and huge chats because the trigger is a *ratio* to the channel's own normal.
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
