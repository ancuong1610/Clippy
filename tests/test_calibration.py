import hashlib
import json
import subprocess
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipfarm import catchup, post as P


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_full_chat_retries_and_merges_best_pieces(tmp_path, monkeypatch):
    monkeypatch.setattr(catchup, "tdl_path", lambda: "TwitchDownloaderCLI")
    batches = iter([
        [("one", 2)],
        [("boundary", 9), ("first", 1)],
        [("thin", 3)],
        [("last", 13), ("boundary", 9), ("next", 11)],
    ])
    ranges, pauses = [], []

    def runner(cmd, **kwargs):
        ranges.append((cmd[cmd.index("-b") + 1], cmd[cmd.index("-e") + 1]))
        comments = [{"_id": ident, "content_offset_seconds": t} for ident, t in next(batches)]
        write_json(Path(cmd[cmd.index("-o") + 1]), {"video": {"id": "100"}, "comments": comments})

    out = tmp_path / "chat.json"
    assert catchup.full_chat("https://www.twitch.tv/videos/100", 20, out, piece_s=10, min_msgs=3,
                             runner=runner, sleep=pauses.append) == out
    merged = json.loads(out.read_text())
    assert merged["video"] == {"id": "100"}
    assert [c["_id"] for c in merged["comments"]] == ["first", "boundary", "next", "last"]
    assert ranges == [("0s", "10s")] * 3 + [("10s", "20s")]
    assert pauses == [20, 20]


@pytest.mark.parametrize("live", [True, False])
def test_finished_vods_respects_live_status(live):
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        if "--flat-playlist" in cmd:
            rows = "v103\t120\tNewest\nv102\t60\tSecond\nv101\t90\tThird\n"
            return subprocess.CompletedProcess(cmd, 0, rows, "")
        if live:
            return subprocess.CompletedProcess(cmd, 0, "True\n", "")
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: [twitch:stream] xqc: The channel is not currently live")

    vods = catchup.finished_vods("xqc", n=2, runner=runner)
    assert [(v.id, v.duration, v.title) for v in vods] == (
        [("102", 60, "Second"), ("101", 90, "Third")] if live else
        [("103", 120, "Newest"), ("102", 60, "Second")]
    )
    assert any(cmd[-1] == "https://www.twitch.tv/xqc" for cmd in calls)
    assert vods[0].url == f"https://www.twitch.tv/videos/{vods[0].id}"


def test_daily_plan_filters_and_round_robins(monkeypatch):
    from scripts import daily_review_batch as daily

    channels = [{"name": "first"}, {"name": "second", "until": "2026-11-24"},
                {"name": "old", "until": "2026-11-23"}, {"name": "yt", "platform": "youtube"}]
    seen = {"11": "2026-11-23"}
    archives = {"first": [("11", "GTA 6"), ("12", "other game"), ("13", "GTA 6"), ("14", "gta vi")],
                "second": [("21", "gta 6"), ("22", "gta 6")]}

    def finished(channel, n):
        return [catchup.Vod(ident, f"https://www.twitch.tv/videos/{ident}", 60, title)
                for ident, title in archives[channel]]

    monkeypatch.setattr(daily, "finished_vods", finished)
    active = daily.active_channels(channels, "2026-11-24")
    plan = daily.scan_plan(active, seen, r"\bgta (6|vi)\b", 3)
    assert [(channel["name"], vod.id) for channel, vod in plan] == [
        ("first", "13"), ("second", "21"), ("first", "14")]
    # launch-week VODs were clipped live already: below skip_vods_before they are never planned
    plan = daily.scan_plan(active, seen, r"\bgta (6|vi)\b", 5, min_vod=14)
    assert [(channel["name"], vod.id) for channel, vod in plan] == [
        ("first", "14"), ("second", "21"), ("second", "22")]


