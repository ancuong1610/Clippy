from clipfarm.autopost import AutoPoster
from clipfarm.titles import validate

CFG = {"post": {"auto_post_min_score": 70, "auto_post_max_per_hour": 2, "queue_dir": "q"}}


def test_disabled_without_threshold():
    assert not AutoPoster({"post": {}}).enabled and AutoPoster(CFG).enabled


def test_risks_block_autopost():
    ap = AutoPoster(CFG)
    assert ap.decide({"autopost_risks": []}) is None
    assert "hate" in ap.decide({"autopost_risks": ["hate"]})  # music alone no longer blocks (owner)
    assert "unchecked" in ap.decide({})  # no AI check -> human review


def test_garbled_risk_field_fails_closed():
    t = validate({"youtube_titles": ["x"], "thumbnail_text": "HE GOT *CAUGHT*", "autopost_risks": "none"})
    assert t["autopost_risks"] == ["unchecked"]
    assert validate({"youtube_titles": ["x"], "thumbnail_text": "HE GOT *CAUGHT*", "autopost_risks": []})["autopost_risks"] == []


def test_judge_chat_sample_keeps_times_and_limit():
    from clipfarm.judge import chat_lines
    meta = {"chat_timed": [{"t": i / 2, "user": f"u{i}", "text": "o7 crash"} for i in range(400)]}
    lines = chat_lines(meta, limit=80).splitlines()
    assert len(lines) == 80 and lines[0].startswith("[0] u0:") and "o7 crash" in lines[-1]
    assert chat_lines({}) == "(no chat)"


def test_spacing_hourly_and_daily_caps(tmp_path):
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "auto_post_max_per_hour": 3, "auto_post_max_per_day": 4,
                              "auto_post_min_gap_min": 10, "queue_dir": str(tmp_path / "q")}})
    t0 = 10_000
    assert ap.wait_reason(now=t0) is None
    ap.posted.append(t0)
    assert "spacing" in ap.wait_reason(now=t0 + 9 * 60)
    assert ap.wait_reason(now=t0 + 11 * 60) is None
    ap.posted.extend([t0 + 10 * 60, t0 + 20 * 60])
    assert "rate limit" in ap.wait_reason(now=t0 + 31 * 60)  # 3 in the last hour
    assert ap.wait_reason(now=t0 + 61 * 60) is None
    ap.posted.append(t0 + 61 * 60)
    assert "daily cap" in ap.wait_reason(now=t0 + 5 * 3600)  # 4 in 24 h
    assert ap.wait_reason(now=t0 + 86_400 + 21 * 60) is None  # first ones expired


def test_upload_limit_pauses_posting(tmp_path):
    from clipfarm.autopost import is_upload_limit
    err = ("YouTube upload quota exceeded: The user has exceeded the number of videos they may upload.. "
           "You have reached your daily video upload limit. Please wait 24 hours.")
    assert is_upload_limit(err) and not is_upload_limit("network timeout")
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "queue_dir": str(tmp_path / "q")}})
    ap.paused_until = 5000.0
    assert "daily upload limit" in ap.wait_reason(now=4000.0)
    assert ap.wait_reason(now=5001.0) is None


def test_rolling_poster_posts_best_first_one_at_a_time(tmp_path, monkeypatch):
    from clipfarm import post as P
    v = tmp_path / "x_short.mp4"
    root = tmp_path / "q"
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "auto_post_min_gap_min": 20, "queue_dir": str(root)}})
    for sc in (91, 99, 95):
        v.write_bytes(f"fake {sc}".encode())
        it = P.add(root, v, f"clip {sc}", "xqc", {}, score=sc)
        P.move(root, it.id, "pending", "approved")
    sent = []

    def fake_upload(i, s, t):
        sent.append(t)
        ap.posted.append(now[0])
        P.move(root, i, "approved", "posted")
        return {"posted": True}
    monkeypatch.setattr(ap, "_upload", fake_upload)
    now = [100_000.0]
    ap.post_next(now[0])
    ap.post_next(now[0] + 60)  # too soon: spacing
    assert sent == ["clip 99"]
    now[0] += 21 * 60
    ap.post_next(now[0])
    assert sent == ["clip 99", "clip 95"]  # best first, one per gap
    ap.paused_until = 9e18
    now[0] += 21 * 60
    assert ap.post_next(now[0]) is None and sent == ["clip 99", "clip 95"]  # paused: nothing



