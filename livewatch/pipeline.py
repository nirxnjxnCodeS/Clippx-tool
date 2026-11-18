"""Turn a raw 16:9 stream cut into a post-ready 9:16 clip.

transcribe (faster-whisper, word timings) -> layout (facecam on top + gameplay,
or OpenShorts face tracking, or a centre crop) -> AI hook + caption (Claude via
the bridge, optional) -> burned word-by-word captions + pill hook -> deliver
(folder + gallery page + optional Telegram).
"""
import html
import json
import os
import re
import subprocess
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
FONTS = os.path.join(APP, "fonts")
FONT = os.path.join(FONTS, "Montserrat-ExtraBold.ttf")
W, H = 1080, 1920

_whisper = None


# ---------------------------------------------------------------- transcription
def transcribe(path):
    """[{'start','end','word'}] for the clip, or [] when whisper is unavailable."""
    global _whisper
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[edit] faster-whisper not installed: no captions", flush=True)
        return []
    if _whisper is None:
        _whisper = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu",
                                compute_type="int8")
    segments, _ = _whisper.transcribe(path, word_timestamps=True, vad_filter=True, beam_size=1,
                                      condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": float(w.start), "end": float(w.end), "word": text})
    return words


# ---------------------------------------------------------------- layout
def _probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "csv=p=0", path])
    w, h = out.decode().strip().split(",")[:2]
    return int(w), int(h)


def facecam_top_graph(src_w, src_h, box, max_cam=0.42):
    """Facecam band on top (from the inset box), gameplay centre-cropped below, full 9:16."""
    x, y, w, h = [int(v) for v in box]
    cam_h = min(int(round(W * h / float(w))), int(H * max_cam))
    cam_h -= cam_h % 2
    target = W / float(cam_h)
    if w / float(h) < target:          # widen/heighten the crop to the band's aspect: no stretching
        want = min(src_w, int(round(h * target)))
        x, w = int(round(x + w / 2 - want / 2)), want
    else:
        want = min(src_h, int(round(w / target)))
        y, h = int(round(y + h / 2 - want / 2)), want
    x, y = max(0, min(x, src_w - w)), max(0, min(y, src_h - h))
    w, h, x, y = w - w % 2, h - h % 2, x - x % 2, y - y % 2
    game_h = H - cam_h
    graph = (f"[0:v]split[c][g];"
             f"[c]crop={w}:{h}:{x}:{y},scale={W}:{cam_h},setsar=1[cam];"
             f"[g]scale=-2:{game_h},crop={W}:{game_h},setsar=1[game];"
             f"[cam][game]vstack[v]")
    return graph, cam_h


def reframe(raw, out, facecam=None):
    """Write a 1080x1920 version of `raw`. Returns (layout name, caption y or None).
    facecam: fixed (x, y, w, h) of the streamer's webcam in a 1920x1080 frame; skips detection
    (use when a streamer's facecam is too small for auto-detection)."""
    src_w, src_h = _probe(raw)
    box = None
    if facecam:
        sx, sy = src_w / 1920.0, src_h / 1080.0       # the box is given in 1080p coordinates
        box = (facecam[0] * sx, facecam[1] * sy, facecam[2] * sx, facecam[3] * sy)
    try:
        if box is None:
            import sys
            sys.path.insert(0, APP)
            import camera_inset  # OpenShorts: finds a webcam composited into a corner
            box = camera_inset.detect(raw)
    except Exception as e:
        box = None
        print(f"[edit] facecam detection unavailable ({type(e).__name__}); trying face tracking",
              flush=True)
    if box:
        graph, cam_h = facecam_top_graph(src_w, src_h, box)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-filter_complex", graph,
                        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "18", "-c:a", "copy", out], check=True)
        return "facecam-top", cam_h            # captions sit on the seam
    try:
        import main  # OpenShorts: scene detection + face/person tracking
        main.process_video_to_vertical(raw, out)
        return "tracked", None
    except Exception as e:
        print(f"[edit] face tracking unavailable ({type(e).__name__}); centre crop", flush=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw,
                    "-vf", f"scale=-2:{H},crop={W}:{H},setsar=1", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "18", "-c:a", "copy", out], check=True)
    return "centre", None


# ---------------------------------------------------------------- AI hook
def _load_env():
    path = os.path.join(APP, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2)