def test_select_candidates_filters_floors_scenes_and_queued_sources(tmp_path):
    from scripts import daily_review_batch as daily

    rows = [
        ("too-low", "100", 800, 59),
        ("duplicate", "100", 100, 93),
        ("best", "100", 280, 94),
        ("farther", "100", 461, 79),
        ("other-vod", "101", 280, 88),
        ("queued", "102", 100, 100),
        ("queued-scene", "102", 110, 97),
        ("floor", "103", 100, 60),
    ]
    candidates = [daily.Candidate(tmp_path / f"{name}.mp4", "xqc", vod, spike, {"hype": hype})
                  for name, vod, spike, hype in rows]
    used = [c for c in candidates if c.clip.stem == "queued"]
    assert [c.clip.stem for c in daily.select_candidates(candidates, used, min_hype=60, limit=2)] == [
        "best", "other-vod"]
    assert [c.clip.stem for c in daily.select_candidates(candidates, used, min_hype=60, limit=10)] == [
        "best", "other-vod", "farther", "floor"]


def test_batch_uses_existing_judgment_and_never_auto_approves(tmp_path, monkeypatch):
    from scripts import daily_review_batch as daily

    monkeypatch.chdir(tmp_path)
    source = Path("clips/xqc/catchup/100/0-02-00_s50.mp4")
    verdict = {"hype": 94, "visual": 90, "bonus": 4, "cutscene": False, "payoff": "Plane crash"}
    write_json(source.with_suffix(".json"), {"vod": "https://www.twitch.tv/videos/100", "event": {"ts": 150},
                                            "score": {"total": 50}, "judge": verdict})
    source.write_bytes(b"source")
    cfg = {"calibration": {"until": "2026-12-08", "per_day": 1, "min_hype": 60,
                           "spikes_per_vod": 40, "max_vods_per_day": 8},
           "channel": [{"name": "xqc"}], "post": {"auto_approve_min_score": 90},
           "campaign": [{"name": "Example", "streamers": ["xqc"]}]}

    def fake_make(clip, streamer, context, *, out_dir, campaign):
        assert clip == source and streamer == "xqc" and campaign["name"] == "Example"
        out_dir.mkdir()
        short = out_dir / "short.mp4"
        short.write_bytes(b"short")
        return {"short": short, "titles": {"youtube_titles": ["Plane crash"]}}

    monkeypatch.setattr(daily, "load_config", lambda path: cfg)
    monkeypatch.setattr(daily, "make", fake_make)
    monkeypatch.setattr(daily, "make_preview", lambda src, out: out.write_bytes(src.read_bytes()))
    monkeypatch.setattr(daily, "finished_vods", lambda *a, **kw: pytest.fail("unexpected discovery"))
    monkeypatch.setattr(daily, "load_dotenv", lambda *a, **kw: True)
    daily.main(["--from-existing"], today="2026-11-24")
    daily.main(["--from-existing"], today="2026-11-24")

    root = Path("queue")
    [(folder, item)] = P.items(root, "pending")
    assert P.items(root, "approved") == []
    assert item.source_clip == str(source)
    assert (folder / "short.mp4").is_file()
    doc = json.loads((Path("review/batches/2026-11-24/docs") / f"{item.id}.json").read_text())
    assert doc["judge"] == {k: v for k, v in verdict.items() if k != "hype"}
    assert (doc["batch"], doc["queue"], doc["hype"], doc["video"]) == ("2026-11-24", "pending", 94, "")
    assert Path(doc["preview"]).read_bytes() == b"short"
    assert cfg["post"]["auto_approve_min_score"] == 90
    state = json.loads((root / "calibration_state.json").read_text())
    assert [b["queued"] for b in state["batches"]] == [1, 0]


