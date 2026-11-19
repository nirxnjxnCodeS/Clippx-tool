"""Rolling recording of a live stream (Twitch or YouTube), cut into short wall-clock-named segments.

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

import sources

CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
SEGMENT = 2  # seconds


def valid_channel(name):
    return bool(CHANNEL_RE.match(name.lower()))


def as_source(target):
    """A Source; a bare string is parsed (a plain name is a Twitch channel)."""
    return target if isinstance(target, sources.Source) else sources.parse(target)


def stream_url(target, max_height=1080):
    """HLS URL of a live stream, or None when it is offline."""
    src = as_source(target)
    cmd = ["yt-dlp", "-g", "--no-warnings", "-f", f"best[height<={max_height}]/best"]
    if src.platform == "youtube":
        cmd += ["--no-playlist", "--match-filter", "is_live"]   # only an ongoing live, not a VOD
    proc = subprocess.run(cmd + [src.url], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip().splitlines()
    return url[0] if url else None


class Recorder(threading.Thread):
    def __init__(self, target, seg_dir, buffer_seconds=300, use_streamlink=True):
        super().__init__(daemon=True)
        self.source_info = as_source(target)
        # streamlink is only used to skip Twitch's inserted ads; YouTube is recorded directly
        self.use_streamlink = use_streamlink and self.source_info.platform == "twitch"
        self.channel = self.source_info.name
        self.seg_dir = seg_dir
        self.buffer = buffer_seconds
        self.stop_event = threading.Event()
        self.proc = None
        self.source = None
        self.ads_filtered = False
        self.offline_since = None
        os.makedirs(seg_dir, exist_ok=True)

    def run(self):
        log_path = os.path.join(os.path.dirname(self.seg_dir), "recorder.log")
        quick_fails = 0
        while not self.stop_event.is_set():
            url = stream_url(self.source_info)
            if not url:
                self.offline_since = self.offline_since or time.time()
                self.stop_event.wait(20)
                continue
            self.offline_since = None
            segment_args = ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
                            "-segment_time", str(SEGMENT), "-reset_timestamps", "1", "-strftime", "1",
                            os.path.join(self.seg_dir, "%s.ts")]
            self.source = None
            os.makedirs(self.seg_dir, exist_ok=True)   # recreate if someone cleaned up mid-run
            log = open(log_path, "a")
            log.write(f"\n--- {time.strftime('%H:%M:%S')} start ({'streamlink' if self.use_streamlink else 'direct'})\n")
            log.flush()
            started = time.time()
            if self.use_streamlink and shutil.which("streamlink"):
                # streamlink skips Twitch's inserted ad segments (built in since streamlink 6; the old
                # --twitch-disable-ads switch no longer exists), so ads never reach the recording.
                self.source = subprocess.Popen(
                    ["streamlink", "--loglevel", "warning", "--stdout",
                     self.source_info.url, "1080p60,1080p,best"],
                    stdout=subprocess.PIPE, stderr=log)
                # -re: streamlink hands over video in bursts; read it at real-time speed so each
                # 2 s segment is written in its own wall-clock second. Without it several segments
                # land in the same second and overwrite each other (%s names), losing ~2/3 of the video.
                # -v fatal: Twitch streams make the H.264 parser print harmless warnings non-stop
                self.proc = subprocess.Popen(["ffmpeg", "-v", "fatal", "-nostdin", "-re", "-i", "pipe:0",
                                              *segment_args], stdin=self.source.stdout, stderr=log)
                self.source.stdout.close()   # ffmpeg owns the pipe now
                self.ads_filtered = True
            else:
                self.proc = subprocess.Popen(["ffmpeg", "-v", "fatal", "-nostdin", "-i", url,
                                              *segment_args], stderr=log)
                self.ads_filtered = False
            while self.proc.poll() is None and not self.stop_event.is_set():
                self._prune()
                self.stop_event.wait(5)
            for p in (self.proc, self.source):
                if p and p.poll() is None:
                    p.terminate()
            log.close()
            if self.stop_event.is_set():
                break
            # Ended by itself: URL expired, stream dropped, or the ad-skipping pipe failed.
            if time.time() - started < 30:
                quick_fails += 1
                if self.use_streamlink and quick_fails >= 3:
                    self.use_streamlink = False
                    print("[rec] ad-skipping recorder keeps failing (see livewatch/work/<channel>/recorder.log);"
                          " switched to the direct recorder (ads may appear)", flush=True)
            else:
                quick_fails = 0

    def _prune(self):
        cutoff = time.time() - self.buffer
        for path in glob.glob(os.path.join(self.seg_dir, "*.ts")):
            try:
                if int(os.path.basename(path)[:-3]) < cutoff:
                    os.remove(path)
            except (ValueError, OSError):
                pass

    def recording(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        self.stop_event.set()
        for p in (self.proc, self.source):
            if p and p.poll() is None:
                p.terminate()


def segments_between(seg_dir, t0, t1):
    """(epoch, path) of the segments that overlap [t0, t1], oldest first."""
    out = []
    for path in glob.glob(os.path.join(seg_dir, "*.ts")):
        try:
            start = int(os.path.basename(path)[:-3])
        except ValueError:
            continue
        if start + SEGMENT + 1 >= t0 and start <= t1:
            out.append((start, path))
    return sorted(out)


def cut(seg_dir, t0, t1, out_path, max_height=1080, fps=30):
    """Cut wall-clock [t0, t1] out of the rolling buffer into an mp4. Returns True on success."""
    segs = segments_between(seg_dir, t0, t1)
    if not segs:
        return False
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    listing = out_path + ".txt"
    with open(listing, "w") as f:
        for _, p in segs:
            f.write(f"file '{p}'\n")
    offset = max(0.0, t0 - segs[0][0])
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
             "-ss", f"{offset:.2f}", "-t", f"{t1 - t0:.2f}",
             "-vf", f"scale=-2:'min({max_height},ih)',fps={fps}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", out_path],
            check=True, timeout=300)
    finally:
        os.remove(listing)
    if not os.path.exists(out_path):
        return False
    try:
        got = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", out_path]).decode().strip())
        if got < 0.7 * (t1 - t0):
            print(f"[clip] warning: only {got:.0f}s of {t1 - t0:.0f}s was recorded for this moment", flush=True)
    except (subprocess.CalledProcessError, ValueError):
        pass
    return True
