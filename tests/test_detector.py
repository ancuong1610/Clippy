import random

from clipfarm.detector import (
    ChatMessage,
    DetectorConfig,
    SpikeDetector,
    detect_offline,
    message_weight,
)


def steady_chat(start, end, per_s, users=50, text="hello", seed=1):
    rnd = random.Random(seed)
    msgs, t = [], start
    while t < end:
        for _ in range(per_s):
            msgs.append(ChatMessage(t + rnd.random(), f"u{rnd.randrange(users)}", text))
        t += 1
    return msgs


def burst(at, dur, per_s, text="KEKW KEKW", users=200, seed=2):
    return steady_chat(at, at + dur, per_s, users=users, text=text, seed=seed)


def test_no_event_on_steady_chat():
    assert detect_offline(steady_chat(0, 600, 3)) == []


def test_detects_burst_and_timestamps_it():
    msgs = steady_chat(0, 600, 2) + burst(300, 8, 20)
    events = detect_offline(msgs)
    assert len(events) == 1
    assert 300 <= events[0].ts <= 305
    assert events[0].ratio >= 3
    assert events[0].top_tokens[0][0] == "kekw"


def test_cooldown_merges_back_to_back_bursts():
    msgs = steady_chat(0, 600, 2) + burst(300, 5, 20) + burst(320, 5, 20, seed=3)
    assert len(detect_offline(msgs)) == 1


def test_two_separate_moments():
    msgs = steady_chat(0, 900, 2) + burst(300, 6, 20) + burst(700, 6, 20, seed=4)
    assert [round(e.ts, -2) for e in detect_offline(msgs)] == [300, 700]


def test_ratio_scales_with_huge_chat():
    # 40 msg/s normal chat (Kai-sized): a 2x bump is not a moment, a 5x one is.
    normal = steady_chat(0, 600, 40, users=5000)
    assert detect_offline(normal + burst(300, 6, 40, text="hi", users=5000)) == []
    assert len(detect_offline(normal + burst(300, 6, 200, users=5000))) == 1


def test_no_trigger_during_warmup():
    msgs = burst(5, 6, 30)
    assert detect_offline(msgs, cfg=DetectorConfig(warmup_s=60)) == []


def test_tiny_chat_needs_unique_users():
    msgs = steady_chat(0, 600, 0) + burst(300, 6, 5, users=2)
    assert detect_offline(msgs) == []


def test_message_weight():
    cfg = DetectorConfig()
    assert message_weight("hello there", cfg)[0] == 1.0
    w, hits = message_weight("CLIP IT NOW", cfg)
    assert w > 1.5 and "clip it" in hits


def test_live_api_tick_order():
    det = SpikeDetector("x", DetectorConfig(warmup_s=0))
    for t in range(120):
        det.add(ChatMessage(t, f"u{t % 7}", "hi"))
        assert det.tick(t) is None
    for i in range(60):
        det.add(ChatMessage(120 + i / 20, f"v{i}", "LMAO"))
    assert det.tick(123) is not None


def test_wave_trigger_in_capped_chat():
    """Constant 10 msg/s (volume can't spike) but half the chatters suddenly spam one emote."""
    import random
    random.seed(1)
    words = [f"word{i}" for i in range(200)]
    msgs = []
    for s in range(240):
        for k in range(10):
            u = f"u{random.randrange(3000)}"
            text = "OOOO OOOO" if 180 <= s < 186 and k < 6 else " ".join(random.sample(words, 3))
            msgs.append(ChatMessage(s + k / 10, u, text))
    ev = detect_offline(msgs, "x", DetectorConfig())
    assert len(ev) == 1 and ev[0].trigger == "wave" and ev[0].wave_token == "hype"  # OOOO belongs to the hype mood
    assert 180 <= ev[0].ts <= 190 and ev[0].wave_share >= 0.3


