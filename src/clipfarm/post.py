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
    youtube_description: str | None = None  # from `clipfarm title`
    tiktok_caption: str | None = None
    instagram_caption: str | None = None
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
        score: int | None = None, source_clip: str | None = None, titles: dict | None = None) -> PostItem:
    if len(title) > 100:
        raise SystemExit("title > 100 chars (YouTube limit)")
    titles = titles or {}
    item = PostItem(
        id=datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        title=title, streamer=streamer, platforms=list(cfg.get("platforms", ["tiktok", "youtube", "instagram"])),
        description=cfg.get("description", ""), tags=list(cfg.get("tags", [])) + [streamer],
        score=score, source_clip=source_clip,
        youtube_description=titles.get("youtube_description"),
        tiktok_caption=titles.get("tiktok_caption"), instagram_caption=titles.get("instagram_caption"))
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


COMPOSIO_USER = "stream-chaos-clips"
AUTO_PLATFORMS = {"composio": {"youtube"}, "upload-post": {"youtube", "tiktok", "instagram"}}


def youtube_args(item: PostItem, cfg: dict) -> dict:
    desc = item.youtube_description or caption_for(item, cfg)
    if item.youtube_description and "#" not in desc:
        desc += "\n" + " ".join("#" + t.lstrip("#") for t in item.tags)
    return {"title": item.title, "description": desc[:5000], "tags": item.tags,
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


def post_composio(root: Path, cfg: dict, dry_run: bool = False) -> list[dict]:
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
        manual = [p for p in item.platforms if p not in AUTO_PLATFORMS["composio"]]
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


def post_all(root: Path, cfg: dict, dry_run: bool = False, gap_minutes: float = 0) -> list[dict]:
    """Post everything in approved/. gap_minutes > 0 schedules them that far apart instead of all at once."""
    if cfg.get("backend", "composio") == "composio":
        return post_composio(root, cfg, dry_run)
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
