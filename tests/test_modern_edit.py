import importlib
import json
import tomllib
from pathlib import Path

import pytest

from clipfarm import edit, judge, titles

ROOT = Path(__file__).resolve().parents[1]
FACE = (0.0, 0.0, 0.2, 0.3)


def test_judge_accepts_major_reactions_and_visible_discoveries():
    examples = tomllib.loads((ROOT / "judge_examples.toml").read_text())["example"]
    owner = {e["score"]: e["what"].lower() for e in examples
             if e["by"] == "owner reference (viral TikTok, 1M+ views)"}
    assert "ending" in owner[92] and "hands on his head" in owner[92]
    assert "new mechanic" in owner[90] and "aim" in owner[90] and "several" in owner[90]
    rejected = judge.RUBRIC.split("What the owner rejected:", 1)[1].split("Score four parts", 1)[0].lower()
    assert "ordinary" in rejected and "final mission" in rejected
    assert "visible streamer reaction" in rejected and "new mechanic" in rejected
    assert judge.final_score({"hype": 92, "cutscene": True, "story_reaction": True}, [])["hype"] == 92
    assert judge.final_score({"hype": 92, "cutscene": True}, [])["hype"] == 80


@pytest.mark.parametrize(("label", "expected"), [
    ("Lacy gets shoved out of his car", "LACY GETS SHOVED OUT OF HIS CAR"),
    ("Will Lacy survive this?", "LACY LOSES HIS CAR IN GTA 6"),
    (None, "LACY LOSES HIS CAR IN GTA 6"),
])
def test_title_label_is_parsed_with_a_short_youtube_fallback(label, expected):
    response = {"youtube_titles": ["Lacy loses his car in GTA 6 after a wild chase!"], "thumbnail_text": "WATCH *LACY*"}
    if label is not None:
        response["label"] = label
    assert '"label"' in titles.build_prompt("lacy", "GTA 6", "look out", [])
    assert titles.validate(response)["label"] == expected


@pytest.fixture
def render_graph(monkeypatch):
    runs = []
    monkeypatch.setattr(edit.subprocess, "check_output", lambda *_: b"12.0")

    def inspect(cmd, *, check):
        concat = Path(cmd[cmd.index("-safe") + 3]).read_text()
        label = Path(cmd[cmd.index("-filter_complex") - 1]) if cmd.count("-i") == 3 else None
        if label:
            with edit.Image.open(label) as png:
                assert png.getbbox() and edit.WHITE in png.get_flattened_data()
        runs.append((cmd[cmd.index("-filter_complex") + 1], concat, cmd))

    monkeypatch.setattr(edit.subprocess, "run", inspect)
    return runs


def test_statement_label_is_a_full_duration_overlay(render_graph, tmp_path, monkeypatch):
    seen = []
    render = edit.render_overlay

    def capture(title, *args, **kwargs):
        seen.append(title)
        return render(title, *args, **kwargs)

    monkeypatch.setattr(edit, "render_overlay", capture)
    edit.make_short(Path("clip.mp4"), tmp_path / "clip.mp4", "WATCH *LACY*", "stack", FACE,
                    words_override=[], title_until=4.5, label="LACY LOSES HIS CAR")
    graph, concat, command = render_graph[-1]
    assert "[2:v]format=rgba[label]" in graph
    assert "[base][label]overlay=0:0:eof_action=repeat" in graph
    assert "enable=" not in graph
    assert command[command.index("-t") + 1] == "12.000"
    assert "WATCH *LACY*" not in seen
    assert "duration 12.000" in concat


@pytest.mark.parametrize(("layout", "facecam", "duration", "switches"), [
    ("stack", FACE, 12.0, True),
    ("stack", FACE, 7.9, False),
    ("blur", None, 12.0, False),
    ("square", None, 12.0, False),
])
def test_peak_filter_uses_a_bounded_window_only_on_long_stacks(
        render_graph, tmp_path, monkeypatch, layout, facecam, duration, switches):
    monkeypatch.setattr(edit.subprocess, "check_output", lambda *_: str(duration).encode())
    edit.make_short(Path("clip.mp4"), tmp_path / "clip.mp4", layout=layout, facecam=facecam,
                    words_override=[], peak_at=4.0, peak_fullscreen_s=2.0)
    graph = render_graph[-1][0]
    if switches:
        assert "gte(t,4.000)*lt(t,6.000)" in graph
        assert graph.index("[fullscreen]overlay") < graph.index("[base][ov]overlay")
    else:
        assert "enable=" not in graph


