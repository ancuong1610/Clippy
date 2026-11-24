"""Post analytics -> a learning dataset: how did each posted clip do, and what about the clip explains it?

collect(): for every posted clip, pull YouTube (views, average % watched, hook retention, likes, shares, subs gained),
  Instagram (views, reach, average watch time, shares, saves) and TikTok (views, likes, shares) via Composio, joined
  with the clip's own features (judge score + sub-scores + kind, streamer, title, banner, post hour). Each run appends
  one snapshot row per clip to queue/insights.jsonl; channel-level audience (age/gender, country, traffic source)
  goes to queue/audience.jsonl.
report(): reads the latest snapshot and writes reports/insights-<date>.md (does the hype score predict views? which
  kinds / streamers / post hours / hooks hold viewers?). lessons() asks the AI for 3 lessons + 1 question to test
  next; they are appended to learnings.md as PROPOSED, the owner promotes the ones that hold up.
Instagram/TikTok posts made by hand (before auto-posting) are matched to their queue item by caption text.
"""

from __future__ import annotations

import json
import os
import re
import statistics as st
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

from . import post as P

BERLIN = ZoneInfo("Europe/Berlin")
HOOK_S = 3.0  # "did the hook work": share of viewers still watching after this many seconds
MIN_VIEWS_FOR_CURVE = 30  # retention curves of tiny videos are noise


# ---------------------------------------------------------------- features of our own clips
def features(d: Path, item: P.PostItem) -> dict:
    meta_f = Path(item.source_clip or "").with_suffix(".json")
    meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
    j = meta.get("judge") or {}
    short_json = next(iter(sorted(Path(item.source_clip or ".").parent.glob(
        f"shorts*/{Path(item.source_clip or 'x').stem}_*short.json"))), None) if item.source_clip else None
    sj = json.loads(short_json.read_text()) if short_json and short_json.exists() else {}
    t = sj.get("titles") or meta.get("titles") or {}
    return {"id": item.id, "streamer": (item.streamer or "").lower(), "title": item.title, "hype": item.score,
            "kind": j.get("kind"), "cutscene": j.get("cutscene"), "streamer_caused": j.get("streamer_caused"),
            **{k: j.get(k) for k in ("payoff_pts", "stakes", "legible", "surprise", "visual", "unison")},
            "banner": t.get("thumbnail_text"), "hook_at": sj.get("hook_at"), "campaign": item.campaign,
            "judge_version": "v2" if "payoff_pts" in j else "v1", "variant": item.variant or "base"}


def _norm(s: str) -> str:
    first = re.sub(r"#\S+", "", (s or "").split("\n")[0])  # first line, no hashtags
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", first.lower())).strip()[:80]


def _match(caption: str, candidates: dict[str, list[str]]) -> str | None:
    """Queue item id whose known caption or title best matches a platform caption (manual posts carry no id)."""
    c = _norm(caption)
    best, score = None, 0.0
    for iid, texts in candidates.items():
        for text in texts:
            s = SequenceMatcher(None, c, _norm(text)).ratio()
            if s > score:
                best, score = iid, s
    return best if score >= 0.6 else None


# ---------------------------------------------------------------- platform pulls
def _yt(c, cfg: dict, ids: list[str], start: str, end: str) -> dict[str, dict]:
    run = lambda a: P._data(P._run(c, "YOUTUBE_QUERY_ANALYTICS", {"ids": "channel==MINE", "startDate": start,  # noqa: E731
                                                                   "endDate": end, **a}, cfg))
    out: dict[str, dict] = {}
    m = "views,averageViewDuration,averageViewPercentage,likes,shares,comments,subscribersGained"
    for i in range(0, len(ids), 50):
        r = run({"metrics": m, "dimensions": "video", "filters": "video==" + ",".join(ids[i:i + 50]),
                 "maxResults": 200})
        for v, views, dur, pct, likes, shares, comments, subs in r.get("rows") or []:
            out[v] = {"yt_views": views, "yt_avg_view_s": dur, "yt_avg_view_pct": round(pct, 1), "yt_likes": likes,
                      "yt_shares": shares, "yt_comments": comments, "yt_subs": subs,
                      "length_s": round(dur * 100 / pct, 1) if pct else None}
    for v, row in out.items():
        if row["yt_views"] < MIN_VIEWS_FOR_CURVE or not row["length_s"]:
            continue
        r = run({"metrics": "audienceWatchRatio,relativeRetentionPerformance", "dimensions": "elapsedVideoTimeRatio",
                 "filters": f"video=={v}"})
        curve = r.get("rows") or []
        if curve:
            at = min(1.0, HOOK_S / row["length_s"])
            hook = min(curve, key=lambda x: abs(x[0] - at))
            row.update(yt_hook_ret=round(hook[1], 3), yt_end_ret=round(curve[-1][1], 3),
                       yt_rel_ret=round(st.mean(x[2] for x in curve), 3))
    return out