def test_daily_scan_saves_verdicts_and_never_rescans_vod(tmp_path, monkeypatch):
    from scripts import daily_review_batch as daily

    monkeypatch.chdir(tmp_path)
    cfg = {"calibration": {"until": "2026-12-08", "per_day": 0, "min_hype": 60,
                           "spikes_per_vod": 3, "max_vods_per_day": 1},
           "clip": {"min_score": 35, "game": "gta 6"}, "detector": {}, "channel": [{"name": "xqc"}]}
    url = "https://www.twitch.tv/videos/100"
    monkeypatch.setattr(daily, "load_config", lambda path: cfg)
    monkeypatch.setattr(daily, "load_dotenv", lambda path: True)
    monkeypatch.setattr(daily, "finished_vods", lambda channel, n: [catchup.Vod("100", url, 1800, "GTA 6")])
    monkeypatch.setattr(daily, "full_chat", lambda url, duration, path: write_json(path, {"comments": []}))
    events = [SimpleNamespace(ratio=ratio, wave_share=share) for ratio, share in [(4, .1), (3, .6), (5, .1), (3, .1)]]
    monkeypatch.setattr(daily, "detect_offline", lambda *args: events)

    def cut(vod, selected, pre, post, out, msgs):
        assert (vod, selected, pre, post, out) == (url, [events[1], events[2], events[0]], 45, 8,
                                                  Path("clips/xqc/catchup/100"))
        files = []
        for index, signal in enumerate((34, 35, 50)):
            clip = out / f"{index}_s{signal}.mp4"
            clip.write_bytes(b"clip")
            write_json(clip.with_suffix(".json"), {"score": {"total": signal}})
            files.append(clip)
        return files

    def judge(clip, meta):
        if meta["score"]["total"] == 35:
            raise SystemExit("test judge error")
        assert meta["score"]["total"] == 50
        return {"hype": 87}

    monkeypatch.setattr(daily, "cut_vod", cut)
    monkeypatch.setattr(daily, "judge", judge)
    daily.main([], today="2026-11-24")
    monkeypatch.setattr(daily, "full_chat", lambda *a: pytest.fail("VOD rescanned"))
    monkeypatch.setattr(daily, "cut_vod", lambda *a: pytest.fail("VOD clips recut"))
    daily.main([], today="2026-11-25")

    out = Path("clips/xqc/catchup/100")
    assert "judge" not in json.loads((out / "0_s34.json").read_text())
    assert json.loads((out / "1_s35.json").read_text())["judge_error"] == "test judge error"
    assert json.loads((out / "2_s50.json").read_text())["judge"]["hype"] == 87
    state = json.loads(Path("queue/calibration_state.json").read_text())
    assert state["scanned_vods"] == {"100": "2026-11-24"}
    assert [(b["vods_scanned"], b["spikes"], b["judged"], b["judge_errors"]) for b in state["batches"]] == [
        (1, 4, 1, 1), (0, 0, 0, 0)]
    monkeypatch.setattr(daily, "finished_vods", lambda *a, **kw: pytest.fail("discovery after deadline"))
    daily.main(["--dry-run"], today="2026-12-09")


def test_failed_review_document_leaves_candidate_available_for_retry(tmp_path, monkeypatch):
    from scripts import daily_review_batch as daily

    monkeypatch.chdir(tmp_path)
    short = Path("short.mp4")
    short.write_bytes(b"rendered")
    candidate = daily.Candidate(Path("clip.mp4"), "xqc", "100", 35, {"hype": 90})
    root = Path("queue")
    root.mkdir()
    docs = Path("review/batches/2026-11-24/docs")
    monkeypatch.setattr(daily, "make", lambda *a, **kw: {"short": short, "titles": {"youtube_titles": ["Title"]}})
    monkeypatch.setattr(daily, "make_preview", lambda src, out: out.write_bytes(src.read_bytes()))

    def reject_doc(path, doc):
        raise OSError("cannot write review doc")

    with monkeypatch.context() as patch:
        patch.setattr(daily, "write_json", reject_doc)
        with pytest.raises(OSError, match="cannot write review doc"):
            daily.queue_candidate(candidate, {}, root, "2026-11-24", docs)
    assert P.items(root, "pending") == []
    assert not list(daily.PREVIEWS.glob("*.mp4"))
    daily.queue_candidate(candidate, {}, root, "2026-11-24", docs)
    assert len(P.items(root, "pending")) == 1


