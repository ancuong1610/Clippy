from clipfarm.insights import _match, group, report, spearman, views


def _row(i, hype, yt, kind="crash_fail", hook=None, pct=60.0, hour=19):
    return {"id": str(i), "title": f"clip {i}", "hype": hype, "kind": kind, "streamer": "xqc", "yt_views": yt,
            "ig_views": 10, "yt_avg_view_pct": pct, "yt_hook_ret": hook, "post_hour_berlin": hour, "ts": 1}


def test_spearman_needs_five_and_ranks():
    assert spearman([1, 2, 3], [1, 2, 3]) is None
    assert spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert spearman([1, 2, 3, 4, 5], [50, 40, 30, 20, 10]) == -1.0


def test_manual_posts_match_queue_items_by_caption():
    cands = {"a": ["xQc vs MR COW in GTA 6 😭\n🎥 xQc live", "xQc Loses To A COW"],
             "b": ["zackrawrr goes wrong 😭 #gta6", "zackrawrr Touches ONE Button in GTA 6 and It All Goes Wrong"]}
    assert _match("xQc vs MR COW in GTA 6 😭\n🎥 xQc live on stream #gta6", cands) == "a"
    assert _match("zackrawrr Touches ONE Button in GTA 6 and It All Goes Wrong #streamer #gta6", cands) == "b"
    assert _match("something unrelated entirely", cands) is None


def test_report_groups_and_ranks_by_total_views():
    rows = [_row(i, 80 + i, 100 * i, "stunt" if i % 2 else "crash_fail", 0.9) for i in range(1, 7)]
    assert views(rows[0]) == 110
    g = dict((k, (n, v)) for k, n, v, _, _ in group(rows, "kind"))
    assert g["stunt"][0] == 3 and g["crash_fail"][0] == 3
    md = report(rows)
    assert "Spearman(hype, total views) = 1.0" in md and "## Clip kind" in md
    assert md.index("clip 6") < md.index("clip 1")  # top first
