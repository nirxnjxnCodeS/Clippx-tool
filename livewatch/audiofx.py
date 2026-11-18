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


# ---------------------------------------------------------------- AI plan
def plan_prompt(channel, words, sfx_labels, sfx_max):
    timed = " ".join(f"[{i}]{w['word']}" for i, w in enumerate(words))[:6000]
    return (
        f"Clip of the Twitch streamer {channel}. Transcript, each word numbered [n]:\n{timed}\n\n"
        f"Plan the sound design. Available sound effects: {', '.join(sfx_labels)}.\n"
        f"Return JSON with:\n"
        f'- "mood": one of {", ".join(MOODS)} (overall feel of the clip)\n'
        f'- "sfx": up to {sfx_max} items {{"word": n, "sound": name, "punch": true/false}}. Place an effect '
        "right AFTER the word it reacts to (a punchline, a fail, a big play, a shocked reaction). Only where "
        "it really adds something; fewer is better than random. Never in the first word. "
        '"punch" = also do a quick zoom (use only for the biggest moment).\n'
        "Reply with only the JSON object."
    )


def clean_plan(raw, words, sfx_lib, sfx_max):
    mood = raw.get("mood") if raw.get("mood") in MOODS else "hype"
    items, used_times = [], []
    for it in raw.get("sfx") or []:
        try:
            idx, sound = int(it.get("word")), str(it.get("sound", "")).strip().lower()
        except (TypeError, ValueError):
            continue
        if sound not in sfx_lib or not (1 <= idx < len(words)):
            continue
        t = float(words[idx]["end"])
        if t < 1.0 or any(abs(t - u) < 1.5 for u in used_times):   # spread effects out
            continue
        used_times.append(t)
        items.append({"time": round(t, 2), "sound": sound, "punch": bool(it.get("punch"))})
        if len(items) >= sfx_max:
            break
    return {"mood": mood, "sfx": items, "typewriter": bool(raw.get("typewriter"))}


def ai_plan(channel, words, sfx_max):
    """Ask Claude via the bridge for mood + sfx placements. Falls back to mood 'hype', no sfx."""
    sys.path.insert(0, HERE)
    import pipeline  # for .env loading
    pipeline._load_env()
    lib = sfx_library()
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    if not base or not words or sfx_max <= 0:
        return {"mood": "hype", "sfx": []}
    try:
        import httpx
        r = httpx.post(f"{base}/chat/completions", timeout=180, json={
            "model": os.environ.get("LLM_MODEL", "sonnet"),
            "messages": [{"role": "user", "content": plan_prompt(channel, words, sorted(lib), sfx_max)}],
            "response_format": {"type": "json_object"}})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        raw = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return clean_plan(raw, words, lib, sfx_max)
    except Exception as e:
        print(f"[audio] AI sound plan skipped ({type(e).__name__}: {e})", flush=True)
        return {"mood": "hype", "sfx": []}


# ---------------------------------------------------------------- render
def _duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", path])
    return float(out.decode().strip())


FACE_HOLD = 0.9       # seconds the facecam fills the screen on a reaction punch


def punch_filter(punches, w=1080, h=1920):
    """Video filter: brief zoom-in at each punch time (empty string if none)."""
    if not punches:
        return ""
    z = "+".join(f"between(t,{t:.2f},{t + 0.25:.2f})" for t in punches)
    return (f"scale=w='trunc({w}*(1+{PUNCH_ZOOM}*({z}))/2)*2':h=-2:eval=frame,"
            f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2")


SHAKE_PX = 14         # max shake offset in pixels
SHAKE_SECONDS = 0.35
SHAKE_MAX = 3


def loud_peaks(video, max_peaks=SHAKE_MAX, min_gap=2.5):
    """Times (s) where the clip's own audio jumps well above its normal level: screams, hype.
    Rules: >= 10 dB above the clip's median, louder than -18 dBFS, a local peak, >= min_gap apart,
    not in the first 0.5 s. Returns the loudest few, in time order."""
    import numpy as np
    sr, hop = 16000, 800  # 50 ms windows
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    n = len(x) // hop
    if n < 20:
        return []
    rms = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9
    db = 20 * np.log10(rms)
    smooth = np.convolve(db, np.ones(3) / 3, mode="same")
    floor = max(float(np.median(smooth)) + 10, -18.0)
    cands = [i for i in range(10, n - 1)
             if smooth[i] >= floor and smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]]
    cands.sort(key=lambda i: smooth[i], reverse=True)
    picked = []
    for i in cands:
        t = i * hop / sr
        if all(abs(t - p) >= min_gap for p in picked):
            picked.append(t)
        if len(picked) >= max_peaks:
            break
    return sorted(round(t, 2) for t in picked)


def shake_filter(times, w=1080, h=1920):
    """Camera shake that decays over SHAKE_SECONDS at each time. Zooms 4% so edges never show."""
    if not times:
        return ""
    env = "+".join(f"between(t,{t:.2f},{t + SHAKE_SECONDS:.2f})*(1-(t-{t:.2f})/{SHAKE_SECONDS})"
                   for t in times)
    zw, zh = int(w * 1.04) // 2 * 2, int(h * 1.04) // 2 * 2
    return (f"scale={zw}:{zh},"
            f"crop={w}:{h}:x='({zw}-{w})/2+{SHAKE_PX}*sin(t*83)*({env})':"
            f"y='({zh}-{h})/2+{SHAKE_PX}*cos(t*71)*({env})'")


def face_punch_graph(punches, cam_h, w=1080, h=1920):
    """Facecam layout: on each punch, the facecam band (top cam_h px) fills the whole screen
    for FACE_HOLD s, with a small push-in, so the streamer's reaction is what you see."""
    on = "+".join(f"between(t,{max(0.0, t - 0.1):.2f},{t + FACE_HOLD:.2f})" for t in punches)
    push = "+".join(f"between(t,{max(0.0, t - 0.1):.2f},{t + FACE_HOLD:.2f})*"
                    f"(1+0.06*min(1,(t-{max(0.0, t - 0.1):.2f})/{FACE_HOLD}))" for t in punches)
    return (f"[0:v]split[base][c];"
            f"[c]crop={w}:{cam_h}:0:0,scale=-2:{h},crop={w}:{h},"
            f"scale=w='trunc({w}*max(1,{push})/2)*2':h=-2:eval=frame,crop={w}:{h},setsar=1[face];"
            f"[base][face]overlay=0:0:enable='{on}'[vp]")