def test_calibration_joins_legacy_scores_and_proposes_only_train_notes(tmp_path, monkeypatch):
    from scripts import calibrate_hype as calibrate

    monkeypatch.chdir(tmp_path)
    root, reviews = Path("queue"), Path("reviews")
    examples = Path("judge_examples.toml")
    original = '[[example]]\nscore = 95\nwhat = "existing"\n'
    examples.write_text(original)
    Path("channels.toml").write_text("[judge]\nmin_hype = 90\n")
    fixtures = [("below", 55, "no"), ("near", 68, "no"), ("middle", 76, "no"), ("good", 84, "yes"),
                ("good2", 89, "yes"), ("top", 98, "yes"), ("train-no", 95, "no"), ("test-no", 96, "no")]
    train, test = [next(f"note-{i}" for i in range(100)
                        if hashlib.sha256(f"note-{i}".encode()).digest()[0] % 2 == parity) for parity in (0, 1)]
    fixtures[-2:] = [(train, 95, "no"), (test, 96, "no")]
    snapshots = []
    for i, (ident, hype, decision) in enumerate(fixtures):
        clip = Path("clips") / f"{ident}.mp4"
        if ident != "below":
            write_json(clip.with_suffix(".json"), {"judge": {"hype": hype, "payoff": "Payoff"}})
        item = P.PostItem(ident, ident, "xqc", [], score=55, source_clip=str(clip))
        write_json(root / "posted" / ident / "post.json", vars(item))
        note = 'Train note "é" 🎥\nsecond line' if ident == train else "Held-out note" if ident == test else ""
        write_json(reviews / f"{ident}.json", {"data": {"decision": decision, "tags": ["tag"], "note": note}})
        snapshots.append({"id": ident, "ts": 1, "ig_views": i * 100, "tt_views": 10})
    (root / "insights.jsonl").write_text("".join(json.dumps(row) + "\n" for row in snapshots))
    write_json(reviews / "unmatched.json", {"decision": "yes"})

    assert calibrate.split(train) == calibrate.split(train) == "train"
    assert calibrate.split(test) == calibrate.split(test) == "test"
    calibrate.main([str(reviews)], today="2026-11-24")
    report = Path("reports/calibration-2026-11-24.md").read_text()
    assert "| <60 | 1 | 0 | 0% |" in report
    assert "| 75–84 | 2 | 1 | 50% |" in report
    assert "| 90+ | 3 | 1 | 33% |" in report
    assert "Best F1 bar: 80" in report
    assert "Spearman" in report and "8 posted clips with views" in report
    assert "Held-out note" in report
    block = report.split("```toml\n")[1].split("```")[0]
    proposal = tomllib.loads(block)["example"]
    assert len(proposal) == 1 and train in proposal[0]["clip"]
    assert 'Train note "é" 🎥\nsecond line' in proposal[0]["what"]
    assert "Held-out note" not in block
    assert examples.read_text() == original


def test_posted_review_docs_join_latest_views_and_skip_missing_videos(tmp_path, monkeypatch, capsys):
    from scripts import posted_review_docs as posted

    monkeypatch.chdir(tmp_path)
    root = Path("queue")
    Path("channels.toml").write_text("[post]\n")
    for ident in ("short", "fallback", "missing"):
        clip = Path("clips") / f"{ident}.mp4"
        write_json(clip.with_suffix(".json"), {"judge": {"hype": 88, "visual": 85, "payoff": "Jump"}})
        item = P.PostItem(ident, f"Title {ident}", "xqc", [], source_clip=str(clip), score=88)
        write_json(root / "posted" / ident / "post.json", vars(item))
    (root / "posted/short/short.mp4").write_bytes(b"short video")
    Path("clips/fallback.mp4").write_bytes(b"raw video")
    snapshots = [{"id": "short", "ts": 2, "yt_views": 70, "ig_views": 200, "tt_views": 0,
                  "posted": "2026-11-23T10:00:00+00:00"},
                 {"id": "short", "ts": 1, "ig_views": 50}]
    (root / "insights.jsonl").write_text("".join(json.dumps(row) + "\n" for row in snapshots))
    monkeypatch.setattr(posted, "make_preview", lambda src, out: out.write_bytes(src.read_bytes()))
    posted.main()
    docs = Path("review/batches/posted/docs")
    assert sorted(path.stem for path in docs.glob("*.json")) == ["fallback", "short"]
    short = json.loads((docs / "short.json").read_text())
    assert short["views"] == {"instagram": 200, "tiktok": 0, "youtube": 70}
    assert (short["queue"], short["posted"], short["payoff"], short["video"]) == ("posted", "2026-11-23", "Jump", "")
    assert Path(short["preview"]).read_bytes() == b"short video"
    fallback = json.loads((docs / "fallback.json").read_text())
    assert Path(fallback["preview"]).read_bytes() == b"raw video"
    assert "1 skipped (no video)" in capsys.readouterr().out


