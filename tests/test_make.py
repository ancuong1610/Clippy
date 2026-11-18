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
