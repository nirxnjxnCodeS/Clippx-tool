"""livewatch chat scoring and spike detection (pure Python, no network).

Times start at T0 (epoch-like), since started_at=0 would read as "not started"."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "livewatch"))

import chat  # noqa: E402

T0 = 1_700_000_000.0


def _steady(start, end, per_second, text="hi"):
    out, t = [], start
    step = 1.0 / per_second
    i = 0
    while t < end:
        out.append((T0 + t, f"u{i}", f"{text} {i}"))
        t += step
        i += 1
    return out


def test_not_ready_during_warmup():
    msgs = _steady(0, 50, 2)
    s = chat.score_at(msgs, T0 + 50, started_at=T0)
    assert s["ready"] is False


def test_steady_chat_scores_low():
    msgs = _steady(0, 400, 2)
    s = chat.score_at(msgs, T0 + 400, started_at=T0)
    assert s["ready"] is True
    assert s["score"] < 30


def test_spike_scores_high():
    msgs = _steady(0, 390, 1) + [(T0 + 390 + i * 0.1, f"x{i}", "LOL") for i in range(100)]
    s = chat.score_at(msgs, T0 + 400, started_at=T0)
    assert s["ready"] is True
    assert s["msgs"] >= 90
    assert s["repetition"] > 0.9
    assert s["score"] >= 90


def test_empty_history_is_not_trusted():
    # a chat outage leaves the baseline empty: any chat would look like a spike
    msgs = [(T0 + 395 + i * 0.05, f"x{i}", "hi") for i in range(100)]
    s = chat.score_at(msgs, T0 + 400, started_at=T0)
    assert s["ready"] is False


def test_detector_fires_once_per_spike_and_respects_cooldown():
    det = chat.Detector(threshold=60, cooldown=40, min_msgs=10)
    hot = {"ready": True, "msgs": 50, "score": 80}
    cold = {"ready": True, "msgs": 2, "score": 0}
    assert det.update(100, hot) is None          # spike starts
    assert det.update(101, dict(hot, score=95)) is None
    assert det.update(102, cold) == (100, 95)    # spike ends: start time + peak
    assert det.update(110, hot) is None          # inside the cooldown: ignored
    assert det.update(111, cold) is None
    assert det.update(150, hot) is None          # after the cooldown: a new spike
    assert det.update(158, hot) == (150, 80)     # capped at 8 s


def test_detector_needs_minimum_volume():
    det = chat.Detector(threshold=60, cooldown=0, min_msgs=10)
    assert det.update(1, {"ready": True, "msgs": 3, "score": 100}) is None
    assert det.active is None


def test_add_keeps_messages_in_time_order():
    r = chat.ChatReader("lacy")
    r.add(T0 + 10, "a", "one")
    r.add(T0 + 8, "b", "late")          # delivered out of order
    r.add(T0 + 11, "c", "three")
    times = [m[0] for m in r.snapshot()]
    assert times == sorted(times)


def test_parse_api_messages_keeps_text_only():
    data = {"items": [
        {"snippet": {"type": "textMessageEvent", "publishedAt": "2026-11-19T10:00:00.5Z",
                     "displayMessage": "W"}, "authorDetails": {"displayName": "fan"}},
        {"snippet": {"type": "superChatEvent", "publishedAt": "2026-11-19T10:00:01Z"}},
    ]}
    got = chat.parse_api_messages(data)
    assert len(got) == 1
    ts, user, text = got[0]
    assert (user, text) == ("fan", "W")
    assert ts == 1795082400.5


def test_reader_for_platform():
    import sources
    assert type(chat.reader_for(sources.parse("lacy"))) is chat.ChatReader
    yt = chat.reader_for(sources.parse("@SomeStreamer"))
    assert isinstance(yt, chat.YouTubeChatReader)
    assert yt.url == "https://www.youtube.com/@SomeStreamer/live"