def test_performance_percentiles_skip_missing_components():
    from clipfarm import performance

    first = {"ig_views": 100, "tt_views": 100, "ig_shares": 3, "tt_shares": 4,
             "ig_saves": 1, "ig_avg_watch_s": 10, "length_s": 20}
    second = {"ig_views": 300, "ig_shares": 0, "ig_saves": 0, "ig_avg_watch_s": 20, "length_s": 20}
    third = {"tt_views": 200, "tt_shares": 2}
    fourth = {"ig_views": 100, "ig_shares": 0, "ig_avg_watch_s": 10, "length_s": 0}
    missing = {"yt_views": 9999}
    cohort = [first, second, third, fourth, missing]

    metrics = performance.metrics(first)
    assert {key: metrics[key] for key in ("views_ig", "views_tt", "ig_watch_ratio", "ig_share_rate",
                                          "tt_share_rate", "ig_save_rate", "share_rate")} == {
        "views_ig": 100, "views_tt": 100, "ig_watch_ratio": 0.5, "ig_share_rate": 30,
        "tt_share_rate": 40, "ig_save_rate": 10, "share_rate": 35,
    }
    assert performance.metrics(fourth)["ig_watch_ratio"] is None
    assert performance.metrics({"tt_views": 0, "tt_shares": 1})["tt_share_rate"] is None
    assert performance.metrics({"ig_views": 100, "ig_shares": 2, "tt_views": 900})["share_rate"] == 20
    assert performance.perf_score(first, cohort) == pytest.approx(45)
    assert performance.perf_score(second, cohort) == pytest.approx((100 + 100 + 100 / 6 + 0) / 4)
    assert performance.perf_score(third, cohort) == pytest.approx((100 + 200 / 3) / 2)
    assert performance.perf_score(missing, cohort) is None
    assert performance.perf_score({"ig_views": 0}, [{"ig_views": 0}]) == 50


def test_performance_settles_relative_to_newest_snapshot():
    from clipfarm import performance

    newest = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    cutoff = newest - timedelta(hours=48)
    boundary = {"ts": newest.timestamp() - 1, "posted": cutoff.isoformat()}
    young = {"ts": newest.timestamp(), "posted": (cutoff + timedelta(seconds=1)).isoformat()}
    older = {"ts": newest.timestamp() - 10, "posted": (cutoff - timedelta(days=1)).isoformat()}
    no_date = {"ts": newest.timestamp() - 1}
    assert performance.settled_rows([boundary, young, older, no_date]) == ([boundary, older], 2)
    assert performance.settled_rows([]) == ([], 0)


def test_performance_hit_rate_prefers_eligible_bar_with_greatest_coverage():
    from clipfarm import performance

    rows = [{"perf_score": 100 - i, "hype": hype}
            for i, hype in enumerate([95, 92, 65, 85, 80, 75, 70, 60, 59])]
    hit_rates, best = performance.hype_hit_rates(rows)
    assert hit_rates[95] == (1, 1 / 9, 1.0)
    assert hit_rates[93] == (1, 1 / 9, 1.0)
    assert hit_rates[81] == (3, 3 / 9, 2 / 3)
    assert best == 81
    assert performance.hype_hit_rates([{"hype": 59, "perf_score": 100}])[1] is None


