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


def test_rate_limit_per_hour():
    ap = AutoPoster(CFG)
    ap.posted.extend([1000.0, 1500.0])
    assert "rate limit" in ap.decide({"autopost_risks": []}, now=2000.0)
    assert ap.decide({"autopost_risks": []}, now=1000.0 + 3601) is None  # oldest expired


def test_upload_limit_pauses_posting():
    from clipfarm.autopost import is_upload_limit
    err = ("YouTube upload quota exceeded: The user has exceeded the number of videos they may upload.. "
           "You have reached your daily video upload limit. Please wait 24 hours.")
    assert is_upload_limit(err) and not is_upload_limit("network timeout")
    ap = AutoPoster(CFG)
    ap.paused_until = 5000.0
    assert "daily upload limit" in ap.decide({"autopost_risks": []}, now=4000.0)
    assert ap.decide({"autopost_risks": []}, now=5001.0) is None


def test_backlog_respects_pause_and_cap(tmp_path, monkeypatch):
    from clipfarm import post as P
    v = tmp_path / "x_short.mp4"
    v.write_bytes(b"fake")
    root = tmp_path / "q"
    ap = AutoPoster({"post": {"auto_post_min_score": 60, "auto_post_max_per_hour": 1, "queue_dir": str(root)}})
    for sc in (61, 90):
        v.write_bytes(f"fake {sc}".encode())  # distinct videos (identical ones are deduped)
        it = P.add(root, v, f"clip {sc}", "xqc", {}, score=sc)
        P.move(root, it.id, "pending", "approved")
    sent = []
    monkeypatch.setattr(ap, "_upload", lambda i, s, t: sent.append(t) or ap.posted.append(9e9) or {})
    ap.paused_until = 9e18
    ap.flush_backlog()
    assert sent == []  # paused: nothing
    ap.paused_until = 0
    ap.flush_backlog()
    assert sent == ["clip 90"]  # best score first, then the 1/hour cap stops it


def test_judge_chat_sample_keeps_times_and_limit():
    from clipfarm.judge import chat_lines
    meta = {"chat_timed": [{"t": i / 2, "user": f"u{i}", "text": "o7 crash"} for i in range(400)]}
    lines = chat_lines(meta, limit=80).splitlines()
    assert len(lines) == 80 and lines[0].startswith("[0] u0:") and "o7 crash" in lines[-1]
    assert chat_lines({}) == "(no chat)"
