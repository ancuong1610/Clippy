"""Store today's TikTok trend tags (the 2 the caption adds after the 3 channel tags).

  uv run python scripts/set_tiktok_tags.py "#lucia" "#huss" --source "Creative Center US 7d: #lucia 570M views, ..."
Written to queue/trending_tags.json; the poster reads it for every TikTok caption (stale after 3 days -> channels.toml).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--source", default="")
    a = ap.parse_args()
    tags = ["#" + re.sub(r"[^\w]", "", t.lstrip("#")).lower() for t in a.tags][:2]
    tags = [t for t in tags if len(t) > 1]
    out = Path("queue/trending_tags.json")
    out.write_text(json.dumps({"tags": tags, "source": a.source, "at": datetime.now().isoformat(timespec="minutes")},
                              indent=2, ensure_ascii=False))
    print(f"{out}: {' '.join(tags)}")


if __name__ == "__main__":
    main()
