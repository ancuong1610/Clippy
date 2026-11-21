from clipfarm.face import facecam_box
from clipfarm.make import shift_words
from clipfarm.titles import apply_fixes, pick_window, timestamped

W = [{"start": i * 1.0, "end": i * 1.0 + 0.8, "text": t} for i, t in
     enumerate("i played fortnight before everybody . nier automator wins as a game".split())]


def test_facecam_picks_stable_corner_face_not_moving_game_faces():
    frames = []
    for i in range(10):
        faces = [(0.05, 0.05, 0.05, 0.09)]                     # webcam, top-left, every frame
        if i % 3 == 0:
            faces.append((0.3 + i * 0.05, 0.4, 0.06, 0.1))     # face in the game, moving
        frames.append(faces)
    (x, y, w, h), focus = facecam_box(frames)
    assert x < 0.1 and y < 0.1 and 0.1 < w < 0.3 and 0 < focus[0] < 1


def test_facecam_none_when_no_stable_face():
    frames = [[(0.1 * i, 0.5, 0.05, 0.09)] for i in range(10)]
    assert facecam_box(frames) is None


def test_apply_fixes_keeps_punctuation():
    out = apply_fixes([{"start": 0, "end": 1, "text": "fortnight,"}], [["fortnight", "Fortnite"], ["two words", "x"]])
    assert out[0]["text"] == "Fortnite,"


def test_pick_window_snaps_and_falls_back():
    s, e = pick_window({"best_start": 2.2, "best_end": 9.6}, 11, W, min_len=5)
    assert s == 1.75 and e == 10.3
    assert pick_window({"best_start": 0, "best_end": 2}, 11, W) == (0.0, 11)          # too short
    assert pick_window({"best_start": "x"}, 11, W) == (0.0, 11)


def test_shift_words_and_timestamped():
    ws = shift_words(W, 3.0, 7.9)
    assert ws[0].start == 0.0 and ws[0].text == "before" and all(w.end <= 5.2 for w in ws)
    assert timestamped(W[:4], every=2).startswith("[0.0] i played [2.0] fortnight")


def test_facecam_crop_stops_at_cam_border():
    """Cam (static) with a hard bottom border and changing 'chat overlay' text under it: crop must end at the border."""
    import numpy as np

    from clipfarm.face import snap_to_cam
    rng = np.random.default_rng(0)
    frames = []
    for _ in range(8):
        f = np.full((720, 1280, 3), 90, np.uint8)
        f[200:360, 40:260] = 140                       # cam area (static room)
        f[360:364, 40:260] = 250                       # cam bottom border line at y=360
        f[370:460, 40:260] = rng.integers(0, 255, (90, 220, 3))  # chat overlay below: changes every frame
        frames.append(f)
    box = (40 / 1280, 200 / 720, 220 / 1280, 260 / 720)   # face-multiple crop: runs to y=460, into the chat
    face = (120 / 1280, 250 / 720, 50 / 1280, 60 / 720)
    x, y, w, h = snap_to_cam(frames, box, face)
    assert (y + h) * 720 <= 364  # ends at the border, not in the chat
    assert w * 1280 >= 2.2 * 50  # still head-and-shoulders, not a face-only crop


def test_hook_tease_ends_before_the_loudest_moment():
    from clipfarm.make import tease_start
    assert tease_start(10.0, 1.5) == 7.5  # 7.5-9.0 s: build-up, stops 1 s before the scream/crash
    assert tease_start(10.0, 1.5, lead=0.5) == 8.0
    assert tease_start(1.0, 1.5) == 0.0  # never before the clip starts
