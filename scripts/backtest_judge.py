"""Re-judge clips the owner already reviewed and compare the judge's verdict with the owner's yes/no.

  uv run python scripts/backtest_judge.py <reviews_dir> [--split split.json] [--out results.jsonl] [--bar 90]
reviews_dir holds one <queue item id>.json per review ({"decision": "yes"|"no", ...}) exported from the review
page. Prints precision/recall at the bar, overall and on the held-out "test" ids if a split file is given.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from clipfarm.judge import final_score, judge


def source_of(item_id: str) -> Path | None:
    for p in Path("queue").glob(f"*/{item_id}/post.json"):
        src = json.loads(p.read_text()).get("source_clip")
        return Path(src) if src and Path(src).exists() else None
    return None


def stats(rows: list[dict], bar: int) -> str:
    tp = sum(r["hype"] >= bar and r["owner"] == "yes" for r in rows)
    fp = sum(r["hype"] >= bar and r["owner"] == "no" for r in rows)
    yes = sum(r["owner"] == "yes" for r in rows)
    prec = tp / (tp + fp) if tp + fp else 0
    return f"n={len(rows)} passed={tp + fp} precision={prec:.0%} ({tp}/{tp + fp}) recall={tp / yes if yes else 0:.0%} ({tp}/{yes})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("reviews")
    ap.add_argument("--split")
    ap.add_argument("--out", default="backtest.jsonl")
    ap.add_argument("--bar", type=int, default=90)
    a = ap.parse_args()
    load_dotenv()
    todo = []
    for f in sorted(Path(a.reviews).glob("*.json")):
        r = json.loads(f.read_text())
        r = r.get("data", r)
        src = source_of(f.stem)
        if src and r.get("decision") in ("yes", "no"):
            todo.append((f.stem, r["decision"], src))

    def run(t):
        item, owner, src = t
        meta = json.loads(src.with_suffix(".json").read_text())
        try:
            v = final_score(judge(src, meta), meta.get("chat_timed") or [])
        except Exception as e:  # noqa: BLE001 — one failed clip must not stop the backtest
            return {"id": item, "owner": owner, "error": str(e)}
        old = (meta.get("judge") or {}).get("hype")
        return {"id": item, "owner": owner, "old": old, **{k: v.get(k) for k in (
            "hype", "visual", "bonus", "cutscene", "streamer_caused", "kind", "payoff_pts", "stakes", "legible",
            "surprise", "payoff", "why")}}

    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(run, todo))
    Path(a.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    ok = [r for r in rows if "error" not in r]
    print(f"errors: {len(rows) - len(ok)}")
    print("new  all :", stats(ok, a.bar))
    print("old  all :", stats([{**r, "hype": r["old"] or 0} for r in ok], a.bar))
    if a.split:
        test = set(json.loads(Path(a.split).read_text())["test"])
        t = [r for r in ok if r["id"] in test]
        print("new  test:", stats(t, a.bar))
        print("old  test:", stats([{**r, "hype": r["old"] or 0} for r in t], a.bar))


if __name__ == "__main__":
    main()
