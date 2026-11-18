import subprocess

import numpy as np
import pytest

from clipfarm import LENGTH_PRESETS, clip_window
from clipfarm.edit import CAPTION_PRESETS, Word, chunk, render_overlay
from clipfarm.score import audio_jump, audio_points, chat_points, moment_score


def test_chat_points_scale():
    assert chat_points(1.0, 100, 0) == 0
    assert chat_points(6.0, 100, 50) == 60
    assert 0 < chat_points(3.0, 100, 10) < chat_points(5.0, 100, 10)


def test_audio_points_scale():
    assert audio_points(None) == 0
    assert audio_points(5) == 0
    assert audio_points(18) == 40
    assert 0 < audio_points(12) < 40


def test_audio_jump_finds_peak_in_focus():
    db = np.full(80, -30.0)
    db[10] = -5  # loud but outside focus
    db[60] = -12  # inside focus (30s..40s at 0.5s windows = idx 60..80)
    jump, at = audio_jump(db, focus=(30, 40))
    assert at == 30.0 and round(jump) == 18


def make_clip(path, loud_at=None, dur=20):
    """Sine tone clip, quiet; optional loud burst at loud_at seconds."""
    vol = "0.02" if loud_at is None else f"'if(between(t,{loud_at},{loud_at + 1}),0.9,0.02)'"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
                    "-f", "lavfi", "-i", f"color=c=black:s=320x180:d={dur}",
                    "-af", f"volume={vol}:eval=frame", "-shortest", str(path)], check=True)


def test_moment_score_rewards_loud_moment(tmp_path):
    quiet, loud = tmp_path / "q.mp4", tmp_path / "l.mp4"
    make_clip(quiet)
    make_clip(loud, loud_at=12)
    sq = moment_score(quiet, 4.0, 100, 30, focus=(5, 20))
    sl = moment_score(loud, 4.0, 100, 30, focus=(5, 20))
    assert sq.audio == 0
    assert sl.audio == 40 and sl.total > sq.total
    assert 11.5 <= sl.peak_at_s <= 13


def test_length_presets():
    assert clip_window({}) == LENGTH_PRESETS["auto"]
    assert clip_window({"length": "15-30"}) == (20, 6)
    assert clip_window({"length": "30-60", "post_s": 5}) == (40, 5)
    with pytest.raises(SystemExit):
        clip_window({"length": "bogus"})


def test_chunk_respects_words_per_line():
    ws = [Word(i * 0.3, i * 0.3 + 0.25, f"w{i}") for i in range(7)]
    assert [len(c) for c in chunk(ws, 2)] == [2, 2, 2, 1]
    assert [len(c) for c in chunk(ws, 3)] == [3, 3, 1]


@pytest.mark.parametrize("preset", list(CAPTION_PRESETS))
def test_render_overlay_presets(preset):
    ws = [Word(0, 0.3, "absolutely"), Word(0.3, 0.6, "insane")]
    img = render_overlay("Title here", ws, 1, 1400, 300, CAPTION_PRESETS[preset])
    assert img.size == (1080, 1920)
    assert img.getbbox() is not None  # something was drawn


def test_thumb_text_and_compose():
    from PIL import Image
    from clipfarm.thumb import ThumbSpec, compose, parse_words, text_block
    assert parse_words("he got *caught*") == [("HE", False), ("GOT", False), ("CAUGHT", True)]
    tb = text_block("HE GOT *CAUGHT*", 700, 360)
    assert tb.width <= 700 * 1.15 and tb.height <= 360 * 1.3  # rotated a few degrees
    frame = Image.new("RGB", (1280, 720), (40, 120, 200))
    for fmt, size in (("16:9", (1280, 720)), ("9:16", (1080, 1920))):
        assert compose(frame, ThumbSpec("NO WAY", fmt, facecam=(0, 0.69, 0.22, 0.31))).size == size
