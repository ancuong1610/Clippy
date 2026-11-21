"""Re-render queued Shorts with the current house style (cold-open hook, face-safe banner, campaign logo/hashtags).

Reuses each clip's earlier AI titles (no API calls) unless --rejudge, which re-scores the source clip with the
current judge (writes its meta "judge", old verdict kept as "judge_prev") and asks for fresh titles.
The previous video is kept as short_prev.mp4; a ".rendered-<tag>" marker makes re-runs skip finished items.
  uv run python scripts/rerender_queue.py [--states approved,pending] [--only <id>] [--limit N] [--rejudge] [--tag v3]
"""

from __future__ import annotations

import argparse
import json
import logging
import tomllib
from dataclasses import asdict
from pathlib import Path

from clipfarm import post as P
from clipfarm.campaigns import route
from clipfarm.judge import final_score, judge
from clipfarm.make import make


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="approved,pending")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rejudge", action="store_true")
    ap.add_argument("--tag", default="v3")
    a = ap.parse_args()
    if a.rejudge:
        from dotenv import load_dotenv
        load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = tomllib.loads(Path("channels.toml").read_text())
    root = Path(cfg["post"].get("queue_dir", "queue"))
    done = 0
    for state in a.states.split(","):
        for d, it in P.items(root, state):
            if (a.only and it.id != a.only) or (d / f".rendered-{a.tag}").exists():
                continue
            clip = Path(it.source_clip or "")
            old = clip.parent / "shorts" / f"{clip.stem}_short.json"
            if not clip.exists() or not old.exists():
                print(f"skip {it.id}: source or titles missing")
                continue
            titles = None if a.rejudge else json.loads(old.read_text())["titles"]
            if a.rejudge:
                mp = clip.with_suffix(".json")
                meta = json.loads(mp.read_text())
                v = final_score(judge(clip, meta), meta.get("chat_timed") or [])
                meta["judge_prev"], meta["judge"] = meta.get("judge"), v
                mp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
                it.score = v["hype"]
            camp = route(it.streamer, cfg)
            r = make(clip, it.streamer, cfg["post"].get("context", "GTA 6 livestream"),
                     out_dir=clip.parent / "shorts_v2", campaign=camp, titles=titles)
            if a.rejudge:
                t = r["titles"]
                it.title = (t.get("youtube_titles") or [it.title])[0][:100]
                it.youtube_description = t.get("youtube_description") or it.youtube_description
                it.tiktok_caption = t.get("tiktok_caption") or it.tiktok_caption
                it.instagram_caption = t.get("instagram_caption") or it.instagram_caption
            (d / "short.mp4").replace(d / "short_prev.mp4")
            Path(r["short"]).replace(d / "short.mp4")
            it.video_hash = P.file_hash(d / "short.mp4")
            it.campaign = (camp or {}).get("name")
            it.campaign_hashtags = list((camp or {}).get("hashtags", []))
            (d / "post.json").write_text(json.dumps(asdict(it), indent=2, ensure_ascii=False))
            (d / f".rendered-{a.tag}").touch()
            done += 1
            print(f"[{done}] {state} {it.id} {it.streamer} -> {it.campaign} hype={it.score} hook@{r['hook_at']} | {it.title}", flush=True)
            if a.limit and done >= a.limit:
                return


if __name__ == "__main__":
    main()
