from clipfarm.autopost import AutoPoster
from clipfarm.titles import validate

CFG = {"post": {"auto_post_min_score": 70, "auto_post_max_per_hour": 2, "queue_dir": "q"}}


def test_disabled_without_threshold():
    assert not AutoPoster({"post": {}}).enabled and AutoPoster(CFG).enabled


def test_risks_block_autopost():
    ap = AutoPoster(CFG)
    assert ap.decide({"autopost_risks": []}) is None
    assert "music" in ap.decide({"autopost_risks": ["music"]})
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