def test_punch_caption_cards_break_on_limits_punctuation_and_pauses():
    words = [edit.Word(0, .1, "look"), edit.Word(.15, .2, "at"), edit.Word(.25, .3, "that"),
             edit.Word(.35, .4, "car,"), edit.Word(.5, .6, "it"), edit.Word(.86, .9, "costs"),
             edit.Word(.95, 1.0, "$50k")]
    cards = edit.chunk(words, max_words=3, max_gap=.25, punctuation=".,!?;:")
    assert [[w.text for w in c] for c in cards] == [["look", "at", "that"], ["car,"], ["it"], ["costs", "$50k"]]
    overlay = edit.render_overlay("", cards[-1], -1, 700, 0, edit.CAPTION_PRESETS["punch"])
    assert edit.RED in overlay.get_flattened_data()
    overlay = edit.render_overlay("", cards[1], -1, 700, 0, edit.CAPTION_PRESETS["punch"],
                                  label="LACY LOSES HIS CAR")
    assert edit.RED in overlay.get_flattened_data()
    overlay = edit.render_overlay("", cards[1], -1, 700, 0, edit.CAPTION_PRESETS["punch"])
    assert edit.RED not in overlay.get_flattened_data()


@pytest.mark.parametrize(("label", "peak", "style", "explicit", "judge_time", "hook", "expected"), [
    (True, True, "punch", None, 6.0, 1.5, ("LACY LOSES HIS CAR", 5.5, "punch")),
    (False, True, "punch", None, 6.0, 0, ("", 4.0, "punch")),
    (True, False, "punch", None, 6.0, 0, ("LACY LOSES HIS CAR", None, "punch")),
    (True, True, "karaoke", None, 6.0, 0, ("LACY LOSES HIS CAR", 4.0, "karaoke")),
    (False, False, "karaoke", None, 6.0, 0, ("", None, "karaoke")),
    (False, False, "punch", "classic", 6.0, 0, ("", None, "classic")),
    (True, True, "punch", None, None, 1.5, ("LACY LOSES HIS CAR", 6.5, "punch")),
])
def test_make_honors_independent_edit_switches(monkeypatch, tmp_path, label, peak, style, explicit,
                                             judge_time, hook, expected):
    make = importlib.import_module("clipfarm.make")
    monkeypatch.chdir(tmp_path)
    Path("channels.toml").write_text(f'[edit]\nlabel = {str(label).lower()}\n'
                                     f'peak_fullscreen = {str(peak).lower()}\n'
                                     f'peak_fullscreen_s = 2.5\ncaptions = "{style}"\n')
    clip = tmp_path / "clip.mp4"
    clip.with_suffix(".words.json").write_text("[]")
    clip.with_suffix(".json").write_text(json.dumps({"judge": {"payoff_at": judge_time}}))
    monkeypatch.setattr(make, "trim", lambda src, start, end, out: out)
    monkeypatch.setattr(make, "cold_open", lambda src, at, length, out: out)
    monkeypatch.setattr(make, "detect_facecam", lambda *_: (FACE, (0.5, 0.5)))
    monkeypatch.setattr(make, "loudest", lambda *_: 5.0)
    monkeypatch.setattr(judge, "impact_time", lambda *_: None)
    monkeypatch.setattr(make, "make_thumbnails", lambda *_, **__: [])
    options = {}

    def capture(src, out, title, layout, box, **kwargs):
        options.update(kwargs)
        assert title == "WATCH *LACY*"
        return out

    monkeypatch.setattr(make, "make_short", capture)
    make.make(clip, captions=explicit, hook_s=hook,
              titles={"youtube_titles": ["Lacy loses his car"], "thumbnail_text": "WATCH *LACY*", "window": (2, 14)})
    assert (options.get("label", ""), options.get("peak_at"), options["captions"]) == expected
    if expected[1] is not None:
        assert options["peak_fullscreen_s"] == 2.5


def test_all_render_switches_off_keep_the_legacy_graph(render_graph, tmp_path):
    edit.make_short(Path("clip.mp4"), tmp_path / "clip.mp4", "WATCH *LACY*", "stack", FACE,
                    words_override=[], title_until=4.5, label="", peak_at=None, captions="karaoke")
    graph, concat, command = render_graph[-1]
    assert graph == (edit._filter("stack", FACE)[0]
                     + ";[1:v]format=rgba[ov];[base][ov]overlay=0:0:eof_action=repeat,format=yuv420p[v]")
    assert command.count("-i") == 2
    assert "duration 4.500" in concat and "duration 7.500" in concat


def test_skip_dark_start_moves_past_a_black_game_screen():
    from clipfarm.make import skip_dark_start
    dark_then_game = [19.3, 19.0, 19.4, 19.1, 19.6, 19.3, 197.7, 57.6, 56.6, 50.0]
    assert skip_dark_start(Path("x.mp4"), 7.77, 31.38, luma=lambda *a: dark_then_game) == 10.77
    assert skip_dark_start(Path("x.mp4"), 7.77, 31.38, luma=lambda *a: [40.0, 41.0]) == 7.77
    assert skip_dark_start(Path("x.mp4"), 7.77, 31.38, luma=lambda *a: [19.0] * 16) == 7.77  # all dark: leave it
    assert skip_dark_start(Path("x.mp4"), 10.0, 23.0, luma=lambda *a: dark_then_game) == 10.0  # would be < 12 s


def test_censor_softens_swears_and_keeps_case():
    from clipfarm.make import censor
    assert censor("WHAT THE FUCK?") == "WHAT THE F*CK?"
    assert censor("Shit, he's fucking done") == "Sh*t, he's f*cking done"
    assert censor("Scunthorpe classic") == "Scunthorpe classic"
