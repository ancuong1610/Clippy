import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipfarm import post

TODAY = "2026-11-24"


def result(ident, views, duration=25, **metadata):
    return {"id": ident, "title": f"Short {ident}", "view_count": views, "duration": duration,
            "channel": "Benchmark channel", "like_count": 2000, "upload_date": "20261120", **metadata}


@pytest.fixture
def posted_clips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("channels.toml").write_text("[post.social]\nhandle_instagram = '@our_channel'\n")
    snapshots = []
    ts = datetime(2026, 11, 24, 12, tzinfo=timezone.utc).timestamp()
    for ident, views, kind in [
        ("best", 700, "crash_fail"), ("second", 400, "chase"), ("third", 100, None),
        ("too-new", 9000, None), ("outside-posted", 10000, None),
    ]:
        snapshots.append({"id": ident, "ts": ts, "posted": "2026-11-23" if ident == "too-new" else "2026-11-20",
                          "ig_views": views, "ig_likes": views // 10, "tt_views": views, "tt_likes": 0,
                          "length_s": 20, "kind": kind, "banner": "SAVE THE *CAR*"})
        if ident == "outside-posted":
            continue
        folder = Path("queue/posted") / ident
        folder.mkdir(parents=True)
        source = folder / "source.mp4"
        item = post.PostItem(ident, f"Title {ident}", "xqc", [], source_clip=str(source),
                             tiktok_caption="TikTok hook", instagram_caption="Instagram hook")
        (folder / "post.json").write_text(json.dumps(vars(item)))
        (folder / ("source.mp4" if ident == "second" else "short.mp4")).write_bytes(b"fake video")
        (folder / "manual_instagram.txt").write_text("Actual IG caption")
    (Path("queue") / "insights.jsonl").write_text("".join(json.dumps(row) + "\n" for row in snapshots))


def test_search_filters_and_ranks_top_five():
    from scripts import viral_benchmark as viral

    entries = [
        result("too-long", 999999, 91), result("unknown-views", None), result("unknown-time", 999999, None),
        result("ours-handle", 999999, uploader_id="@OUR_CHANNEL"),
        result("ours-url", 999999, channel_url="https://www.youtube.com/@our_channel/shorts"),
        result("ours-name", 999999) | {"channel": "Our Channel"}, result("known-post", 999999),
        result("a", 9000, 90), result("a", 9000, 90), result("b", 22000), result("c", 46000),
        result("d", 2000), result("e", 31300), result("f", 1000), result("g", 8000),
    ]

    def runner(cmd, **kwargs):
        assert "--ignore-config" in cmd
        assert cmd[-3:] == ["--flat-playlist", "--dump-json", "ytsearch40:xqc gta 6 crash fail #shorts"]
        return SimpleNamespace(stdout="\n".join(map(json.dumps, entries)))

    comps = viral.search_shorts("xqc gta 6 crash fail #shorts", {"ourchannel"}, runner=runner, own_ids={"known-post"})
    assert [item["id"] for item in comps] == ["c", "e", "b", "a", "g"]
    assert comps[0] == {
        "id": "c", "url": "https://www.youtube.com/watch?v=c", "title": "Short c", "channel": "Benchmark channel",
        "views": 46000, "likes": 2000, "duration": 25, "upload_date": "20261120",
    }


def test_dry_run_only_searches_ranked_settled_posts(posted_clips, monkeypatch, capsys):
    from scripts import viral_benchmark as viral

    queries = []

    def runner(cmd, **kwargs):
        assert "--flat-playlist" in cmd
        queries.append(cmd[-1])
        return SimpleNamespace(stdout=json.dumps(result("comp", 150000)))

    monkeypatch.setattr(viral.httpx, "post", lambda *a, **kw: pytest.fail("dry-run called the model"))
    monkeypatch.setattr(viral, "load_dotenv", lambda *a: pytest.fail("dry-run loaded .env"))
    assert viral.main(["--dry-run", "--max-calls", "0"], today=TODAY, runner=runner) == 0
    assert queries == ["ytsearch40:xqc gta 6 crash fail #shorts", "ytsearch40:xqc gta 6 chase #shorts",
                       "ytsearch40:xqc gta 6 shorts"]
    output = capsys.readouterr().out
    assert output.index("Title best") < output.index("Title second") < output.index("Title third")
    assert "too-new" not in output and "outside-posted" not in output
    assert "source.mp4" in output and "150,000" in output
    assert not Path("review").exists() and not Path("reports").exists()