def test_mixed_laughter_counts_as_one_wave():
    """Half of chat laughing in different emotes (LOL / LMAO / KEKW / ICANT) is one laugh wave."""
    import random
    random.seed(2)
    words = [f"word{i}" for i in range(200)]
    laughs = ["LOL", "LMAO", "KEKW", "ICANT", "PepeLaugh", "lol lol"]
    msgs = []
    for s in range(240):
        for k in range(10):
            u = f"u{random.randrange(3000)}"
            text = laughs[k % len(laughs)] if 180 <= s < 186 and k < 6 else " ".join(random.sample(words, 3))
            msgs.append(ChatMessage(s + k / 10, u, text))
    ev = detect_offline(msgs, "x", DetectorConfig())
    assert len(ev) == 1 and ev[0].trigger == "wave" and ev[0].wave_token == "lol"


def test_mood_peak_and_unison_bonus():
    from clipfarm.detector import mood_peak, unison_bonus
    chat = [{"t": i * 0.2, "user": f"u{i}", "text": ["KEKW", "LMAO", "ICANT LOL"][i % 3] if i % 10 < 7 else f"word{i}"}
            for i in range(50)]
    share, mood = mood_peak(chat)
    assert mood == "lol" and 0.65 <= share <= 0.85  # ~70 % laughing; peak window can be a bit higher
    assert unison_bonus(0.30) == 0 and unison_bonus(0.60) == 15 and 0 < unison_bonus(0.5) < 15
    assert mood_peak([{"t": 0, "user": "a", "text": "LOL"}]) == (0.0, "")  # too few chatters


def test_chat_unison_bonus_only_for_gameplay_with_payoff():
    from clipfarm.judge import final_score
    chat = [{"t": i * 0.2, "user": f"u{i}", "text": "KEKW"} for i in range(40)]
    assert final_score({"hype": 80, "cutscene": False}, chat)["hype"] == 95
    assert final_score({"hype": 70, "cutscene": False}, chat)["hype"] == 70  # no payoff on screen: no bonus
    assert final_score({"hype": 80, "cutscene": True}, chat)["hype"] == 80  # owner review: cutscenes flopped
    assert final_score({"hype": 90, "cutscene": True}, chat)["hype"] == 80
    assert final_score({"hype": 90, "cutscene": True, "streamer_caused": True}, chat)["hype"] == 90


def test_game_filter_category_then_title():
    from clipfarm.recorder import plays
    g = r"grand theft auto vi|\bgta ?(6|vi)\b"
    assert plays({"category": "Grand Theft Auto VI", "title": "x"}, g)
    assert plays({"category": "Gaming", "title": "PLAYING GTA 6 EARLY ACCESS"}, g)  # YouTube
    assert not plays({"category": "Grand Theft Auto IV", "title": "PLAYING EVERY GTA"}, g)
    assert not plays({"category": "Just Chatting", "title": "GTA 6 TOMORROW reacts"}, g)
    assert not plays({"category": "Rainbow Six Siege", "title": "ranked"}, g)
    assert plays({"category": "Anything"}, None)


def test_chat_gap_then_burst_is_not_a_spike():
    """YouTube chat reader reconnect: 30 s of silence, then a normal batch arrives at once -> no event."""
    import random
    random.seed(3)
    msgs = []
    for s in range(120):  # steady 3 msg/s
        for k in range(3):
            msgs.append(ChatMessage(s + k / 3, f"u{random.randrange(500)}", f"word{random.randrange(300)} hi"))
    for k in range(60):  # gap 120-150 s, then 60 msgs in 2 s (backlog flush)
        msgs.append(ChatMessage(150 + k / 30, f"u{random.randrange(500)}", f"word{random.randrange(300)} hi"))
    for s in range(152, 200):
        for k in range(3):
            msgs.append(ChatMessage(s + k / 3, f"u{random.randrange(500)}", f"word{random.randrange(300)} hi"))
    assert detect_offline(msgs, "yt", DetectorConfig()) == []


def test_off_topic_gets_no_chat_bonus():
    from clipfarm.judge import final_score
    chat = [{"t": i * 0.2, "user": f"u{i}", "text": "KEKW"} for i in range(40)]
    assert final_score({"hype": 20, "gta6": False}, chat)["hype"] == 20
