import random

from clipfarm.detector import ChatMessage, DetectorConfig, SpikeDetector, detect_offline, message_weight


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