def test_performance_report_without_reviews_includes_sorted_correlations_and_train_examples(tmp_path, monkeypatch):
    from scripts import calibrate_hype as calibrate

    monkeypatch.chdir(tmp_path)
    Path("channels.toml").write_text("[post]\nqueue_dir = 'queue'\n")
    train = [f"clip-{i}" for i in range(40) if calibrate.split(f"clip-{i}") == "train"][:12]
    held_out = next(f"held-out-{i}" for i in range(40) if calibrate.split(f"held-out-{i}") == "test")
    ts = datetime(2026, 11, 24, tzinfo=timezone.utc).timestamp()
    snapshots = []
    for rank, ident in enumerate([*train, held_out]):
        clip = Path("clips") / f"{ident}.mp4"
        write_json(clip.with_suffix(".json"), {"judge": {"payoff": f"Payoff for {ident}"}})
        item = P.PostItem(ident, f"Title {ident}", "xqc", [], score=80, source_clip=str(clip))
        write_json(Path("queue/posted") / ident / "post.json", vars(item))
        snapshots.append({"id": ident, "ts": ts, "posted": "2026-11-21T00:00:00Z", "title": item.title,
                          "ig_views": rank + 1, "hype": 60 + rank % 4 * 10, "visual": 90 - rank,
                          "kind": "stunt", "cutscene": False, "streamer_caused": True,
                          "streamer": "xqc", "post_hour_berlin": 0, "ig_url": f"https://example.com/{ident}"})
    snapshots.append({**snapshots[0], "id": "young", "posted": "2026-11-23T00:00:00Z"})
    Path("queue/insights.jsonl").write_text("".join(json.dumps(row) + "\n" for row in snapshots))
    Path("reviews").mkdir()

    calibrate.main([], today="2026-11-24")
    output = Path("reports/calibration-2026-11-24.md")
    report = output.read_text()
    assert "## Real performance (Instagram + TikTok)" in report
    assert "13 clips with performance scores" in report
    assert "1 dropped" in report
    assert report.index("| visual | 13 | -1.00 |") < report.index("| hype | 13 |")
    assert report.index("| ig_views | 13 | 1.00 |") < report.index("| hype | 13 |")
    assert f"Payoff for {held_out}" in report and f"[IG](https://example.com/{held_out})" in report
    assert "| kind | stunt | 13 | 50.0 |" in report
    assert "### Top-quartile hit rate" in report
    proposal = tomllib.loads(report.split("```toml\n")[1].split("```")[0])["example"]
    assert [e["clip"].rsplit(", ", 1)[1] for e in proposal if e["score"] == 90] == train[-5:][::-1]
    assert [e["clip"].rsplit(", ", 1)[1] for e in proposal if e["score"] == 60] == train[:5]
    assert all(e["by"].startswith("analytics (IG/TT perf ") for e in proposal)
    assert not Path("judge_examples.toml").exists()

    calibrate.main(["--reviews", "reviews"], today="2026-11-24")
    assert output.read_text() == report
    write_json(Path("reviews") / f"{train[0]}.json", {"decision": "no"})
    write_json(Path("reviews") / f"{held_out}.json", {"decision": "no"})
    calibrate.main(["--reviews", "reviews"], today="2026-11-24")
    assert "1 of 2 (50.0%)" in output.read_text()


@pytest.mark.parametrize("response", ["success", "rejected", "nested_error", "raises"])
def test_ig_metrics_with_fallback_for_rejected_new_metrics(monkeypatch, response):
    from clipfarm import insights

    calls = []
    new_metrics = ["total_interactions", "ig_reels_video_view_total_time"]
    base_metrics = ["views", "reach", "likes", "comments", "shares", "saved", "ig_reels_avg_watch_time"]
    values = dict(zip(base_metrics, [900, 800, 20, 2, 3, 0, 7500]))

    def runner(client, slug, args, cfg):
        if slug == "INSTAGRAM_GET_USER_INFO":
            return {"data": {"id": "account"}}
        if slug == "INSTAGRAM_GET_IG_USER_MEDIA":
            return {"data": {"data": [{"id": "reel"}]}}
        assert slug == "INSTAGRAM_GET_IG_MEDIA_INSIGHTS"
        calls.append(args["metric"])
        data = [{"name": key, "values": [{"value": value}]} for key, value in values.items()]
        if calls[-1] == base_metrics + new_metrics:
            if response == "raises":
                raise RuntimeError("unsupported metric")
            if response == "rejected":
                return {"successful": False, "error": "unsupported metric"}
            if response == "nested_error":
                return {"data": {"error": {"message": "unsupported metric"}}}
            data += [{"name": new_metrics[0], "total_value": {"value": 25}},
                     {"name": new_metrics[1], "values": [{"value": 6750123}]}]
        return {"data": {"data": data}}

    monkeypatch.setattr(P, "_run", runner)
    [row] = insights._ig(None, {})
    assert calls == ([base_metrics + new_metrics] if response == "success"
                     else [base_metrics + new_metrics, base_metrics])
    assert (row["ig_views"], row["ig_saves"], row["ig_avg_watch_s"]) == (900, 0, 7.5)
    assert (row["ig_interactions"], row["ig_watch_total_s"]) == (
        (25, pytest.approx(6750.123)) if response == "success" else (None, None))


