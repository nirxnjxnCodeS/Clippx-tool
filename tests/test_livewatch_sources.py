"""livewatch.sources: channel names and URLs -> Source."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "livewatch"))

import sources  # noqa: E402


@pytest.mark.parametrize("target", ["lacy", "Lacy", "twitch.tv/lacy", "https://www.twitch.tv/lacy",
                                    "https://m.twitch.tv/LACY/"])
def test_twitch_targets(target):
    s = sources.parse(target)
    assert (s.platform, s.name, s.url) == ("twitch", "lacy", "https://www.twitch.tv/lacy")


@pytest.mark.parametrize("target", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
                                    "youtu.be/dQw4w9WgXcQ", "https://youtube.com/live/dQw4w9WgXcQ"])
def test_youtube_video_targets(target):
    s = sources.parse(target)
    assert s.platform == "youtube"
    assert s.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert s.name == "dqw4w9wgxcq"


@pytest.mark.parametrize("target", ["@SomeStreamer", "https://www.youtube.com/@SomeStreamer",
                                    "youtube.com/@SomeStreamer/live"])
def test_youtube_handle_targets(target):
    s = sources.parse(target)
    assert (s.platform, s.name, s.url) == ("youtube", "somestreamer",
                                           "https://www.youtube.com/@SomeStreamer/live")


def test_youtube_channel_id():
    cid = "UC" + "a" * 22
    s = sources.parse(f"https://www.youtube.com/channel/{cid}")
    assert s.url == f"https://www.youtube.com/channel/{cid}/live"


@pytest.mark.parametrize("target", ["", "ab", "lacy; rm -rf /", "https://evil.example/lacy",
                                    "https://www.twitch.tv/videos/123", "file:///etc/passwd",
                                    "https://www.youtube.com/watch?v=short", "https://www.youtube.com/feed"])
def test_rejected(target):
    with pytest.raises(ValueError):
        sources.parse(target)


def test_url_is_rebuilt_not_passed_through():
    s = sources.parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&x=--exec")
    assert s.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
