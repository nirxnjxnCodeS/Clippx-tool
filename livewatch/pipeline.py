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