def _ig(c, cfg: dict) -> list[dict]:
    me = P._data(P._run(c, "INSTAGRAM_GET_USER_INFO", {}, cfg)).get("id")
    media = P._data(P._run(c, "INSTAGRAM_GET_IG_USER_MEDIA", {"ig_user_id": me, "limit": 100,
                                                              "fields": "id,caption,timestamp,media_product_type,permalink"}, cfg))
    base = ["views", "reach", "likes", "comments", "shares", "saved", "ig_reels_avg_watch_time"]
    extended = [*base, "total_interactions", "ig_reels_video_view_total_time"]
    out = []
    for m in media.get("data") or []:
        vals = {}
        for metric in (extended, base):
            try:
                r = P._run(c, "INSTAGRAM_GET_IG_MEDIA_INSIGHTS", {"ig_media_id": m["id"], "metric": metric}, cfg)
            except Exception:  # A failed insight request for one reel must not interrupt the pull.
                continue
            data = P._data(r)
            for entry in data.get("data") or []:
                values = entry.get("values") or []
                total = entry.get("total_value") or {}
                value = total.get("value") if "value" in total else values[0].get("value") if values else None
                if value is not None:
                    vals[entry["name"]] = value
            if r.get("successful") is not False and not (r.get("error") or data.get("error")):
                break
        out.append({"ig_media_id": m["id"], "ig_url": m.get("permalink"), "caption": m.get("caption") or "",
                    "posted": m.get("timestamp"),
                    "ig_views": vals.get("views"), "ig_reach": vals.get("reach"), "ig_likes": vals.get("likes"),
                    "ig_comments": vals.get("comments"), "ig_shares": vals.get("shares"),
                    "ig_saves": vals.get("saved"), "ig_interactions": vals.get("total_interactions"),
                    "ig_watch_total_s": vals["ig_reels_video_view_total_time"] / 1000
                    if vals.get("ig_reels_video_view_total_time") is not None else None,
                    "ig_avg_watch_s": round(vals["ig_reels_avg_watch_time"] / 1000, 1)
                    if vals.get("ig_reels_avg_watch_time") is not None else None})
    return out


def _tt(c, cfg: dict) -> list[dict]:
    out, cursor = [], None
    for _ in range(10):
        r = P._data(P._run(c, "TIKTOK_LIST_VIDEOS", {"max_count": 20, **({"cursor": cursor} if cursor else {})}, cfg))
        for v in r.get("videos") or []:
            out.append({"tt_id": v.get("id"), "tt_url": v.get("share_url") or v.get("embed_link"), "caption": v.get("video_description") or v.get("title") or "",
                        "posted": v.get("create_time"), "tt_views": v.get("view_count"),
                        "tt_likes": v.get("like_count"), "tt_comments": v.get("comment_count"),
                        "tt_shares": v.get("share_count")})
        cursor = r.get("cursor")
        if not r.get("has_more"):
            break
    return out


def _audience(c, cfg: dict, start: str, end: str) -> dict:
    def yt(dims: str, metric: str = "views") -> list:
        r = P._data(P._run(c, "YOUTUBE_QUERY_ANALYTICS", {"ids": "channel==MINE", "startDate": start, "endDate": end,
                                                          "metrics": metric, "dimensions": dims,
                                                          "sort": f"-{metric}", "maxResults": 15}, cfg))
        return r.get("rows") or []
    return {"yt_age_gender": yt("ageGroup,gender", "viewerPercentage"), "yt_country": yt("country"),
            "yt_traffic": yt("insightTrafficSourceType")}


