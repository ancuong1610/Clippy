"""Compare hype scores with Instagram and TikTok performance and optional owner reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

from clipfarm import insights, load_config, performance, post as P
from clipfarm.review import VIEW_FIELDS, source_metadata

BUCKETS = (("<60", 0, 60), ("60–74", 60, 75), ("75–84", 75, 85), ("85–89", 85, 90), ("90+", 90, 101))
FEATURES = ("hype", "visual", "stakes", "surprise", "legible", "payoff_pts", "unison", "length_s", "post_hour_berlin")
RAW_METRICS = ("ig_views", "ig_reach", "ig_likes", "ig_comments", "ig_shares", "ig_saves", "ig_avg_watch_s",
               "ig_interactions", "ig_watch_total_s", "tt_views", "tt_likes", "tt_comments", "tt_shares")
RATES = ("ig_watch_ratio", "ig_share_rate", "tt_share_rate", "share_rate", "ig_save_rate")


@dataclass(frozen=True)
class Review:
    id: str
    streamer: str
    decision: str
    hype: int
    tags: list[str]
    note: str
    payoff: str
    posted: bool
    views: int | None


def split(item_id: str) -> str:
    """Use a stable hash so a clip always remains in the same half."""
    return "train" if hashlib.sha256(item_id.encode("utf-8")).digest()[0] % 2 == 0 else "test"


def load_reviews(reviews: Path | None, root: Path, snapshots: dict[str, dict]) -> tuple[list[Review], Counter, dict]:
    rows, counts, decisions = [], Counter(), {}
    paths = sorted(reviews.glob("*.json")) if reviews is not None else []
    if not paths:
        return rows, counts, decisions
    queue = {item.id: (state, item) for state in P.STATES if (root / state).is_dir()
             for _, item in P.items(root, state)}
    for path in paths:
        counts["exported"] += 1
        review = json.loads(path.read_text())
        review = review.get("data", review)
        decision = review.get("decision")
        if decision not in ("yes", "no"):
            counts["undecided"] += 1
            continue
        decisions[path.stem] = decision
        counts[decision] += 1
        if path.stem not in queue:
            counts["missing_queue"] += 1
            continue
        state, item = queue[path.stem]
        verdict = source_metadata(item.source_clip).get("judge") or {}
        hype = verdict.get("hype")
        if hype is None:
            hype = item.score
        if hype is None:
            counts["missing_score"] += 1
            continue
        snapshot = snapshots.get(item.id, {})
        posted = state == "posted"
        measured = posted and any(snapshot.get(key) is not None for key in VIEW_FIELDS.values())
        rows.append(Review(item.id, item.streamer, decision, hype, review.get("tags") or [],
                           (review.get("note") or "").strip(), verdict.get("payoff") or "",
                           posted, insights.views(snapshot) if measured else None))
    return rows, counts, decisions


def scores(rows: list[Review], bar: int) -> tuple[float, float, float]:
    positives = sum(r.decision == "yes" for r in rows)
    passed = sum(r.hype >= bar for r in rows)
    true = sum(r.hype >= bar and r.decision == "yes" for r in rows)
    return (true / passed if passed else 0.0, true / positives if positives else 0.0,
            2 * true / (passed + positives) if passed + positives else 0.0)


def cell(text: str) -> str:
    return " ".join(text.splitlines()).replace("|", "\\|")


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def performance_section(ranked: list[dict], dropped: int, decisions: dict[str, str]) -> list[str]:
    watch_count = sum(row["ig_watch_ratio"] is not None for row in ranked)
    lines = ["## Real performance (Instagram + TikTok)", "",
             f"- {len(ranked)} clips with performance scores.",
             f"- {dropped} dropped by the 48 h settling filter (young or missing a post timestamp).",
             f"- {watch_count} clips with an Instagram watch ratio.", ""]

    correlations = []
    for key in (*FEATURES, *RAW_METRICS, *RATES):
        pairs = [(row["perf_score"], row[key]) for row in ranked if row.get(key) is not None]
        if pairs:
            perf, values = zip(*pairs)
            correlations.append((key, len(pairs), insights.spearman(perf, values)))
    correlations.sort(key=lambda row: (row[2] is None, -abs(row[2] or 0), row[0]))
    lines += ["### Spearman correlations with performance", "", "| Metric | Clips | Spearman ρ |", "|---|---:|---:|"]
    lines += [f"| {key} | {n} | {f'{rho:.2f}' if rho is not None else '—'} |" for key, n, rho in correlations]
    lines += ["", "A dash indicates fewer than five pairs or constant values. Correlations with score components "
                  "describe how the score behaves.", "",
              "### Mean performance by category", "", "| Category | Value | Clips | Mean performance |",
              "|---|---|---:|---:|"]
    for key in ("kind", "cutscene", "streamer_caused", "streamer", "post_hour_berlin"):
        groups = defaultdict(list)
        for row in ranked:
            if row.get(key) not in (None, ""):
                groups[row[key]].append(row["perf_score"])
        summaries = [(value, len(scores), mean(scores)) for value, scores in groups.items() if len(scores) >= 3]
        for value, n, average in sorted(summaries, key=lambda entry: (-entry[2], str(entry[0]))):
            lines.append(f"| {key} | {cell(str(value))} | {n} | {average:.1f} |")

    lines += ["", "### Performance by hype bucket", "", "| Hype | Clips | Mean performance |", "|---|---:|---:|"]
    for label, low, high in BUCKETS:
        bucket = [row["perf_score"] for row in ranked if row.get("hype") is not None and low <= row["hype"] < high]
        lines.append(f"| {label} | {len(bucket)} | {f'{mean(bucket):.1f}' if bucket else '—'} |")

    rates, best = performance.hype_hit_rates(ranked)
    lines += ["", "### Top-quartile hit rate", "",
              "The top quartile includes everyone tied at the cutoff. Ties between bars favor greater coverage, "
              "then the lower bar."]
    if best is not None:
        _, coverage, rate = rates[best]
        lines.append(f"Suggested hype bar **{best}** keeps {coverage:.1%} of scored clips with a {rate:.1%} hit rate.")
    else:
        lines.append("No bar keeps at least 25% of scored clips.")
    lines += ["", "| Bar | Clips passing | Coverage | Top-quartile hit rate |", "|---:|---:|---:|---:|"]
    for bar, (n, coverage, rate) in rates.items():
        lines.append(f"| {bar} | {n} | {coverage:.1%} | {f'{rate:.1%}' if rate is not None else '—'} |")

    if decisions:
        lines += ["", "### Owner agreement with top-half performance", ""]
        reviewed = [row for row in ranked if row["id"] in decisions]
        if reviewed:
            cutoff = performance.top_cutoff(ranked, 0.5)
            agree = sum((row["perf_score"] >= cutoff) == (decisions[row["id"]] == "yes") for row in reviewed)
            lines.append(f"{agree} of {len(reviewed)} ({agree / len(reviewed):.1%}) matched. "
                         "The top half includes everyone tied at the cutoff.")
        else:
            lines.append("No owner decisions matched clips with performance scores.")

    for label, clips in (("Best 10 performers", ranked[:10]), ("Worst 10 performers", ranked[::-1][:10])):
        lines += ["", f"### {label}", "", "| Clip | Perf | Hype | Kind | Payoff | IG views | TT views | Links |",
                  "|---|---:|---:|---|---|---:|---:|---|"]
        for row in clips:
            links = " · ".join(f"[{label}]({cell(row[key])})" for label, key in (("IG", "ig_url"), ("TT", "tt_url"))
                               if row.get(key))
            details = [row.get(key) for key in ("hype", "kind", "payoff", "views_ig", "views_tt")]
            values = " | ".join(cell(str(value)) if value is not None and value != "" else "—" for value in details)
            lines.append(f"| {cell(row.get('title') or row['id'])} | {row['perf_score']:.1f} | {values} | {links or '—'} |")
    return lines


def report(rows: list[Review], counts: Counter, current: int, today: str, *,
           ranked: list[dict], dropped: int, decisions: dict[str, str]) -> str:
    approved = sum(r.decision == "yes" for r in rows)
    train = sum(split(r.id) == "train" for r in rows)
    lines = [
        f"# Hype calibration {today}", "",
        f"- {counts['exported']} exports: {counts['yes']} yes, {counts['no']} no, {counts['undecided']} without a decision.",
        f"- {len(rows)} scored reviews: {approved} yes, {len(rows) - approved} no. {train} train, {len(rows) - train} test.",
        f"- {counts['missing_queue']} decisions without a queue match and {counts['missing_score']} without a score.", "",
        "## Owner approval by hype", "", "| Hype | Reviews | Yes | Yes rate |", "|---|---:|---:|---:|",
    ]
    for label, low, high in BUCKETS:
        bucket = [r for r in rows if low <= r.hype < high]
        yes = sum(r.decision == "yes" for r in bucket)
        rate = f"{yes / len(bucket):.0%}" if bucket else "—"
        lines.append(f"| {label} | {len(bucket)} | {yes} | {rate} |")

    bars = range(60, 96, 5)
    metrics = {bar: scores(rows, bar) for bar in sorted({*bars, current})}
    best = max(bars, key=lambda bar: (metrics[bar][2], bar)) if rows else None
    lines += ["", "## Precision and recall", "", f"Current bar: {current}.",
              f"Best F1 bar: {best} ({metrics[best][2]:.1%}, higher bar wins ties)." if best is not None else
              "Best F1 bar: unavailable without scored reviews.", "",
              "| Bar | Precision | Recall | F1 |", "|---|---:|---:|---:|"]
    for bar, (precision, recall, f1) in metrics.items():
        label = f"{bar} (current)" if bar == current else str(bar)
        lines.append(f"| {label} | {precision:.1%} | {recall:.1%} | {f1:.1%} |")

    yes_tags = Counter(tag for r in rows if r.decision == "yes" for tag in set(r.tags))
    no_tags = Counter(tag for r in rows if r.decision == "no" for tag in set(r.tags))
    lines += ["", "## Owner tags", "", "| Tag | Yes | No |", "|---|---:|---:|"]
    for tag in sorted(yes_tags.keys() | no_tags.keys()):
        lines.append(f"| {cell(tag)} | {yes_tags[tag]} | {no_tags[tag]} |")

    posted = [r for r in rows if r.posted]
    viewed = [r for r in posted if r.views is not None]
    rho = insights.spearman([r.hype for r in viewed], [r.views for r in viewed])
    correlation = f"{rho:.2f}" if rho is not None else "unavailable (needs at least five clips and variation)"
    lines += ["", "## Views on reviewed posts", "",
              f"{len(viewed)} posted clips with views out of {len(posted)} reviewed posts. "
              f"Spearman(hype, views): {correlation}. Views sum the available platforms per clip.", "",
              "| Owner decision | Clips with views | Median views | Mean views |", "|---|---:|---:|---:|"]
    for decision in ("yes", "no"):
        values = [r.views for r in viewed if r.decision == decision]
        summary = f"{median(values):,.0f} | {mean(values):,.0f}" if values else "— | —"
        lines.append(f"| {decision} | {len(values)} | {summary} |")

    disagreements = sorted((r for r in rows if (r.hype >= current) != (r.decision == "yes")),
                           key=lambda r: (-abs(r.hype - current), r.id))
    lines += ["", "## Biggest disagreements", "",
              "| ID | Streamer | Hype | Owner | Split | Payoff | Owner note |", "|---|---|---:|---|---|---|---|"]
    for r in disagreements[:15]:
        lines.append(f"| {cell(r.id)} | {cell(r.streamer)} | {r.hype} | {r.decision} | {split(r.id)} | "
                     f"{cell(r.payoff)} | {cell(r.note)} |")

    lines += ["", *performance_section(ranked, dropped, decisions)]
    examples = []
    for r in [r for r in disagreements if r.note and split(r.id) == "train"][:15]:
        score = current if r.decision == "yes" else current - 1
        decision = "approved" if r.decision == "yes" else "rejected"
        what = f"{r.payoff}\nOwner note: {r.note}" if r.payoff else r.note
        examples.append((score, f"owner review ({decision})", f"{r.streamer}, {r.id}", what))
    train_performance = [r for r in ranked if split(r["id"]) == "train"]
    tail_size = min(5, len(train_performance) // 2)
    top = train_performance[:tail_size]
    bottom = train_performance[::-1][:tail_size]
    for score, clips in ((90, top), (60, bottom)):
        for r in clips:
            examples.append((score, f"analytics (IG/TT perf {r['perf_score']:.1f})",
                             f"{r.get('streamer') or 'unknown'}, {r['id']}", r.get("payoff") or r.get("title") or r["id"]))

    lines += ["", "## Proposed judge examples", "",
              "These examples use the train half. Owner reviews use the current bar for approvals and one point "
              "below it for rejections. Analytics examples use 90 for up to five top performers and 60 for up to "
              "five bottom performers. All examples are proposals for owner review.", ""]
    if examples:
        lines.append("```toml")
        for score, by, clip, what in examples:
            lines += ["[[example]]", f"score = {score}", f"by = {toml_string(by)}",
                      f"clip = {toml_string(clip)}", f"what = {toml_string(what)}", ""]
        lines.append("```")
    else:
        lines.append("No judge examples available from the train split.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, today: str | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    review_args = ap.add_mutually_exclusive_group()
    review_args.add_argument("reviews_dir", nargs="?", type=Path, help="owner review exports (legacy positional form)")
    review_args.add_argument("--reviews", type=Path, metavar="DIR", help="owner review exports")
    args = ap.parse_args(argv)
    reviews = args.reviews or args.reviews_dir
    if reviews is not None and not reviews.is_dir():
        ap.error(f"review directory does not exist: {reviews}")
    cfg = load_config(Path("channels.toml"))
    root = Path(cfg.get("post", {}).get("queue_dir", "queue"))
    current = cfg.get("judge", {}).get("min_hype", 90)
    today = today or date.today().isoformat()
    snapshots = insights.latest(root)
    rows, counts, decisions = load_reviews(reviews, root, {row["id"]: row for row in snapshots})
    settled, dropped = performance.settled_rows(snapshots)
    ranked = sorted(performance.scored_rows(settled), key=lambda row: (-row["perf_score"], row["id"]))
    if ranked and (root / "posted").is_dir():
        sources = {item.id: item.source_clip for _, item in P.items(root, "posted")}
        for row in ranked:
            verdict = source_metadata(sources.get(row["id"])).get("judge") or {}
            row["payoff"] = verdict.get("payoff") or row.get("payoff")
    out = Path("reports") / f"calibration-{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(rows, counts, current, today, ranked=ranked, dropped=dropped, decisions=decisions))
    print(f"{out} ({len(ranked)} clips with performance scores, {len(rows)} scored reviews)")


if __name__ == "__main__":
    main()
