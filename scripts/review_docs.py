"""Build review-page records (one JSON file per queued clip) for the Clippy Review artifact.

  uv run python scripts/review_docs.py <uploads.json> <out_dir>
uploads.json maps queue item id -> asset url ("/_blob/<id>"). Writes <out_dir>/<id>.json.
"""

import json
import sys
import tomllib
from pathlib import Path

from clipfarm import post as P

uploads = json.loads(Path(sys.argv[1]).read_text())
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
pcfg = tomllib.loads(Path("channels.toml").read_text())["post"]
reasons = {}
for line in Path("queue/autopost.jsonl").read_text().splitlines():
    r = json.loads(line)
    if r.get("id") and r.get("reason"):
        reasons[r["id"]] = r["reason"]
for state in ("approved", "pending"):
    for d, it in P.items(Path("queue"), state):
        if it.id not in uploads:
            continue
        meta = Path(it.source_clip or "").with_suffix(".json")
        judge = (json.loads(meta.read_text()).get("judge") or {}) if meta.exists() else {}
        doc = {"title": it.title, "streamer": it.streamer, "hype": it.score or 0, "campaign": it.campaign,
               "queue": state, "reason": reasons.get(it.id) if state == "pending" else None,
               "payoff": judge.get("payoff"), "video": uploads[it.id],
               "tiktok": P.social_caption(it, "tiktok", pcfg), "instagram": P.social_caption(it, "instagram", pcfg)}
        (out / f"{it.id}.json").write_text(json.dumps(doc, ensure_ascii=False))
print(len(list(out.glob("*.json"))), "docs")