# ---------------------------------------------------------------- collect
def collect(cfg: dict) -> list[dict]:
    if not os.getenv("COMPOSIO_API_KEY"):
        raise SystemExit("COMPOSIO_API_KEY missing — put it in .env")
    from composio import Composio
    pcfg = cfg.get("post", {})
    root = Path(pcfg.get("queue_dir", "queue"))
    c = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    now = time.time()
    end = datetime.now(timezone.utc).date()
    start = (end - timedelta(days=90)).isoformat()
    rows, yt_ids, ig_known, captions = {}, {}, {}, {"instagram": {}, "tiktok": {}}
    for d, it in P.items(root, "posted"):
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else {}
        pl = res.get("platforms", {})
        row = features(d, it)
        posted = datetime.fromtimestamp((d / "result.json").stat().st_mtime if (d / "result.json").exists()
                                        else (d / "post.json").stat().st_mtime, timezone.utc)
        row.update(ts=now, posted=posted.isoformat(), post_hour_berlin=posted.astimezone(BERLIN).hour)
        if (pl.get("youtube") or {}).get("video_id"):
            row["yt_id"] = pl["youtube"]["video_id"]
            yt_ids[row["yt_id"]] = it.id
        if (pl.get("instagram") or {}).get("media_id"):
            ig_known[pl["instagram"]["media_id"]] = it.id
        for p in ("instagram", "tiktok"):
            man = d / f"manual_{p}.txt"
            captions[p][it.id] = [man.read_text() if man.exists() else P.social_caption(it, p, pcfg), it.title]
        rows[it.id] = row
    for v, m in _yt(c, pcfg, list(yt_ids), start, end.isoformat()).items():
        rows[yt_ids[v]].update(m)
    for m in _ig(c, pcfg):
        iid = ig_known.get(m["ig_media_id"]) or _match(m.pop("caption"), captions["instagram"])
        m.pop("caption", None)
        if iid in rows:
            rows[iid].update({k: v for k, v in m.items() if k != "posted"})
    for m in _tt(c, pcfg):
        iid = _match(m.pop("caption"), captions["tiktok"])
        if iid in rows:
            rows[iid].update({k: v for k, v in m.items() if k != "posted"})
    out = list(rows.values())
    with (root / "insights.jsonl").open("a") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (root / "audience.jsonl").open("a") as f:
        f.write(json.dumps({"ts": now, "start": start, "end": end.isoformat(),
                            **_audience(c, pcfg, start, end.isoformat())}, ensure_ascii=False) + "\n")
    return out


def latest(root: Path) -> list[dict]:
    """Newest snapshot row per clip."""
    f = root / "insights.jsonl"
    rows: dict[str, dict] = {}
    for line in f.read_text().splitlines() if f.exists() else []:
        r = json.loads(line)
        if r["id"] not in rows or r["ts"] >= rows[r["id"]]["ts"]:
            rows[r["id"]] = r
    return list(rows.values())


# ---------------------------------------------------------------- report
def views(r: dict) -> int:
    return sum(r.get(k) or 0 for k in ("yt_views", "ig_views", "tt_views"))


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None

    def ranks(values: list[float]) -> list[float]:
        by_value, start = {}, 0
        for value, count in sorted(Counter(values).items()):
            by_value[value] = start + (count - 1) / 2
            start += count
        return [by_value[value] for value in values]

    try:
        return round(st.correlation(ranks(xs), ranks(ys)), 2)
    except st.StatisticsError:
        return None


def group(rows: list[dict], key: str, min_n: int = 2) -> list[tuple]:
    g: dict = {}
    for r in rows:
        g.setdefault(r.get(key), []).append(r)
    out = []
    for k, rs in g.items():
        if len(rs) < min_n:
            continue
        pct = [r["yt_avg_view_pct"] for r in rs if r.get("yt_avg_view_pct") is not None]
        hook = [r["yt_hook_ret"] for r in rs if r.get("yt_hook_ret") is not None]
        out.append((k, len(rs), int(st.median(views(r) for r in rs)), round(st.median(pct), 1) if pct else None,
                    round(st.mean(hook), 2) if hook else None))
    return sorted(out, key=lambda x: -x[2])