def test_music_allowed_but_lyrics_never_captioned():
    from clipfarm.titles import drop_lyrics, validate
    ap = AutoPoster(CFG)
    assert ap.decide({"autopost_risks": ["music"]}) is None
    assert "sexual" in ap.decide({"autopost_risks": ["music", "sexual"]})
    words = [{"start": 1.0, "end": 1.4, "text": "yo"}, {"start": 5.0, "end": 5.4, "text": "unwritten"},
             {"start": 9.0, "end": 9.3, "text": "bro"}]
    assert [w["text"] for w in drop_lyrics(words, [[4.0, 6.0]])] == ["yo", "bro"]
    t = validate({"youtube_titles": ["x"], "thumbnail_text": "A *B*", "lyric_spans": [[4, 6], ["x", 1], [9, 8]]})
    assert t["lyric_spans"] == [[4.0, 6.0]]


def test_one_clip_per_scene(tmp_path):
    from clipfarm import post as P
    root = tmp_path / "q"
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "auto_post_scene_min": 3, "queue_dir": str(root)}})
    v = tmp_path / "s.mp4"
    ids = {}
    for stamp, streamer, sc in [("20261118-181600", "xqc", 90), ("20261118-181650", "xqc", 97),
                                ("20261118-181746", "xqc", 97), ("20261118-180921", "xqc", 100),
                                ("20261118-181700", "kaicenat", 91)]:
        v.write_bytes(f"{stamp}{streamer}".encode())
        it = P.add(root, v, f"{streamer} {stamp}", streamer, {}, score=sc,
                   source_clip=f"clips/{streamer}/{stamp}_s50_h{sc}.mp4")
        P.move(root, it.id, "pending", "approved")
        ids[stamp + streamer] = it.id
    skipped = ap.dedupe_scenes()
    left = {it.title for _, it in P.items(root, "approved")}
    # bank scene (3 xQc clips within 2.5 min) -> one 97 kept; the carjack (7 min earlier) and Kai stay
    assert len(skipped) == 2 and "xqc 20261118-180921" in left and "kaicenat 20261118-181700" in left
    bank = {"xqc 20261118-181600", "xqc 20261118-181650", "xqc 20261118-181746"}
    assert len(left & bank) == 1
    assert "xqc 20261118-181600" not in left  # the 90 lost to the 97s


def test_window_and_jitter():
    import time as _t
    from clipfarm.autopost import in_window, parse_window
    assert parse_window("00:30-03:30") == (30, 210)
    t = _t.mktime((2026, 11, 20, 1, 0, 0, 0, 0, -1))
    assert in_window((30, 210), t) and not in_window((30, 210), t + 3 * 3600)
    assert in_window(parse_window("22:00-02:00"), t)  # crosses midnight
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "auto_post_window": "00:30-03:30",
                              "auto_post_jitter_min": 60, "queue_dir": "/nonexistent"}})
    ap.posted.clear()
    assert "outside posting window" in ap.wait_reason(now=t + 3 * 3600)
    r = ap.wait_reason(now=t)
    assert r is None or "random delay" in r
    assert ap.wait_reason(now=t + 3600 + 1) is None  # jitter is at most 60 min


def test_youtube_tags_include_streamer():
    from clipfarm.post import PostItem, youtube_args
    it = PostItem(id="x", title="t", streamer="xQc", platforms=["youtube"], tags=["GTA6", "xQc"],
                  youtube_description="desc")
    a = youtube_args(it, {})
    assert "xQc GTA 6" in a["tags"] and len(a["tags"]) == len(set(a["tags"]))
    assert a["description"].endswith("#GTA6 #xQc #Shorts")


def test_slot_rotation():
    import time as _t
    from clipfarm.autopost import in_window, slot_window
    slots = ["19:00", "22:00", "01:00"]
    d1 = _t.mktime((2026, 11, 22, 19, 30, 0, 0, 0, -1))
    seen = {slot_window(slots, 60, d1 + k * 86400)[0] for k in range(3)}
    assert seen == set(slots)  # three days cycle through all slots
    s, w = slot_window(slots, 60, d1)
    late = d1 + 5.5 * 3600  # 01:00 the same night belongs to the same posting day
    assert slot_window(slots, 60, late)[0] == s
    h = int(s[:2])
    assert in_window(w, _t.mktime((2026, 11, 22 if h > 6 else 23, h, 30, 0, 0, 0, -1)))


def test_slot_cap_is_per_day():
    import time as _t
    ap = AutoPoster({"post": {"auto_post_min_score": 90, "auto_post_slots": ["19:00", "22:00", "01:00"],
                              "auto_post_max_per_day": 4, "auto_post_min_gap_min": 10, "auto_post_max_per_hour": 6,
                              "queue_dir": "/nonexistent"}})
    ap.posted.clear()
    t = _t.time()
    ap.posted.extend([t - 18 * 3600 + k * 600 for k in range(4)])  # yesterday's batch, 18 h ago
    r = ap.wait_reason(now=t)
    assert r is None or "daily cap" not in r
