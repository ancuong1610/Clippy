"""Posting queue -> YouTube Shorts + Instagram Reels via Composio; TikTok gets a draft in the app's inbox
(TikTok only allows direct posting for audited apps), and the caption is pushed to the owner's phone (ntfy).

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
    campaign: str | None = None  # Content Rewards campaign this clip is made for (see campaigns.py)
    campaign_hashtags: list[str] = field(default_factory=list)  # required in every caption
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def queue_dir(root: Path, state: str) -> Path:
    d = root / state
    d.mkdir(parents=True, exist_ok=True)
    return d


def caption_for(item: PostItem, cfg: dict) -> str:
    credit = cfg.get("credit_template", "🎥 {streamer}").format(streamer=item.streamer)
    tags = " ".join("#" + t.lstrip("#") for t in item.tags)
    return "\n".join(x for x in (item.description, credit, tags) if x)


def _with_tags(text: str, hashtags: list[str]) -> str:
    from .campaigns import with_hashtags
    return with_hashtags(text, {"hashtags": hashtags})


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
        score: int | None = None, source_clip: str | None = None, titles: dict | None = None,
        campaign: dict | None = None) -> PostItem:
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
        video_hash=vh, campaign=(campaign or {}).get("name"),
        campaign_hashtags=list((campaign or {}).get("hashtags", [])))
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
AUTO_PLATFORMS = {"youtube"}  # default; [post] auto_platforms = ["youtube", "instagram", "tiktok"] adds the others.
# Leaving "youtube" out rests the channel: items still post elsewhere and get "youtube_pending" in result.json.


def auto_platforms(cfg: dict) -> set[str]:
    return set(cfg.get("auto_platforms", AUTO_PLATFORMS))


def _run(c, slug: str, args: dict, cfg: dict) -> dict:
    r = c.tools.execute(slug, args, user_id=cfg.get("composio_user", COMPOSIO_USER), dangerously_skip_version_check=True)
    return r if isinstance(r, dict) else r.model_dump()


def _data(r: dict) -> dict:
    d = r.get("data") or {}
    return d.get("data") if isinstance(d.get("data"), dict) else d


def post_instagram(c, d: Path, item: PostItem, cfg: dict) -> dict:
    """Reel with caption, public at once: container (Composio hosts the file for Meta to fetch) -> publish."""
    from composio.core.models._files import FileUploadable
    fu = FileUploadable.from_path(c.client, str(d / "short.mp4"), tool="INSTAGRAM_POST_IG_USER_MEDIA",
                                  toolkit="instagram", file_upload_allowlist=[d])
    me = _data(_run(c, "INSTAGRAM_GET_USER_INFO", {}, cfg)).get("id")
    r = _run(c, "INSTAGRAM_POST_IG_USER_MEDIA", {"ig_user_id": me, "video_file": fu.model_dump(), "media_type": "REELS",
                                                 "share_to_feed": True, "caption": social_caption(item, "instagram", cfg)},
             cfg)
    cid = _data(r).get("id")
    if not cid:
        return {"ok": False, "error": r.get("error") or r.get("data")}
    r = _run(c, "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", {"ig_user_id": me, "creation_id": cid, "max_wait_seconds": 240},
             cfg)
    mid = _data(r).get("id")
    return {"ok": bool(r.get("successful") and mid), "media_id": mid, "error": r.get("error")}


def post_tiktok_draft(c, d: Path, item: PostItem, cfg: dict, wait_s: int = 120) -> dict:
    """Video -> the TikTok app's inbox (draft); the owner adds the caption (sent to the phone) and taps Post."""
    from composio.core.models._files import FileUploadable
    fu = FileUploadable.from_path(c.client, str(d / "short.mp4"), tool="TIKTOK_UPLOAD_VIDEO", toolkit="tiktok",
                                  file_upload_allowlist=[d])
    r = _run(c, "TIKTOK_UPLOAD_VIDEO", {"file_to_upload": fu.model_dump(), "publish": False}, cfg)
    pid = _data(r).get("publish_id")
    if not pid:
        return {"ok": False, "error": r.get("error") or r.get("data")}
    status = None
    for _ in range(max(1, wait_s // 10)):
        time.sleep(10)
        status = _data(_run(c, "TIKTOK_FETCH_PUBLISH_STATUS", {"publish_id": pid}, cfg)).get("status")
        if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"):
            break
    ok = status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE")
    if ok:
        notify(cfg, f"TikTok draft: {item.title}", social_caption(item, "tiktok", cfg), click="https://www.tiktok.com/")
    return {"ok": ok, "draft": True, "publish_id": pid, "status": status}


def notify(cfg: dict, title: str, body: str, click: str | None = None) -> bool:
    """Push to the owner's phone via ntfy.sh (NTFY_TOPIC in .env; the ntfy app subscribes to that topic).
    Only captions go there (public anyway once posted) — never anything private."""
    topic = cfg.get("ntfy_topic") or os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    import httpx
    try:
        # JSON publish: UTF-8 title/body (an HTTP "Title" header can't carry "—" or emoji)
        msg = {"topic": topic, "title": title, "message": body, "tags": ["clapper"]}
        if click:
            msg["click"] = click  # tapping the notification opens this (e.g. the TikTok app)
        httpx.post("https://ntfy.sh/", json=msg, timeout=15).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def youtube_args(item: PostItem, cfg: dict) -> dict:
    desc = item.youtube_description or caption_for(item, cfg)
    s = item.streamer
    if item.youtube_description and "#" not in desc:  # 3 hashtags: game, streamer, shorts
        desc += f"\n#GTA6 #{s.replace(' ', '')} #Shorts"
    desc = _with_tags(desc, item.campaign_hashtags)
    tags = list(dict.fromkeys([*item.tags, *(h.lstrip("#") for h in item.campaign_hashtags), f"{s} GTA 6", f"{s} clips", f"{s} highlights", "GTA 6", "GTA VI",
                               "GTA 6 gameplay", "GTA 6 funny moments"]))
    return {"title": item.title, "description": desc[:5000], "tags": tags,
            "categoryId": cfg.get("youtube_category", "20"),
            "privacyStatus": cfg.get("youtube_privacy", "public")}


STREAMER_TAGS = {"xqc": "xqc", "kaicenat": "kaicenat", "ishowspeed": "ishowspeed", "caseoh_": "caseoh",
                 "zackrawrr": "asmongold", "stableronaldo": "stableronaldo", "jynxzi": "jynxzi",
                 "ohnepixel": "ohnepixel", "lacy": "lacy", "clix": "clix"}


DISPLAY = {"xqc": "xQc", "kaicenat": "Kai Cenat", "ishowspeed": "IShowSpeed", "caseoh_": "CaseOh",
           "zackrawrr": "Asmongold", "stableronaldo": "StableRonaldo", "jynxzi": "Jynxzi", "ohnepixel": "ohnePixel",
           "lacy": "Lacy", "clix": "Clix", "summit1g": "summit1g"}


def social_caption(item: PostItem, platform: str, cfg: dict) -> str:
    """Copy-paste caption for TikTok / Instagram: hook line, credit, follow line, 5 hashtags.
    Hashtag mix (in this order): game, streamer, streamer clips, one broad reach tag, one mood tag;
    campaign hashtags (e.g. #KAICENAT) are added on top. 5 is the most Instagram allows."""
    sc = cfg.get("social", {})
    hook = (item.tiktok_caption if platform == "tiktok" else item.instagram_caption) or item.title
    hook = " ".join(w for w in hook.split() if not w.startswith("#")).strip() or item.title
    st = STREAMER_TAGS.get(item.streamer.lower(), item.streamer.lower().replace(" ", ""))
    tags = ["#gta6", f"#{st}", f"#{st}clips", sc.get(f"reach_tag_{platform}", "#fyp" if platform == "tiktok" else "#gaming"),
            sc.get("mood_tag", "#funny")]
    for ct in item.campaign_hashtags:  # a campaign's exact spelling wins (#KAICENAT replaces #kaicenat)
        tags = [ct if t.lower() == ct.lower() else t for t in tags]
        if ct not in tags:
            tags.insert(len(tags) - 1, ct)
    tags = list(dict.fromkeys(tags))[:5] if platform == "instagram" else list(dict.fromkeys(tags))
    credit = f"🎥 {DISPLAY.get(item.streamer.lower(), item.streamer)} live on stream"
    follow = sc.get("follow_line", "Follow for daily GTA 6 streamer clips")
    handle = sc.get(f"handle_{platform}")
    lines = [hook, credit, f"{follow} {handle}".strip() if handle else follow, " ".join(tags)]
    return "\n".join(lines)


def manual_pack(d: Path, item: PostItem, cfg: dict, platforms: list[str]) -> list[str]:
    """For platforms without auto-posting: write ready-to-paste captions next to the video."""
    written = []
    for p in platforms:
        cap = social_caption(item, p, cfg) if p in ("tiktok", "instagram") else f"{item.title}\n{caption_for(item, cfg)}"
        f = d / f"manual_{p}.txt"
        f.write_text(cap + "\n")
        written.append(f.name)
    return written


def post_youtube(c, d: Path, item: PostItem, cfg: dict) -> dict:
    from composio.core.models._files import FileUploadable
    try:
        fu = FileUploadable.from_path(c.client, str(d / "short.mp4"), tool="YOUTUBE_MULTIPART_UPLOAD_VIDEO",
                                      toolkit="youtube", file_upload_allowlist=[d])
        r = _run(c, "YOUTUBE_MULTIPART_UPLOAD_VIDEO", {**youtube_args(item, cfg), "videoFile": fu.model_dump()}, cfg)
        vid = ((r.get("data") or {}).get("video") or {}).get("id")
        return {"ok": bool(r.get("successful") and vid), "video_id": vid,
                "url": f"https://youtube.com/shorts/{vid}" if vid else None, "error": r.get("error")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def youtube_backlog(root: Path, cfg: dict, limit: int = 1, only: str | None = None) -> list[dict]:
    """Upload to YouTube the posted clips that skipped it while the channel was resting (best score first)."""
    from composio import Composio
    c = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    todo = []
    for d, item in items(root, "posted"):
        f = d / "result.json"
        res = json.loads(f.read_text()) if f.exists() else {}
        if res.get("youtube_pending") and (d / "short.mp4").exists() and (not only or item.id == only):
            todo.append((-(item.score or 0), d, item, res))
    out = []
    for _, d, item, res in sorted(todo, key=lambda x: x[0])[:limit]:
        yt = post_youtube(c, d, item, cfg)
        res["platforms"]["youtube"] = yt
        if yt.get("ok"):
            res.pop("youtube_pending", None)
        (d / "result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
        out.append({"id": item.id, "title": item.title, **yt})
    return out


def post_composio(root: Path, cfg: dict, dry_run: bool = False, only: str | None = None) -> list[dict]:
    """YouTube (+ Instagram, TikTok draft when listed in auto_platforms) via Composio. Others -> manual pack.
    An item moves to posted/ once any platform worked (never re-posted); failed platforms get a manual pack."""
    c = None
    if not dry_run:
        if not os.getenv("COMPOSIO_API_KEY"):
            raise SystemExit("COMPOSIO_API_KEY missing — put it in .env")
        from composio import Composio
        c = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    results = []
    for d, item in items(root, "approved"):
        if only and item.id != only:
            continue
        manual = [p for p in item.platforms if p not in auto_platforms(cfg) and p != "youtube"]
        res: dict = {"id": item.id, "platforms": {}}
        if "youtube" in item.platforms and "youtube" not in auto_platforms(cfg):
            res["youtube_pending"] = True  # YouTube resting: `clipfarm post --youtube-backlog` uploads it later
        elif "youtube" in item.platforms:
            res["platforms"]["youtube"] = ({"dry_run": True, "args": youtube_args(item, cfg)} if dry_run
                                           else post_youtube(c, d, item, cfg))
        for p, fn in (("instagram", post_instagram), ("tiktok", post_tiktok_draft)):
            if p not in item.platforms or p not in auto_platforms(cfg):
                continue
            if dry_run:
                res["platforms"][p] = {"dry_run": True, "caption": social_caption(item, p, cfg)}
                continue
            try:
                res["platforms"][p] = fn(c, d, item, cfg)
            except Exception as e:  # noqa: BLE001 — one platform failing must not stop the others
                res["platforms"][p] = {"ok": False, "error": str(e)}
            if not res["platforms"][p].get("ok"):
                manual.append(p)
        if manual:
            res["manual"] = manual if dry_run else manual_pack(d, item, cfg, manual)
        if not dry_run:
            ok = any(v.get("ok") for v in res["platforms"].values())
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
