"""livewatch.ytchat helpers: parsing the YouTube live chat page and responses (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "livewatch"))

import ytchat  # noqa: E402


def test_video_id_from_urls_without_network():
    for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
                "https://www.youtube.com/live/dQw4w9WgXcQ"):
        assert ytchat.video_id(url) == "dQw4w9WgXcQ"


def test_object_after_reads_the_json_after_a_marker():
    page = 'var x = 1; window["ytInitialData"] = {"a": {"b": [1, 2]}}; more()'
    assert ytchat._object_after(page, "ytInitialData") == {"a": {"b": [1, 2]}}
    assert ytchat._object_after(page, "missing") is None


def test_first_continuation_and_timeout():
    conts = [{"timedContinuationData": {"continuation": "abc", "timeoutMs": 3500}}]
    assert ytchat._first_continuation(conts) == ("abc", 3.5)
    assert ytchat._first_continuation([]) == (None, 2.0)


def test_message_text_joins_text_and_emoji_shortcuts():
    renderer = {"message": {"runs": [{"text": "W "},
                                     {"emoji": {"shortcuts": [":fire:"], "emojiId": "x"}},
                                     {"emoji": {"emojiId": "UCxyz/abc"}}]}}
    assert ytchat._message_text(renderer) == "W :fire:UCxyz/abc"


def test_reader_keeps_only_recent_messages():
    r = ytchat.YouTubeChatReader("https://www.youtube.com/@x/live", keep=10)
    r.add(100.0, "a", "old")
    r.add(115.0, "b", "new")
    assert [m[2] for m in r.snapshot()] == ["new"]
