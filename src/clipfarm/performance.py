"""Score Instagram and TikTok performance from saved clip snapshots."""

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from math import ceil
from statistics import mean

SETTLE_SECONDS = 48 * 3600
COMPONENTS = ("views_ig", "views_tt", "ig_watch_ratio", "share_rate", "ig_save_rate")


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator and denominator > 0 else None


def metrics(row: dict) -> dict:
    """Return both platforms' views, IG watch ratio, and rates per 1,000 views."""
    ig_views, tt_views = row.get("ig_views"), row.get("tt_views")
    ig_shares, tt_shares = row.get("ig_shares"), row.get("tt_shares")
    ig_share_rate = _ratio(ig_shares, ig_views)
    tt_share_rate = _ratio(tt_shares, tt_views)
    ig_save_rate = _ratio(row.get("ig_saves"), ig_views)
    measured = [(shares, views) for shares, views, rate in ((ig_shares, ig_views, ig_share_rate),
                                                           (tt_shares, tt_views, tt_share_rate)) if rate is not None]
    return {
        "views_ig": ig_views,
        "views_tt": tt_views,
        "ig_watch_ratio": _ratio(row.get("ig_avg_watch_s"), row.get("length_s")),
        "ig_share_rate": ig_share_rate * 1000 if ig_share_rate is not None else None,
        "tt_share_rate": tt_share_rate * 1000 if tt_share_rate is not None else None,
        "ig_save_rate": ig_save_rate * 1000 if ig_save_rate is not None else None,
        "share_rate": 1000 * sum(shares for shares, _ in measured) / sum(views for _, views in measured)
        if measured else None,
    }


def _distribution(values: list[dict]) -> dict[str, list[float]]:
    return {key: sorted(row[key] for row in values if row[key] is not None) for key in COMPONENTS}


def _percentile(value: float, ordered: list[float]) -> float:
    if len(ordered) == 1:
        return 50.0
    low, high = bisect_left(ordered, value), bisect_right(ordered, value)
    rank = (low + high - 1) / 2 if low < high else min(low, len(ordered) - 1)
    return 100 * rank / (len(ordered) - 1)


def _score(values: dict, distribution: dict[str, list[float]]) -> float | None:
    percentiles = [_percentile(values[key], order) for key, order in distribution.items()
                   if values[key] is not None and order]
    return mean(percentiles) if percentiles else None


def perf_score(row: dict, cohort: list[dict]) -> float | None:
    """Average the available 0–100 cohort percentiles for IG views, TT views,
    IG watch ratio (average watch seconds / clip length), pooled IG+TT shares per
    1,000 views, and IG saves per 1,000 views. Share counts and views are pooled
    only from platforms with both measurements and positive views.

    Each percentile uses 100 × (average zero-based rank of ties) / (n − 1)
    across the cohort's available values. A lone value gets 50. Missing
    components are skipped and a clip with no scoreable IG/TT data returns None.
    """
    return _score(metrics(row), _distribution([metrics(clip) for clip in cohort]))


def scored_rows(cohort: list[dict]) -> list[dict]:
    """Return copies of scoreable rows with their derived metrics and performance scores."""
    values = [metrics(row) for row in cohort]
    distribution = _distribution(values)
    scored = []
    for row, clip_metrics in zip(cohort, values):
        score = _score(clip_metrics, distribution)
        if score is not None:
            scored.append({**row, **clip_metrics, "perf_score": score})
    return scored


def _posted_timestamp(posted: str | None) -> float | None:
    if not posted:
        return None
    try:
        parsed = datetime.fromisoformat(posted)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()


def settled_rows(rows: list[dict]) -> tuple[list[dict], int]:
    """Keep posts at least 48 hours old at the newest snapshot and count exclusions."""
    newest = max((r["ts"] for r in rows if r.get("ts") is not None), default=None)
    if newest is None:
        return [], len(rows)
    cutoff = newest - SETTLE_SECONDS
    settled = [row for row in rows if (posted := _posted_timestamp(row.get("posted"))) is not None and posted <= cutoff]
    return settled, len(rows) - len(settled)


def top_cutoff(rows: list[dict], fraction: float) -> float:
    """Score at the last place in the top fraction, rounding up the number of places."""
    return sorted((row["perf_score"] for row in rows), reverse=True)[ceil(len(rows) * fraction) - 1]


def hype_hit_rates(rows: list[dict]) -> tuple[dict[int, tuple[int, float, float | None]], int | None]:
    """Return (passing, coverage, top-quartile hit rate) at each bar and the best eligible bar."""
    rates = {}
    cutoff = top_cutoff(rows, 0.25) if rows else None
    for bar in range(60, 96):
        passing = [row for row in rows if row.get("hype") is not None and row["hype"] >= bar]
        hits = sum(row["perf_score"] >= cutoff for row in passing)
        rates[bar] = (len(passing), len(passing) / len(rows) if rows else 0.0, hits / len(passing) if passing else None)
    eligible = [bar for bar, (_, coverage, _) in rates.items() if coverage >= 0.25]
    best = max(eligible, key=lambda bar: (rates[bar][2], rates[bar][1], -bar), default=None)
    return rates, best