def ensure_tags(caption, channel, tags):
    """Campaign rules (e.g. Lacy): the caption must name the streamer and carry the hashtags."""
    caption = caption.strip()
    if channel.lower() not in caption.lower():
        caption = f"{channel.upper()} {caption}"
    for tag in tags.split():
        if tag.lower() not in caption.lower():
            caption += f" {tag}"
    return caption


def ai_hook(channel, words, sfx_max=0, tags=None):
    """ONE Claude call (via the bridge) for the whole edit:
    {'hook': [line1, line2], 'caption': str, 'plan': {'mood', 'sfx'}}; plain fallback on failure."""
    import audiofx
    tags = tags or f"#{channel} #twitch"
    fallback = {"hook": [], "caption": ensure_tags(f"{channel.upper()} moment 😭", channel, tags),
                "plan": {"mood": "hype", "sfx": []}}
    _load_env()
    base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
    if not base or not words:
        return fallback
    lib = audiofx.sfx_library()
    numbered = " ".join(f"[{i}]{w['word']}" for i, w in enumerate(words))[:6000]
    sound_part = ""
    if sfx_max > 0 and lib:
        sound_part = (
            f'- "mood": one of {", ".join(audiofx.MOODS)} (overall feel, picks the background music).\n'
            f'- "sfx": up to {sfx_max} items {{"word": n, "sound": name, "punch": true/false}} from these '
            f"sound effects: {', '.join(sorted(lib))}. Place each right AFTER the word it reacts to "
            "(punchline, fail, big play, shocked reaction); only where it adds something, fewer is better, "
            'never on word 0. "punch" = cut to the streamer\'s face for the single biggest reaction.\n'
            '- "typewriter": true if the hook reads like a story setup or suspense (it will type itself '
            "out letter by letter with a typing sound at the start), false for instant hype/shock hooks.\n")
    notes_path = os.path.join(HERE, "notes", f"{channel.lower()}.txt")
    notes = open(notes_path).read().strip() if os.path.exists(notes_path) else ""
    prompt = (
        (f"Facts you must respect (pronouns, names):\n{notes}\n\n" if notes else "") +
        f"Transcript of a ~40 second Twitch clip of the streamer {channel}, each word numbered [n]:\n"
        f"{numbered}\n\n"
        "Write JSON with:\n"
        '- "hook1" and "hook2": an on-screen hook in two short lines, UPPERCASE, max 24 '
        "characters each, no emojis, no hashtags. It must describe what actually happens in the "
        f"clip (no made-up events, nothing negative about {channel}) and should mention {channel.upper()} "
        "when it fits.\n"
        f'- "caption": one short social caption that names {channel.upper()}, with 1-2 emojis, '
        f"ending with {tags}. English only.\n"
        f"{sound_part}"
        "Reply with only the JSON object."
    )
    try:
        import httpx
        r = httpx.post(f"{base}/chat/completions", timeout=180, json={
            "model": os.environ.get("LLM_MODEL", "sonnet"),
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        hook = [str(data.get(k, "")).upper()[:28] for k in ("hook1", "hook2") if data.get(k)]
        plan = audiofx.clean_plan(data, words, lib, sfx_max) if sfx_max > 0 else fallback["plan"]
        caption = ensure_tags(str(data.get("caption") or fallback["caption"]), channel, tags)
        return {"hook": hook, "caption": caption, "plan": plan}
    except Exception as e:
        hint = (" Start the Claude bridge on the Mac: python3 examples/claude-code-bridge/bridge.py"
                if "refused" in str(e).lower() or "ConnectError" in type(e).__name__ else "")
        print(f"[edit] AI hook skipped ({type(e).__name__}: {e}).{hint}", flush=True)
        return fallback


# ---------------------------------------------------------------- styling
TYPE_CPS = 22          # typewriter speed, characters per second
TYPE_FPS = 30


def _hook_image(lines, shown=None, size=56):
    """Pill hook on a canvas sized for the FULL text; `shown` = how many characters to draw
    (typewriter frames), None = all. Same canvas size for every frame, so frames line up."""
    font = ImageFont.truetype(FONT, size)
    pad_x, pad_y, gap, radius = 34, 18, 10, 22
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    _, t_ref, _, b_ref = probe.textbbox((0, 0), "HAWQ", font=font)   # fixed line height
    line_h = b_ref - t_ref
    widths = [probe.textbbox((0, 0), text, font=font)[2] for text in lines]
    width = max(widths) + 2 * pad_x + 40
    height = len(lines) * (line_h + 2 * pad_y) + gap * (len(lines) - 1) + 40
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    left = len("".join(lines)) if shown is None else shown
    y = 20
    for text in lines:
        part, left = text[:max(0, left)], left - len(text)
        if part.strip():
            l, _, r, _ = d.textbbox((0, 0), part, font=font)
            bw, bh = (r - l) + 2 * pad_x, line_h + 2 * pad_y
            x = (width - bw) // 2
            d.rounded_rectangle((x + 5, y + 6, x + bw + 5, y + bh + 6), radius, fill=(0, 0, 0, 110))
            d.rounded_rectangle((x, y, x + bw, y + bh), radius, fill=(255, 255, 255, 255))
            d.text((x + pad_x - l, y + pad_y - t_ref), part, font=font, fill=(10, 10, 10, 255))
        y += line_h + 2 * pad_y + gap
    return img.rotate(2, resample=Image.BICUBIC, expand=True)


def cover_hook_png(old, new, path, size=56):
    """A new hook drawn over an already-burned one: every pill is at least as wide as the
    old pill it sits on, on the same canvas, so nothing of the old text shows through."""
    font = ImageFont.truetype(FONT, size)
    pad_x, pad_y, gap, radius = 34, 18, 10, 22
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    _, t_ref, _, b_ref = probe.textbbox((0, 0), "HAWQ", font=font)
    line_h = b_ref - t_ref
    tw = lambda s: probe.textbbox((0, 0), s, font=font)[2]
    rows = max(len(old), len(new))
    old = list(old) + [""] * (rows - len(old))
    new = list(new) + [""] * (rows - len(new))
    width = max(tw(s) for s in old + new if s) + 2 * pad_x + 40
    height = rows * (line_h + 2 * pad_y) + gap * (rows - 1) + 40
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 20
    for o, n in zip(old, new):
        w = max(tw(o) if o.strip() else 0, tw(n) if n.strip() else 0)
        if w:
            bw, bh = w + 2 * pad_x, line_h + 2 * pad_y
            x = (width - bw) // 2
            d.rounded_rectangle((x + 5, y + 6, x + bw + 5, y + bh + 6), radius, fill=(0, 0, 0, 110))
            d.rounded_rectangle((x, y, x + bw, y + bh), radius, fill=(255, 255, 255, 255))
            if n.strip():
                left = d.textbbox((0, 0), n, font=font)[0]
                d.text((x + (bw - tw(n)) // 2 - left, y + pad_y - t_ref), n, font=font, fill=(10, 10, 10, 255))
        y += line_h + 2 * pad_y + gap
    img.rotate(2, resample=Image.BICUBIC, expand=True).save(path)


def replace_hook(clip, new_hook, out=None, start=None):
    """Re-hook a finished clip (fix a wrong/weak hook), optionally trimming from `start` s."""
    meta = json.load(open(clip[:-4] + ".json"))
    seam = meta.get("cam_h")
    out = out or clip[:-4] + "_edit.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "h.png")
        cover_hook_png(meta.get("hook", []), new_hook, png)
        y = f"{seam}-h/2" if seam else "250"
        cmd = ["ffmpeg", "-v", "error", "-y"]
        if start:
            cmd += ["-ss", str(start)]
        cmd += ["-i", clip, "-i", png, "-filter_complex", f"[0:v][1:v]overlay=(W-w)/2:{y},format=yuv420p[v]",
                "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
        subprocess.run(cmd, check=True)
    return out


def hook_png(lines, path, size=56):
    _hook_image(lines, None, size).save(path)


def typing_seconds(lines):
    return len("".join(lines)) / TYPE_CPS


def hook_typewriter_frames(lines, folder):
    """PNG frames (TYPE_FPS) of the hook typing itself out; the last frame is the full hook."""
    total = len("".join(lines))
    n = int(total / TYPE_CPS * TYPE_FPS) + 2
    for i in range(n):
        shown = min(total, int(i / TYPE_FPS * TYPE_CPS) + 1)
        _hook_image(lines, shown).save(os.path.join(folder, f"h_{i:04d}.png"))
    return os.path.join(folder, "h_%04d.png")


