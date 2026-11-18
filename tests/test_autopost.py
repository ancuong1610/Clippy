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
