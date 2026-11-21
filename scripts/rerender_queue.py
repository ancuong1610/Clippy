"""Re-render queued Shorts with the current house style (cold-open hook, face-safe banner, campaign logo/hashtags).

Reuses each clip's earlier AI titles (no API calls). The old video is kept as short_v1.mp4 in the item folder.
  uv run python scripts/rerender_queue.py [--states approved,pending] [--only <id>] [--limit N]
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
from clipfarm.make import make


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="approved,pending")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = tomllib.loads(Path("channels.toml").read_text())
    root = Path(cfg["post"].get("queue_dir", "queue"))
    done = 0
    for state in a.states.split(","):
        for d, it in P.items(root, state):
            if (a.only and it.id != a.only) or (d / "short_v1.mp4").exists():
                continue
            clip = Path(it.source_clip or "")
            old = clip.parent / "shorts" / f"{clip.stem}_short.json"
            if not clip.exists() or not old.exists():
                print(f"skip {it.id}: source or titles missing")
                continue
            titles = json.loads(old.read_text())["titles"]
            camp = route(it.streamer, cfg)
            r = make(clip, it.streamer, cfg["post"].get("context", "GTA 6 livestream"),
                     out_dir=clip.parent / "shorts_v2", campaign=camp, titles=titles)
            (d / "short.mp4").rename(d / "short_v1.mp4")
            Path(r["short"]).replace(d / "short.mp4")
            it.video_hash = P.file_hash(d / "short.mp4")
            it.campaign = (camp or {}).get("name")
            it.campaign_hashtags = list((camp or {}).get("hashtags", []))
            (d / "post.json").write_text(json.dumps(asdict(it), indent=2, ensure_ascii=False))
            done += 1
            print(f"[{done}] {state} {it.id} {it.streamer} -> {it.campaign} hook@{r['hook_at']}", flush=True)
            if a.limit and done >= a.limit:
                return


if __name__ == "__main__":
    main()
