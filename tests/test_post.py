
import pytest

from clipfarm import post as P

CFG = {"platforms": ["tiktok", "youtube", "instagram"], "tags": ["GTA6"],
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
    assert out[0]["platforms"]["youtube"]["args"]["categoryId"] == "20"
    assert out[0]["manual"] == ["tiktok", "instagram"]
    assert [i.id for _, i in P.items(root, "approved")] == [it.id]  # dry run moves nothing


def test_auto_approve_only_above_score(tmp_path, video):
    root = tmp_path / "q"
    cfg = {**CFG, "auto_approve_min_score": 70}
    hi = P.add(root, video, "big", "kaicenat", cfg, score=80)
    lo = P.add(root, video, "small", "kaicenat", cfg, score=40)
    assert [i.id for _, i in P.items(root, "approved")] == [hi.id]
    assert [i.id for _, i in P.items(root, "pending")] == [lo.id]


def test_youtube_args_have_credit_and_tags(tmp_path, video):
    it = P.add(tmp_path / "q", video, "t", "xqc", CFG)
    args = P.youtube_args(it, CFG)
    assert "🎥 xqc" in args["description"] and "#xqc" in args["description"]
    assert args["privacyStatus"] == "public" and "xqc" in args["tags"]


def test_real_post_refuses_without_key(tmp_path, video, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        P.post_all(tmp_path / "q", CFG, dry_run=False)


def test_title_limit(tmp_path, video):
    with pytest.raises(SystemExit):
        P.add(tmp_path / "q", video, "x" * 101, "s", CFG)


def test_composio_dry_run_youtube_auto_others_manual(tmp_path, video):
    root = tmp_path / "q"
    cfg = {**CFG, "youtube_privacy": "unlisted"}
    titles = {"youtube_description": "xQc says he made Fortnite. Credit xQc #xQc",
              "tiktok_caption": "xQc made Fortnite?? #xQc", "instagram_caption": "IG cap #xQc"}
    it = P.add(root, video, "xQc Claims He Made Fortnite", "xqc", cfg, titles=titles)
    P.move(root, it.id, "pending", "approved")
    out = P.post_composio(root, cfg, dry_run=True)
    yt = out[0]["platforms"]["youtube"]["args"]
    assert yt["title"] == "xQc Claims He Made Fortnite" and yt["privacyStatus"] == "unlisted"
    assert yt["description"].startswith("xQc says he made Fortnite") and yt["categoryId"] == "20"
    assert out[0]["manual"] == ["tiktok", "instagram"]
    d = root / "approved" / it.id
    assert P.manual_pack(d, P.items(root, "approved")[0][1], cfg, ["tiktok"]) == ["manual_tiktok.txt"]
    assert (d / "manual_tiktok.txt").read_text().startswith("xQc made Fortnite??")


def test_titles_validate():
    from clipfarm.titles import build_prompt, validate
    t = validate({"youtube_titles": ["a" * 150, " b ", ""], "thumbnail_text": "he got *caught* today ok"})
    assert len(t["youtube_titles"][0]) == 100 and t["youtube_titles"][1] == "b"
    assert t["thumbnail_text"] == "HE GOT *CAUGHT* TODAY"
    p = build_prompt("xQc", "GTA 6", "hello world", ["u: KEKW"])
    assert "xQc" in p and "u: KEKW" in p and "TRUE to the clip" in p
