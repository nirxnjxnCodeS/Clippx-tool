"""Spot Twitch's "Commercial break in progress" screen in a clip.

Logged-out viewers (our recorder) get that screen instead of the stream while the streamer
runs ads: a smooth blue/purple gradient over the whole frame, a small "twitch" logo and one
line of text. Real stream frames are full of detail, so two numbers separate them cleanly:
the share of the frame in that blue-purple colour range, and how much fine detail there is.
"""
import subprocess

import numpy as np

SAMPLE_W, SAMPLE_H = 320, 180


def frames_per_second(path):
    """One small RGB frame per second of the video, as numpy arrays."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                          f"fps=1,scale={SAMPLE_W}:{SAMPLE_H}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    size = SAMPLE_W * SAMPLE_H * 3
    return [np.frombuffer(raw[i:i + size], np.uint8).reshape(SAMPLE_H, SAMPLE_W, 3)
            for i in range(0, len(raw) - size + 1, size)]


def is_ad_frame(rgb):
    import cv2
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # OpenCV hue 0-180: cyan ~90, blue ~120, purple/magenta ~140-160
    tinted = (h >= 85) & (h <= 165) & (s >= 70) & (v >= 40)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detail = cv2.Laplacian(gray, cv2.CV_64F).var()
    # Measured: ad screen 98% tinted; real Lacy gameplay ~45%. Real frames always carry the
    # facecam, chat overlay and game detail, so the colour share alone separates them; the
    # detail cap is a second guard against a rare all-blue game scene (open sea at night).
    return tinted.mean() >= 0.90 and detail < 2000


def ad_seconds(path):
    """[True/False per second]: is that second the commercial-break screen?"""
    return [is_ad_frame(f) for f in frames_per_second(path)]


