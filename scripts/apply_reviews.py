"""Apply the owner's review-page decisions to the queue: "no" -> queue/skipped (never posted; `clipfarm cleanup`
drops its video later), "yes" -> queue/approved (the poster may post it once posting is on).

  uv run python scripts/apply_reviews.py <reviews_dir> [--dry-run]
reviews_dir holds one <queue item id>.json per review ({"decision": "yes"|"no", ...}) exported from the review page.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clipfarm import post as P


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("reviews")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = Path("queue")
    where = {d.name: s for s in ("pending", "approved") for d, _ in P.items(root, s)}
    moved = {"skipped": 0, "approved": 0}
    for f in sorted(Path(a.reviews).glob("*.json")):
        r = json.loads(f.read_text())
        r = r.get("data", r)
        dst = {"no": "skipped", "yes": "approved"}.get(r.get("decision"))
        src = where.get(f.stem)
        if not dst or not src or src == dst:
            continue
        print(f"{src:8} -> {dst:8} {f.stem}")
        if not a.dry_run:
            P.move(root, f.stem, src, dst)
        moved[dst] += 1
    print(moved, "(dry run)" if a.dry_run else "")


if __name__ == "__main__":
    main()
