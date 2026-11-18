"""Moment score 0-100 = chat signal + audio signal.

chat  (max 60): how far chat jumped over its own normal (ratio) + how hype the messages are
audio (max 40): loudest moment near the spike vs the clip's typical loudness (streamer screaming,
                explosion, crowd) — catches what chat alone can't confirm.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 8000  # mono 8 kHz is plenty for loudness


@dataclass
class MomentScore:
    total: int
    chat: float
    audio: float
    audio_jump_db: float | None
    peak_at_s: float | None  # loudest moment, seconds into the clip

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def audio_profile(video: Path, win_s: float = 0.5) -> np.ndarray:
    """Loudness (dBFS) per window. Empty array if the clip has no audio."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(SR * win_s)
    if len(pcm) < n * 4:
        return np.array([])
    frames = pcm[: len(pcm) // n * n].reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    return 20 * np.log10(rms)


def audio_jump(db: np.ndarray, focus: tuple[float, float] | None = None, win_s: float = 0.5
               ) -> tuple[float | None, float | None]:
    """(peak - median) in dB, searching the peak only inside `focus` (seconds) if given."""
    if db.size == 0:
        return None, None
    base = float(np.median(db))
    lo, hi = 0, db.size
    if focus:
        lo = max(0, int(focus[0] / win_s))
        hi = min(db.size, max(lo + 1, int(focus[1] / win_s)))
    i = lo + int(np.argmax(db[lo:hi]))
    return float(db[i] - base), i * win_s


def chat_points(ratio: float, msgs: int, hype_msgs: int, wave_share: float = 0.0) -> float:
    ratio_pts = min(max(ratio - 1.5, 0) / 4.5, 1.0) * 45  # 1.5x -> 0, 6x+ -> 45
    wave_pts = min(max(wave_share - 0.2, 0) / 0.4, 1.0) * 45  # 20 % of chatters -> 0, 60 %+ -> 45
    ratio_pts = max(ratio_pts, wave_pts)  # capped huge chats spike by converging, not by speed
    hype_frac = max(hype_msgs / msgs if msgs else 0.0, wave_share)
    hype_pts = min(hype_frac / 0.5, 1.0) * 15  # half the window hype -> full 15
    return ratio_pts + hype_pts


def audio_points(jump_db: float | None) -> float:
    if jump_db is None:
        return 0.0
    return min(max(jump_db - 6, 0) / 12, 1.0) * 40  # +6 dB -> 0, +18 dB -> 40


def moment_score(video: Path | None, ratio: float, msgs: int, hype_msgs: int,
                 focus: tuple[float, float] | None = None, wave_share: float = 0.0) -> MomentScore:
    jump = peak = None
    if video and Path(video).exists():
        jump, peak = audio_jump(audio_profile(Path(video)), focus)
    c, a = chat_points(ratio, msgs, hype_msgs, wave_share), audio_points(jump)
    return MomentScore(round(c + a), round(c, 1), round(a, 1),
                       None if jump is None else round(jump, 1), peak)
