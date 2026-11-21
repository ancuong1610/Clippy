import json
import os
import time
from pathlib import Path

from clipfarm.cleanup import plan

OLD = time.time() - 10 * 86400


def _item(q: Path, state: str, iid: str, src: Path, old: bool = True) -> Path:
    d = q / state / iid
    d.mkdir(parents=True)
    (d / "post.json").write_text(json.dumps({"id": iid, "source_clip": str(src)}))
    (d / "short.mp4").write_bytes(b"x" * 10)
    if old:
        for p in d.iterdir():
            os.utime(p, (OLD, OLD))
    return d


def _clip(c: Path, name: str, old: bool = True) -> Path:
    p = c / "xqc" / f"{name}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"v" * 100)
    (c / "xqc" / f"{name}.json").write_text("{}")
    (c / "xqc" / "shorts").mkdir(exist_ok=True)
    (c / "xqc" / "shorts" / f"{name}_short.mp4").write_bytes(b"r")
    (c / "xqc" / "shorts" / f"{name}_short.json").write_text("{}")
    if old:
        os.utime(p, (OLD, OLD))
    return p


def test_cleanup_deletes_done_and_never_queued_but_spares_live_items(tmp_path):
    q, c = tmp_path / "queue", tmp_path / "clips"
    for s in ("approved", "pending", "failed", "posted", "skipped"):
        (q / s).mkdir(parents=True)
    posted, skipped, live, raw, fresh = (_clip(c, n) for n in ("posted", "skipped", "live", "raw", "x"))
    os.utime(fresh, None)  # recorded just now
    pd = _item(q, "posted", "p1", posted)
    sd = _item(q, "skipped", "s1", skipped)
    ad = _item(q, "approved", "a1", live)
    recent = _item(q, "posted", "p2", live, old=False)
    gone = {a.path for a in plan(q, c) if a.kind == "delete"}
    assert pd / "short.mp4" in gone and sd / "short.mp4" in gone
    assert c / "xqc" / "shorts" / "posted_short.mp4" in gone          # render of a finished clip
    assert raw in gone and c / "xqc" / "shorts" / "raw_short.mp4" in gone
    assert posted not in gone and skipped not in gone                  # labelled examples: shrunk, not deleted
    assert ad / "short.mp4" not in gone and live not in gone           # approved item untouched
    assert recent / "short.mp4" not in gone                            # posted < 3 days: manual TikTok/IG window
    assert fresh not in gone
    assert not any(p.suffix == ".json" for p in gone)


def test_cleanup_keeps_video_still_waiting_for_youtube(tmp_path):
    q, c = tmp_path / "queue", tmp_path / "clips"
    for s in ("approved", "pending", "failed", "posted", "skipped"):
        (q / s).mkdir(parents=True)
    d = _item(q, "posted", "p1", _clip(c, "a"))
    (d / "result.json").write_text(json.dumps({"youtube_pending": True, "platforms": {}}))
    os.utime(d / "result.json", (OLD, OLD))
    assert d / "short.mp4" not in {a.path for a in plan(q, c)}