def test_full_metadata_excludes_an_own_channel_missing_from_flat_search(tmp_path):
    from scripts import viral_benchmark as viral

    comp = {"id": "unrecognized", "url": "https://www.youtube.com/watch?v=unrecognized"}

    def runner(cmd, **kwargs):
        path = tmp_path / "unrecognized.mp4"
        path.touch()
        return SimpleNamespace(stdout=json.dumps({"filepath": str(path), "uploader_id": "@Our_Channel"}))

    assert viral.download_short(comp, tmp_path, {"ourchannel"}, runner=runner) == (None, {})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("max_calls, expected_calls, bad_response", [(0, 0, False), (1, 1, False), (2, 2, False),
                                                                    (1, 1, True)])
def test_full_benchmark_renders_proposals_and_caps_calls(
    posted_clips, monkeypatch, max_calls, expected_calls, bad_response
):
    from scripts import viral_benchmark as viral

    today = Path("review/viral") / TODAY
    other_day = today.with_name("2026-11-23")
    for folder in (today, other_day):
        folder.mkdir(parents=True)
        (folder / "old.mp4").touch()
    Path(".env").write_text("OPENROUTER_API_KEY=fake-test-key\nOPENROUTER_JUDGE_MODEL=fake-model\n")
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_JUDGE_MODEL"):
        monkeypatch.setenv(key, "will be read from .env")
        monkeypatch.delenv(key)
    downloads, requests = [], []

    def runner(cmd, **kwargs):
        if "--flat-playlist" in cmd:
            search_number = 2 if "chase" in cmd[-1] else 1
            return SimpleNamespace(stdout=json.dumps(result(f"comp-{search_number}", 150000)))
        if cmd[0] == "ffmpeg":
            return SimpleNamespace(stdout=b"audio sample")
        assert cmd[cmd.index("-f") + 1] == "worst[height>=360]/worst"
        assert cmd[cmd.index("--max-filesize") + 1] == "25M"
        dest = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
        assert dest.parent == today
        dest.write_bytes(b"fake download")
        downloads.append(dest)
        return SimpleNamespace(stdout=json.dumps({"like_count": 3000, "upload_date": "20261121",
                                                  "filepath": str(dest)}))

    def frames(video, n, width):
        assert n == 6
        return [(float(i), "abcd") for i in range(n)]

    def opening_frames(video, times, width):
        assert len(times) == 3 and 0 <= min(times) < max(times) < 2
        return [(t, "abcd") for t in times]

    def model_post(url, **kwargs):
        data = kwargs["json"]
        requests.append(data)
        assert data["model"] == "fake-model"
        assert kwargs["headers"]["Authorization"] == "Bearer fake-test-key"
        content = data["messages"][0]["content"]
        text = "\n".join(part["text"] for part in content if part["type"] == "text")
        if len(requests) == 1:
            assert all(value in text for value in ("SAVE THE *CAR*", "Actual IG caption", "TikTok hook"))
        assert sum(part["type"] == "image_url" for part in content) == 18
        assert sum(part["type"] == "input_audio" for part in content) == 2
        analysis = {"differences": ["Hook: Open on the vehicle."], "hook": "Reveal the stakes on frame one.",
                    "lead_in": "Keep the warning.", "length": "Cut to 18 seconds.", "text_overlay": "Name the car.",
                    "captions": "Keep the warning legible.", "audio": "Keep the reaction.",
                    "repost_recipe": ["Trim the pause.", "Retitle around the vehicle."],
                    "expected_effect": "More viewers may understand the stakes."}
        response = {"choices": [{"message": {"content": "{}" if bad_response else json.dumps(analysis)}}]}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)

    monkeypatch.setattr(viral.judge, "frames", frames)
    monkeypatch.setattr(viral.judge, "frames_at", opening_frames)
    monkeypatch.setattr(viral.httpx, "post", model_post)
    assert viral.main(["--max-calls", str(max_calls)], today=TODAY, runner=runner) == int(bad_response)
    assert len(requests) == len(downloads) == expected_calls
    assert not (today / "old.mp4").exists() and (other_day / "old.mp4").exists()
    report = Path(f"reports/viral-benchmark-{TODAY}.md").read_text()
    assert "PROPOSAL" in report
    assert "150,000" in report and "https://www.youtube.com/watch?v=comp-1" in report
    assert "View" in report and "Duration" in report and "Likes per view" in report
    assert ("2.00%" in report) == bool(expected_calls)
    assert ("1. Trim the pause." in report) == (bool(expected_calls) and not bad_response)
    assert ("Analysis failed (KeyError)." in report) == bad_response
    assert ("Hook · 2 of 2 groups" in report) == (expected_calls == 2)
