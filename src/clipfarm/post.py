"""Posting queue -> YouTube Shorts via Composio; TikTok / Instagram get ready-to-paste caption files.

  queue/pending/<id>/   short.mp4 + post.json   (added by `clipfarm queue add`)
  queue/approved/<id>/  human said yes          (`clipfarm queue approve <id>`)
  queue/posted/<id>/    + result.json           (`clipfarm post`)
  queue/failed/<id>/    + result.json

Nothing is posted unless it sits in approved/. `auto_approve_min_score` (off by default) lets clips
above a moment score skip the manual step once you have locked the style.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATES = ("pending", "approved", "posted", "failed", "skipped")  # skipped: same scene as a better clip


@dataclass
class PostItem:
    id: str
    title: str
    streamer: str
    platforms: list[str]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    thumbnail: str | None = None  # file name inside the item folder
    score: int | None = None
    source_clip: str | None = None
    youtube_description: str | None = None  # from `clipfarm title`
    tiktok_caption: str | None = None
    instagram_caption: str | None = None
    video_hash: str | None = None  # sha1 of short.mp4: the same video is never queued twice
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def queue_dir(root: Path, state: str) -> Path:
    d = root / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def caption_for(item: PostItem, cfg: dict) -> str:
    credit = cfg.get("credit_template", "🎥 {streamer}").format(streamer=item.streamer)
    tags = " ".join("#" + t.lstrip("#") for t in item.tags)
    return "\n".join(x for x in (item.description, credit, tags) if x)


class DuplicateItem(ValueError):
    """The video (or its source clip) is already in the queue or was already posted."""


def file_hash(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate(root: Path, video_hash: str, source_clip: str | None) -> tuple[str, PostItem] | None:
    for st in STATES:
        for _, it in items(root, st):
            if it.video_hash == video_hash or (source_clip and it.source_clip == source_clip):
                return st, it
    return None


def add(root: Path, video: Path, title: str, streamer: str, cfg: dict, thumbnail: Path | None = None,
        score: int | None = None, source_clip: str | None = None, titles: dict | None = None) -> PostItem:
    if len(title) > 100:
        raise SystemExit("title > 100 chars (YouTube limit)")
    titles = titles or {}
    vh = file_hash(video)
    dup = find_duplicate(root, vh, source_clip)
    if dup:
        raise DuplicateItem(f"already {dup[0]}: {dup[1].id}  {dup[1].title}")
    item = PostItem(
        id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        title=title, streamer=streamer, platforms=list(cfg.get("platforms", ["tiktok", "youtube", "instagram"])),
        description=cfg.get("description", ""), tags=list(cfg.get("tags", [])) + [streamer],
        score=score, source_clip=source_clip,
        youtube_description=titles.get("youtube_description"),
        tiktok_caption=titles.get("tiktok_caption"), instagram_caption=titles.get("instagram_caption"),
        video_hash=vh)
    state = "pending"
    floor = cfg.get("auto_approve_min_score")
    if floor is not None and score is not None and score >= floor:
        state = "approved"
    d = queue_dir(root, state) / item.id
    d.mkdir()
    shutil.copy2(video, d / "short.mp4")
    if thumbnail:
        shutil.copy2(thumbnail, d / ("thumb" + thumbnail.suffix))
        item.thumbnail = "thumb" + thumbnail.suffix
    (d / "post.json").write_text(json.dumps(asdict(item), indent=2, ensure_ascii=False))
    return item


def items(root: Path, state: str) -> list[tuple[Path, PostItem]]:
    out = []
    for d in sorted(queue_dir(root, state).iterdir()):
        if (d / "post.json").exists():
            out.append((d, PostItem(**json.loads((d / "post.json").read_text()))))
    return out


def move(root: Path, item_id: str, src: str, dst: str) -> Path:
    s = queue_dir(root, src) / item_id
    if not s.exists():
        raise SystemExit(f"{item_id} not in {src}/")
    t = queue_dir(root, dst) / item_id
    s.rename(t)
    return t


COMPOSIO_USER = "stream-chaos-clips"
AUTO_PLATFORMS = {"youtube"}  # posted automatically; the rest get a manual caption pack


def youtube_args(item: PostItem, cfg: dict) -> dict:
    desc = item.youtube_description or caption_for(item, cfg)
    s = item.streamer
    if item.youtube_description and "#" not in desc:  # 3 hashtags: game, streamer, shorts
        desc += f"\n#GTA6 #{s.replace(' ', '')} #Shorts"
    tags = list(dict.fromkeys([*item.tags, f"{s} GTA 6", f"{s} clips", f"{s} highlights", "GTA 6", "GTA VI",
                               "GTA 6 gameplay", "GTA 6 funny moments"]))
    return {"title": item.title, "description": desc[:5000], "tags": tags,
            "categoryId": cfg.get("youtube_category", "20"),
            "privacyStatus": cfg.get("youtube_privacy", "public")}


def manual_pack(d: Path, item: PostItem, cfg: dict, platforms: list[str]) -> list[str]:
    """For platforms without auto-posting: write ready-to-paste captions next to the video."""
    written = []
    for p in platforms:
        cap = {"tiktok": item.tiktok_caption, "instagram": item.instagram_caption}.get(p) or \
            f"{item.title}\n{caption_for(item, cfg)}"
        f = d / f"manual_{p}.txt"
        f.write_text(cap + "\n")
        written.append(f.name)
    return written


def post_composio(root: Path, cfg: dict, dry_run: bool = False, only: str | None = None) -> list[dict]:
    """YouTube via Composio (free; verified public uploads 2026-11-18). Other platforms -> manual pack."""
    c = None
    if not dry_run:
        if not os.getenv("COMPOSIO_API_KEY"):
            raise SystemExit("COMPOSIO_API_KEY missing — put it in .env")
        from composio import Composio
        from composio.core.models._files import FileUploadable
        c = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    results = []
    for d, item in items(root, "approved"):
        if only and item.id != only:
            continue
        manual = [p for p in item.platforms if p not in AUTO_PLATFORMS]
        res: dict = {"id": item.id, "platforms": {}}
        if "youtube" in item.platforms:
            args = youtube_args(item, cfg)
            if dry_run:
                res["platforms"]["youtube"] = {"dry_run": True, "args": args}
            else:
                try:
                    fu = FileUploadable.from_path(c.client, str(d / "short.mp4"), tool="YOUTUBE_MULTIPART_UPLOAD_VIDEO",
                                                  toolkit="youtube", file_upload_allowlist=[d])
                    r = c.tools.execute("YOUTUBE_MULTIPART_UPLOAD_VIDEO", {**args, "videoFile": fu.model_dump()},
                                        user_id=cfg.get("composio_user", COMPOSIO_USER),
                                        dangerously_skip_version_check=True)
                    r = r if isinstance(r, dict) else r.model_dump()
                    vid = ((r.get("data") or {}).get("video") or {}).get("id")
                    res["platforms"]["youtube"] = {"ok": bool(r.get("successful") and vid), "video_id": vid,
                                                   "url": f"https://youtube.com/shorts/{vid}" if vid else None,
                                                   "error": r.get("error")}
                except Exception as e:  # noqa: BLE001
                    res["platforms"]["youtube"] = {"ok": False, "error": str(e)}
        if manual:
            res["manual"] = manual if dry_run else manual_pack(d, item, cfg, manual)
        if not dry_run:
            ok = all(v.get("ok") for v in res["platforms"].values())
            dest = move(root, item.id, "approved", "posted" if ok else "failed")
            (dest / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
            time.sleep(2)
        results.append(res)
    return results


def fetch_stats(root: Path, cfg: dict) -> list[dict]:
    """Views / likes / comments of every posted Short (YouTube via Composio); appended to queue/stats.jsonl."""
    if not os.getenv("COMPOSIO_API_KEY"):
        raise SystemExit("COMPOSIO_API_KEY missing — put it in .env")
    from composio import Composio
    c = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    posted = {}
    for d, item in items(root, "posted"):
        r = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
        vid = (r.get("platforms", {}).get("youtube") or {}).get("video_id")
        if vid:
            posted[vid] = item
    rows, ids, now = [], list(posted), time.time()
    for i in range(0, len(ids), 50):
        r = c.tools.execute("YOUTUBE_GET_VIDEO_DETAILS_BATCH", {"id": ids[i:i + 50], "parts": ["statistics", "snippet"]},
                            user_id=cfg.get("composio_user", COMPOSIO_USER), dangerously_skip_version_check=True)
        r = r if isinstance(r, dict) else r.model_dump()
        for v in (r.get("data") or {}).get("items") or []:
            st, it = v.get("statistics", {}), posted[v["id"]]
            rows.append({"ts": now, "video_id": v["id"], "id": it.id, "streamer": it.streamer, "score": it.score,
                         "published": v.get("snippet", {}).get("publishedAt"), "title": it.title,
                         **{k: int(st.get(k + "Count", 0)) for k in ("view", "like", "comment")}})
    with (root / "stats.jsonl").open("a") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return sorted(rows, key=lambda r: -r["view"])


def post_all(root: Path, cfg: dict, dry_run: bool = False) -> list[dict]:
    """Post everything in approved/ (nothing else is ever posted)."""
    return post_composio(root, cfg, dry_run)
