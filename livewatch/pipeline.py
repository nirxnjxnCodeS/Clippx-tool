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