def report(rows: list[dict], audience: dict | None = None) -> str:
    if not rows:
        return "# Insights\n\nNo posted clips with data yet.\n"
    tot = {p: sum(r.get(f"{p}_views") or 0 for r in rows) for p in ("yt", "ig", "tt")}
    scored = [r for r in rows if r.get("hype") is not None]
    pct = [r for r in scored if r.get("yt_avg_view_pct") is not None]
    md = [f"# Insights {datetime.now(BERLIN):%Y-%m-%d %H:%M}",
          f"{len(rows)} posted clips · views YouTube {tot['yt']} · Instagram {tot['ig']} · TikTok {tot['tt']}", "",
          "## Does the hype score predict reach?",
          f"- Spearman(hype, total views) = {spearman([r['hype'] for r in scored], [views(r) for r in scored])}"
          " (1 = perfect, 0 = no link; needs 5+ clips)",
          f"- Spearman(hype, YouTube % watched) = "
          f"{spearman([r['hype'] for r in pct], [r['yt_avg_view_pct'] for r in pct])}",
          ""]
    hooked = [r for r in rows if r.get("yt_hook_ret") is not None]
    if hooked:
        md += ["## Hook", f"- Viewers still watching at {HOOK_S:.0f}s (YouTube, {len(hooked)} clips with "
               f"{MIN_VIEWS_FOR_CURVE}+ views): mean {st.mean(r['yt_hook_ret'] for r in hooked):.2f} "
               "(> 1 means people rewatch the start)", ""]
    for key, name in (("kind", "Clip kind"), ("streamer", "Streamer"), ("post_hour_berlin", "Post hour (Berlin)"),
                      ("judge_version", "Judge version"), ("variant", "Lab variant")):
        g = group(rows, key)
        if g:
            md += [f"## {name}", "| value | clips | median views | YT % watched (median; >100 = loops) | hook ret. |", "|---|---|---|---|---|"]
            md += [f"| {k} | {n} | {v} | {p if p is not None else '–'} | {h if h is not None else '–'} |"
                   for k, n, v, p, h in g] + [""]
    ranked = sorted(rows, key=views, reverse=True)
    for name, part in (("Top 5", ranked[:5]), ("Bottom 5", ranked[-5:][::-1])):
        md += [f"## {name}"] + [f"- {views(r)} views · hype {r.get('hype')} · {r.get('kind') or '?'} · "
                                f"{r.get('yt_avg_view_pct') or '–'}% watched · {r['title']}" for r in part] + [""]
    if audience:
        md += ["## Audience (YouTube, last 90 days)",
               "- Age/gender: " + ", ".join(f"{a} {g} {p}%" for a, g, p in audience.get("yt_age_gender", [])[:5]),
               "- Countries: " + ", ".join(f"{c} {v}" for c, v in audience.get("yt_country", [])[:6]),
               "- Traffic: " + ", ".join(f"{s} {v}" for s, v in audience.get("yt_traffic", [])[:6]), ""]
    return "\n".join(md)


LESSONS = """You coach a GTA 6 streamer-clip channel (YouTube Shorts, Instagram Reels, TikTok). Below is this week's
performance report of our posted clips. Write what the DATA supports, not generic advice. Small samples: say so.
Return JSON: {{"lessons": ["...", "...", "..."], "next_question": "one concrete thing to test in the next posts"}}

{report}"""


def lessons(md: str) -> dict:
    from .titles import _call
    return _call(LESSONS.format(report=md))


def write_report(cfg: dict, ask_ai: bool = True) -> Path:
    root = Path(cfg.get("post", {}).get("queue_dir", "queue"))
    aud_f = root / "audience.jsonl"
    aud = json.loads(aud_f.read_text().splitlines()[-1]) if aud_f.exists() and aud_f.read_text().strip() else None
    md = report(latest(root), aud)
    out = Path("reports") / f"insights-{datetime.now(BERLIN):%Y-%m-%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(md)
    day = f"## {datetime.now(BERLIN):%Y-%m-%d} — PROPOSED"
    if ask_ai and Path("learnings.md").exists() and day in Path("learnings.md").read_text():
        ask_ai = False  # one set of proposed lessons per day
    if ask_ai:
        try:
            ls = lessons(md)
            with Path("learnings.md").open("a") as f:
                f.write(f"\n{day} (owner: keep / drop)\n"
                        + "".join(f"- {x}\n" for x in ls.get("lessons", []))
                        + f"- Next test: {ls.get('next_question')}\n")
        except Exception as e:  # noqa: BLE001 — the numbers are the product; lessons are a bonus
            print(f"lessons skipped: {e}")
    return out
