#!/usr/bin/env python3
"""Background music + timed sound effects (+ optional zoom punches) for clips.

Library (folders in the openshorts root, override with env LW_SFX_DIR / LW_MUSIC_DIR):
  sfx for shorts/<name>.mp3        label = cleaned file name ("boom", "wrong-answer", ...)
  Music for bg/<track>.mp3         mood from Music for bg/moods.json

Claude (through the bridge) reads the clip's timed transcript and returns a plan:
  {"mood": "funny", "sfx": [{"word": 12, "sound": "boom", "punch": true}, ...]}
which is then mixed with ffmpeg: music ducked under speech, effects at exact word times.

Re-mix an existing clip (e.g. to compare subtle vs heavy):
  docker exec -w /app openshorts-backend python livewatch/audiofx.py livewatch/clips/valkyrae/X.mp4 --sfx-max 4
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
SFX_DIR = os.environ.get("LW_SFX_DIR", os.path.join(APP, "sfx for shorts"))
MUSIC_DIR = os.environ.get("LW_MUSIC_DIR", os.path.join(APP, "Music for bg"))
MOODS = ["hype", "funny", "chill", "suspense", "emotional"]

MUSIC_DB = -20.0      # music level under the stream (before ducking)
SFX_DB = -4.0         # effects level (files are peak-normalised to -3 dB)
PUNCH_ZOOM = 0.08     # +8% zoom for 0.25 s on punch effects


# ---------------------------------------------------------------- library
def _label(filename):
    name = os.path.splitext(os.path.basename(filename))[0].lower()
    name = re.sub(r"#\S+", " ", name)                                   # hashtags
    name = re.sub(r"\b(sound effects?|sfx|for editing|free|meme|fyp)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    name = re.sub(r"\bsound\b", "", name).strip("-")
    return re.sub(r"-+", "-", name) or "sfx"


TYPING_FILE = os.path.join(SFX_DIR, "typing.mp3")   # reserved for the typewriter hook
TYPING_DB = -6.0


def sfx_library():
    """Effects the AI may place. Typing sounds are reserved for the typewriter hook."""
    lib = {}
    if os.path.isdir(SFX_DIR):
        for f in sorted(os.listdir(SFX_DIR)):
            if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg")):
                label = _label(f)
                if not label.startswith("typing"):
                    lib.setdefault(label, os.path.join(SFX_DIR, f))
    return lib


def music_library():
    """{mood: [paths]} from moods.json; tracks without an entry count as 'hype'."""
    tracks = [f for f in sorted(os.listdir(MUSIC_DIR))
              if f.lower().endswith((".mp3", ".wav", ".m4a"))] if os.path.isdir(MUSIC_DIR) else []
    try:
        moods = json.load(open(os.path.join(MUSIC_DIR, "moods.json")))
    except (OSError, ValueError):
        moods = {}
    lib = {}
    for t in tracks:
        for m in moods.get(t) or ["hype"]:
            lib.setdefault(m, []).append(os.path.join(MUSIC_DIR, t))
    return lib


def pick_music(mood):
    lib = music_library()
    if not lib:
        return None
    return random.choice(lib.get(mood) or lib.get("hype") or next(iter(lib.values())))