def test_muted_share_reads_ffmpeg_silencedetect():
    # Twitch mutes VOD parts with copyrighted music: those clips must never reach review (owner 2026-11-24: "Mất sound")
    from clipfarm import review

    def runner(cmd, **kw):
        assert "silencedetect=noise=-60dB:d=1" in " ".join(cmd)
        err = ("Duration: 00:00:38.00, start\n[silencedetect] silence_end: 20.0 | silence_duration: 19.0\n"
               "[silencedetect] silence_end: 38.0 | silence_duration: 4.0\n")
        return SimpleNamespace(returncode=0, stderr=err, stdout="")

    assert review.muted_share(Path("x.mp4"), runner=runner) == pytest.approx(23 / 38)

    def loud(cmd, **kw):
        return SimpleNamespace(returncode=0, stderr="Duration: 00:00:38.00, start\n", stdout="")

    assert review.muted_share(Path("x.mp4"), runner=loud) == 0.0


@pytest.mark.parametrize("calibration_window, expected_pre", [({}, 45), ({"vod_pre_s": 52}, 52)])
def test_daily_scan_uses_calibration_lead_in_and_skips_muted_clips(
    tmp_path, monkeypatch, calibration_window, expected_pre
):
    from scripts import daily_review_batch as daily

    monkeypatch.chdir(tmp_path)
    cfg = {"calibration": {"until": "2026-12-08", "per_day": 5, "min_hype": 60,
                           "spikes_per_vod": 1, "max_vods_per_day": 1, **calibration_window},
           "clip": {"min_score": 35, "game": "gta 6", "pre_s": 30, "post_s": 9},
           "detector": {}, "channel": [{"name": "xqc"}]}
    url = "https://www.twitch.tv/videos/100"
    monkeypatch.setattr(daily, "load_config", lambda path: cfg)
    monkeypatch.setattr(daily, "load_dotenv", lambda path: True)
    monkeypatch.setattr(daily, "finished_vods", lambda channel, n: [catchup.Vod("100", url, 1800, "GTA 6")])
    monkeypatch.setattr(daily, "full_chat", lambda url, duration, path: write_json(path, {"comments": []}))
    monkeypatch.setattr(daily, "detect_offline", lambda *args: [SimpleNamespace(ratio=4, wave_share=.1)])

    def cut(vod, selected, pre, post, out, msgs):
        assert (pre, post) == (expected_pre, 9)
        clip = out / "0_s80.mp4"
        clip.write_bytes(b"clip")
        write_json(clip.with_suffix(".json"), {"score": {"total": 80}})
        return [clip]

    monkeypatch.setattr(daily, "cut_vod", cut)
    monkeypatch.setattr(daily, "muted_share", lambda clip: 0.9)
    monkeypatch.setattr(daily, "judge", lambda *a: pytest.fail("muted clip sent to the judge"))
    monkeypatch.setattr(daily, "queue_candidate", lambda *a: pytest.fail("muted clip queued"))
    daily.main([], today="2026-11-24")
    assert json.loads(Path("clips/xqc/catchup/100/0_s80.json").read_text())["muted"] == 0.9


def test_titles_prompt_requests_the_setup_a_stranger_needs():
    from clipfarm.titles import build_prompt

    prompt = build_prompt("xqc", "GTA 6", "", [])
    assert "the tightest 15-30s window" in prompt
    assert "best_start must include the setup a stranger needs to understand the payoff" in prompt
    assert "who/what is happening" in prompt
    assert "typically 3-10 s before the event" in prompt
    assert "no dead air" in prompt
