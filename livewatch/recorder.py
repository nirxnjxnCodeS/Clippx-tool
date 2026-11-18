"""Rolling recording of a live Twitch stream, cut into short wall-clock-named segments.

The stream is copied (no re-encode) into 2-second .ts files named by their start
time in epoch seconds, so any moment can be cut out afterwards by wall-clock time.
Segments older than the buffer are deleted.
"""
import glob
import os
import re
import shutil
import subprocess
import threading
import time

CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
SEGMENT = 2  # seconds


def valid_channel(name):
    return bool(CHANNEL_RE.match(name.lower()))


def stream_url(channel, max_height=1080):
    """HLS URL of a live channel, or None when it is offline."""
    if not valid_channel(channel):
        raise ValueError(f"not a Twitch channel name: {channel!r}")
    proc = subprocess.run(
        ["yt-dlp", "-g", "--no-warnings", "-f", f"best[height<={max_height}]/best",
         f"https://www.twitch.tv/{channel.lower()}"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip().splitlines()
    return url[0] if url else None


