import json
from pathlib import Path

import pytest

from clipfarm import post as P

CFG = {"user": "me", "platforms": ["tiktok", "youtube", "instagram"], "tags": ["GTA6"],
       "credit_template": "🎥 {streamer}"}


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "kaicenat" / "x_short.mp4"
    v.parent.mkdir()
    v.write_bytes(b"fake")
    return v


def test_queue_flow_needs_approval(tmp_path, video):
    root = tmp_path / "q"
    it = P.add(root, video, "Kai gets 5 stars", "kaicenat", CFG, score=90)
    assert [i.id for _, i in P.items(root, "pending")] == [it.id]  # no auto-approve by default
    assert P.post_all(root, CFG, dry_run=True) == []  # nothing approved -> nothing sent
    P.move(root, it.id, "pending", "approved")
    out = P.post_all(root, CFG, dry_run=True)
    assert out[0]["data"]["platform[]"] == ["tiktok", "youtube", "instagram"]
    assert out[0]["data"]["user"] == "me" and out[0]["data"]["categoryId"] == "20"
    assert [i.id for _, i in P.items(root, "approved")] == [it.id]  # dry run moves nothing


def test_auto_approve_only_above_score(tmp_path, video):
    root = tmp_path / "q"
    cfg = {**CFG, "auto_approve_min_score": 70}
    hi = P.add(root, video, "big", "kaicenat", cfg, score=80)
    lo = P.add(root, video, "small", "kaicenat", cfg, score=40)
    assert [i.id for _, i in P.items(root, "approved")] == [hi.id]
    assert [i.id for _, i in P.items(root, "pending")] == [lo.id]


def test_request_has_credit_idempotency_and_schedule(tmp_path, video):
    from datetime import datetime, timezone
    root = tmp_path / "q"
    it = P.add(root, video, "t", "xqc", CFG)
    d = root / "pending" / it.id
    h, data, files = P.build_request(d, it, CFG, datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc))
    assert h["Idempotency-Key"] == it.id
    assert "🎥 xqc" in data["youtube_description"] and "#xqc" in data["youtube_description"]
    assert data["scheduled_date"].startswith("2026-11-20T18:00")
    assert files["video"].name == "short.mp4"


def test_real_post_refuses_without_key(tmp_path, video, monkeypatch):
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        P.post_all(tmp_path / "q", CFG, dry_run=False)


def test_title_limit(tmp_path, video):
    with pytest.raises(SystemExit):
        P.add(tmp_path / "q", video, "x" * 101, "s", CFG)
