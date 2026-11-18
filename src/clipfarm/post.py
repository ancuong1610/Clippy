"""Posting queue -> Upload-Post (one API call posts to TikTok, YouTube Shorts, Instagram Reels).

  queue/pending/<id>/   short.mp4 + post.json   (added by `clipfarm queue add`)
  queue/approved/<id>/  human said yes          (`clipfarm queue approve <id>`)
  queue/posted/<id>/    + result.json           (`clipfarm post`)
  queue/failed/<id>/    + result.json

Nothing is posted unless it sits in approved/. `auto_approve_min_score` (off by default) lets clips
above a moment score skip the manual step once you have locked the style.
Docs: https://docs.upload-post.com/api/upload-video
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

API = "https://api.upload-post.com/api/upload"
STATES = ("pending", "approved", "posted", "failed")


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
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def queue_dir(root: Path, state: str) -> Path:
    d = root / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def caption_for(item: PostItem, cfg: dict) -> str:
    credit = cfg.get("credit_template", "🎥 {streamer}").format(streamer=item.streamer)
    tags = " ".join("#" + t.lstrip("#") for t in item.tags)
    return "\n".join(x for x in (item.description, credit, tags) if x)


def add(root: Path, video: Path, title: str, streamer: str, cfg: dict, thumbnail: Path | None = None,
        score: int | None = None, source_clip: str | None = None) -> PostItem:
    if len(title) > 100:
        raise SystemExit("title > 100 chars (YouTube limit)")
    item = PostItem(
        id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        title=title, streamer=streamer, platforms=list(cfg.get("platforms", ["tiktok", "youtube", "instagram"])),
        description=cfg.get("description", ""), tags=list(cfg.get("tags", [])) + [streamer],
        score=score, source_clip=source_clip)
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


def build_request(d: Path, item: PostItem, cfg: dict, scheduled: datetime | None = None
                  ) -> tuple[dict, dict, dict]:
    """Returns (headers-without-key, form data, files) — pure, testable, no network."""
    caption = caption_for(item, cfg)
    data: dict = {
        "user": cfg["user"],
        "platform[]": item.platforms,
        "title": item.title,
        # YouTube
        "youtube_description": caption,
        "privacyStatus": cfg.get("youtube_privacy", "public"),
        "categoryId": cfg.get("youtube_category", "20"),  # 20 = Gaming
        "tags[]": item.tags,
        "selfDeclaredMadeForKids": "false",
        # TikTok
        "tiktok_title": f"{item.title}\n{caption}"[:2200],
        # Instagram
        "instagram_title": f"{item.title}\n{caption}"[:2200],
        "media_type": "REELS",
        "async_upload": "true",
        "request_id": item.id,
    }
    if cfg.get("tiktok_privacy"):
        data["privacy_level"] = cfg["tiktok_privacy"]
    if scheduled:
        data["scheduled_date"] = scheduled.astimezone(timezone.utc).isoformat()
    files = {"video": (d / "short.mp4")}
    if item.thumbnail and "youtube" in item.platforms:
        files["thumbnail"] = d / item.thumbnail
    headers = {"Idempotency-Key": item.id}  # a retried call can never double-post
    return headers, data, files


def post_all(root: Path, cfg: dict, dry_run: bool = False, gap_minutes: float = 0) -> list[dict]:
    """Post everything in approved/. gap_minutes > 0 schedules them that far apart instead of all at once."""
    key = os.getenv("UPLOAD_POST_API_KEY")
    if not dry_run and not key:
        raise SystemExit("UPLOAD_POST_API_KEY missing — put it in .env (never in code or chat)")
    if not cfg.get("user"):
        raise SystemExit("[post] user = <your Upload-Post profile name> missing in channels.toml")
    results = []
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    for n, (d, item) in enumerate(items(root, "approved")):
        when = start + timedelta(minutes=gap_minutes * n) if gap_minutes else None
        headers, data, files = build_request(d, item, cfg, when)
        if dry_run:
            shown = {k: v for k, v in data.items() if k not in ("tiktok_title", "instagram_title")}
            results.append({"id": item.id, "dry_run": True, "data": shown,
                            "files": {k: str(v) for k, v in files.items()}})
            continue
        handles = {k: (p.name, p.open("rb")) for k, p in files.items()}
        try:
            r = httpx.post(API, headers={**headers, "Authorization": f"Apikey {key}"},
                           data=data, files=handles, timeout=300)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"text": r.text}
            ok = r.status_code in (200, 202) and body.get("success")
        except httpx.HTTPError as e:
            body, ok = {"error": str(e)}, False
        finally:
            for _, fh in handles.values():
                fh.close()
        dest = move(root, item.id, "approved", "posted" if ok else "failed")
        (dest / "result.json").write_text(json.dumps(body, indent=2, ensure_ascii=False))
        results.append({"id": item.id, "ok": ok, "response": body})
        time.sleep(2)
    return results
