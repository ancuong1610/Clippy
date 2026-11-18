from PIL import Image

from clipfarm.chatoverlay import clean, draw_chat, safe, schedule, select


def test_safety_filter_blocks_slurs_links_and_personal_info():
    assert safe("KEKW no way")
    assert not safe("go to www.example.com")
    assert not safe("call 555-123-4567")
    assert not safe("kys bro")
    assert not safe("@someone look")


def test_clean_strips_emoji_and_spam_walls():
    assert clean("LMAO 😭😭😭") == "LMAO"
    assert clean("KEKW KEKW KEKW KEKW KEKW") == "KEKW KEKW KEKW"
    assert clean("x" * 60).endswith("…") and len(clean("x" * 60)) == 34


def test_select_prefers_ai_picks_matched_to_real_messages():
    chat = [{"t": 10.0 + i * 0.3, "user": f"u{i}", "text": t} for i, t in
            enumerate(["KEKW", "NO WAY", "he really said that", "www.spam.com", "CLIP IT"])]
    out = select(chat, ["u2: he really said that", "fake: invented line", "u3: www.spam.com"], spike_t=10)
    texts = [m["text"] for m in out]
    assert "he really said that" in texts
    assert "invented line" not in texts and all("spam" not in t for t in texts)


def test_schedule_spacing_and_window():
    msgs = [{"t": 10.0, "user": "a", "text": "x"}, {"t": 10.1, "user": "b", "text": "y"}]
    ev = schedule(msgs, spike_t=10, duration=30)
    assert ev[1]["show"] - ev[0]["show"] >= 0.8 and all(e["hide"] > e["show"] for e in ev)
    ev2 = schedule(msgs, spike_t=10, duration=30, has_timestamps=False)
    assert 9 <= ev2[0]["show"] < ev2[1]["show"] <= 19
    assert ev2[-1]["hide"] - ev2[0]["show"] >= 8  # long enough to read


def test_draw_chat_draws_inside_box():
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    draw_chat(img, [{"user": "kekw_enjoyer", "text": "NO WAY HE SAID THAT"}], (60, 1360, 1020, 1770))
    x0, y0, x1, y1 = img.getbbox()
    assert x0 >= 60 and x1 <= 1020 and y0 >= 1300 and y1 <= 1770
