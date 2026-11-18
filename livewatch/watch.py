#!/usr/bin/env python3
"""livewatch: a self-hosted Klypra. Watches a live Twitch channel, clips the moments
chat reacts to, and edits them into post-ready 9:16 clips.

Run inside the OpenShorts container (it has ffmpeg, yt-dlp, whisper and the
face tracking):

    docker exec -it -w /app openshorts-backend python livewatch/watch.py lacy
    docker exec -it -w /app openshorts-backend python livewatch/watch.py lacy --threshold 70 --auto

Clips land in livewatch/clips/<channel>/ with a gallery at livewatch/clips/index.html.
Optional: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env sends each clip to your phone.
Test the editor on a local video without a stream:
    python livewatch/watch.py --test-file viqueens/trailer.mp4 --test-channel lacy
"""
import argparse
import os
import queue
import shutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chat as chatmod  # noqa: E402
import pipeline  # noqa: E402
import recorder  # noqa: E402

CLIPS = os.path.join(HERE, "clips")


def editor_worker(jobs, args, counters):
    while True:
        job = jobs.get()
        if job is None:
            return
        raw, score, when = job
        try:
            pipeline.make_clip(raw, args.channel, score, CLIPS, stream_time=when,
                               hook_ai=not args.no_hook, sfx_max=args.sfx_max,
                               music=not args.no_music, music_db=args.music_db,
                               shake=not args.no_shake, tags=args.tags, facecam=args.facecam)
            counters["made"] += 1
        except Exception as e:
            print(f"[edit] failed: {type(e).__name__}: {e}", flush=True)
        finally:
            counters["pending"] -= 1
            try:
                os.remove(raw)
            except OSError:
                pass


def drop_ads(raw, min_len=15):
    """Twitch's "commercial break in progress" screen: drop the clip if it is mostly ad,
    trim it to the clean part if only some of it is. Returns False when nothing usable is left."""
    import subprocess
    try:
        import adcheck
        flags = adcheck.ad_seconds(raw)
    except Exception as e:
        print(f"[clip] ad check unavailable ({type(e).__name__}); keeping clip", flush=True)
        return True
    if not any(flags):
        return True
    span = adcheck.clean_span(flags, min_len)
    if span is None:
        print(f"[clip] commercial break ({sum(flags)}/{len(flags)}s ad); dropped", flush=True)
        try:
            os.remove(raw)
        except OSError:
            pass
        return False
    s, e = span
    trimmed = raw[:-4] + "_t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(s), "-to", str(e), "-i", raw,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", trimmed],
                   check=True)
    os.replace(trimmed, raw)
    print(f"[clip] trimmed out {sum(flags)}s of commercial break (kept {s}-{e}s)", flush=True)
    return True


def cutter(seg_dir, t0, t1, score, jobs, work_dir, counters):
    # wait until the "after" part has been recorded (plus one segment of slack)
    time.sleep(max(0.0, t1 - time.time()) + recorder.SEGMENT + 2)
    raw = os.path.join(work_dir, f"raw_{int(t0)}.mp4")
    try:
        ok = recorder.cut(seg_dir, t0, t1, raw)
    except Exception as e:
        print(f"[clip] cut failed: {e}", flush=True)
        ok = False
    if ok:
        ok = drop_ads(raw)
    if not ok:
        print("[clip] skipped (nothing recorded, or Twitch commercial break)", flush=True)
    elif jobs.qsize() >= 8:
        print("[clip] editor is behind; skipping this one", flush=True)
        os.remove(raw)
    else:
        jobs.put((raw, score, time.strftime("%H:%M:%S", time.localtime(t0))))
        return                      # the editor decrements "pending" when it is done
    counters["pending"] -= 1


def s_ready(reader, now):
    return reader.started_at is not None and now - reader.started_at >= chatmod.WARMUP


def wait_until_live(channel, poll=60):
    print(f"[auto] waiting for {channel} to go live (checking every {poll}s)...", flush=True)
    while not recorder.stream_url(channel):
        time.sleep(poll)
    print(f"[auto] {channel} is live", flush=True)


