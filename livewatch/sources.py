"""What to watch: turn a channel name or URL into a Source (platform, name, url).

Accepted:
  lacy | twitch.tv/lacy | https://www.twitch.tv/lacy            -> Twitch channel
  https://www.youtube.com/watch?v=ID | youtu.be/ID | /live/ID   -> one YouTube live video
  @handle | youtube.com/@handle[/live]                          -> that channel's current live
  youtube.com/channel/UC...[/live]                              -> that channel's current live

Only these hosts are accepted, and the URL handed to yt-dlp / streamlink is rebuilt
from the parsed parts, never the raw input.
"""
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

TWITCH_NAME = re.compile(r"^[A-Za-z0-9_]{3,25}$")
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
YT_CHANNEL = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}
YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


@dataclass(frozen=True)
class Source:
    platform: str   # "twitch" | "youtube"
    name: str       # folder / prompt name: twitch login, youtube handle, or video id
    url: str        # canonical URL for yt-dlp / streamlink / chat

    @property
    def label(self):
        return "Twitch" if self.platform == "twitch" else "YouTube"


def safe_name(text):
    """Lowercase name usable as a folder and a notes file name."""
    name = re.sub(r"[^a-z0-9_-]+", "_", text.lower().lstrip("@")).strip("_")
    return name[:40] or "stream"


def _twitch(login):
    if not TWITCH_NAME.match(login):
        raise ValueError(f"not a Twitch channel name: {login!r}")
    login = login.lower()
    return Source("twitch", login, f"https://www.twitch.tv/{login}")


def _yt_video(vid):
    if not YT_ID.match(vid):
        raise ValueError(f"not a YouTube video id: {vid!r}")
    return Source("youtube", safe_name(vid), f"https://www.youtube.com/watch?v={vid}")


def _yt_handle(handle):
    if not YT_HANDLE.match(handle):
        raise ValueError(f"not a YouTube handle: {handle!r}")
    return Source("youtube", safe_name(handle), f"https://www.youtube.com/{handle}/live")


def parse(target):
    """Source for a channel name or URL; ValueError for anything else."""
    target = (target or "").strip()
    if not target:
        raise ValueError("give a Twitch channel, a YouTube @handle, or a stream URL")
    if target.startswith("@"):
        return _yt_handle(target)
    if "/" not in target and "." not in target:
        return _twitch(target)
    url = urlparse(target if "://" in target else "https://" + target)
    host = (url.hostname or "").lower()
    if url.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {url.scheme!r}")
    parts = [p for p in url.path.split("/") if p]
    if host in TWITCH_HOSTS:
        if len(parts) != 1:
            raise ValueError("give a Twitch channel URL like twitch.tv/<channel> (not a VOD or clip)")
        return _twitch(parts[0])
    if host in YT_HOSTS:
        if host == "youtu.be" and parts:
            return _yt_video(parts[0])
        if parts[:1] == ["watch"]:
            return _yt_video((parse_qs(url.query).get("v") or [""])[0])
        if parts[:1] == ["live"] and len(parts) == 2:
            return _yt_video(parts[1])
        if parts and parts[0].startswith("@"):
            return _yt_handle(parts[0])
        if parts[:1] == ["channel"] and len(parts) >= 2 and YT_CHANNEL.match(parts[1]):
            return Source("youtube", safe_name(parts[1]),
                          f"https://www.youtube.com/channel/{parts[1]}/live")
        raise ValueError("give a YouTube live URL, a watch?v= URL, or a channel @handle")
    raise ValueError(f"only Twitch and YouTube are supported, not {host or target!r}")
