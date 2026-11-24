"""Prepare review docs for previously posted clips from local queue and analytics snapshots."""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from clipfarm import load_config
from clipfarm import insights, post as P
from clipfarm.review import BATCHES, PREVIEWS, VIEW_FIELDS, make_preview, review_doc, source_metadata, write_json


def main() -> None:
    cfg = load_config(Path("channels.toml")).get("post", {})
    root = Path(cfg.get("queue_dir", "queue"))
    snapshots = {row["id"]: row for row in insights.latest(root)}
    docs = BATCHES / "posted" / "docs"
    made = missing = errors = 0
    if (root / "posted").is_dir():
        for folder, item in P.items(root, "posted"):
            video = folder / "short.mp4"
            if not video.is_file() and item.source_clip:
                video = Path(item.source_clip)
            if not video.is_file():
                missing += 1
                continue
            preview = PREVIEWS / f"{item.id}.mp4"
            preview.parent.mkdir(parents=True, exist_ok=True)
            try:
                make_preview(video, preview)
            except (subprocess.CalledProcessError, OSError, ValueError) as exc:
                print(f"{item.id}: could not make preview ({exc})", file=sys.stderr)
                errors += 1
                continue
            snapshot = snapshots.get(item.id, {})
            verdict = source_metadata(item.source_clip).get("judge") or {}
            doc = review_doc(item, cfg, "posted", "posted", preview, verdict)
            doc["views"] = {platform: snapshot[key] for platform, key in VIEW_FIELDS.items()
                            if snapshot.get(key) is not None}
            if snapshot.get("posted"):
                doc["posted"] = snapshot["posted"][:10]
            else:
                stamp = folder / "result.json"
                if not stamp.is_file():
                    stamp = folder / "post.json"
                doc["posted"] = datetime.fromtimestamp(stamp.stat().st_mtime, timezone.utc).date().isoformat()
            write_json(docs / f"{item.id}.json", doc)
            made += 1
    print(f"{made} posted review docs, {missing} skipped (no video), {errors} preview errors.")


if __name__ == "__main__":
    main()
