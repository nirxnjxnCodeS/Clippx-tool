#!/usr/bin/env python3
"""livewatch: watches a live Twitch or YouTube stream, clips the moments chat reacts
to, and edits them into post-ready 9:16 clips.

Run inside the OpenShorts container (it has ffmpeg, yt-dlp, whisper and the
face tracking):

    docker exec -it -w /app openshorts-backend python livewatch/watch.py lacy
    docker exec -it -w /app openshorts-backend python livewatch/watch.py lacy --threshold 70 --auto
    docker exec -it -w /app openshorts-backend python livewatch/watch.py @somestreamer --auto
    docker exec -it -w /app openshorts-backend python livewatch/watch.py "https://youtu.be/<id>"

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
import sources  # noqa: E402

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
                               shake=not args.no_shake, tags=args.tags, facecam=args.facecam,
                               platform=args.platform)
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


def cutter(seg_dir, t0, t1, score, jobs, work_dir, counters, check_ads=True):
    # wait until the "after" part has been recorded (plus one segment of slack)
    time.sleep(max(0.0, t1 - time.time()) + recorder.SEGMENT + 2)
    raw = os.path.join(work_dir, f"raw_{int(t0)}.mp4")
    try:
        ok = recorder.cut(seg_dir, t0, t1, raw)
    except Exception as e:
        print(f"[clip] cut failed: {e}", flush=True)
        ok = False
    if ok and check_ads:                          # the commercial-break screen is Twitch-only
        ok = drop_ads(raw)
    if not ok:
        print("[clip] skipped (nothing recorded, or a commercial break)", flush=True)
    elif jobs.qsize() >= 8:
        print("[clip] editor is behind; skipping this one", flush=True)
        os.remove(raw)
    else:
        jobs.put((raw, score, time.strftime("%H:%M:%S", time.localtime(t0))))
        return                      # the editor decrements "pending" when it is done
    counters["pending"] -= 1


def s_ready(reader, now):
    return reader.started_at is not None and now - reader.started_at >= chatmod.WARMUP


def wait_until_live(source, poll=60):
    print(f"[auto] waiting for {source.name} ({source.label}) to go live (checking every {poll}s)...",
          flush=True)
    while not recorder.stream_url(source):
        time.sleep(poll)
    print(f"[auto] {source.name} is live", flush=True)


def watch(args):
    work = os.path.join(HERE, "work", args.channel)
    seg_dir = os.path.join(work, "segments")
    shutil.rmtree(seg_dir, ignore_errors=True)       # old rolling recording only
    os.makedirs(seg_dir, exist_ok=True)
    import glob as _glob
    leftovers = sorted(_glob.glob(os.path.join(work, "raw_*.mp4")))  # cut but not edited last run

    rec = recorder.Recorder(args.source, seg_dir, buffer_seconds=args.before + args.after + 240,
                            use_streamlink=not args.no_adblock)
    reader = chatmod.reader_for(args.source)
    det = chatmod.Detector(threshold=args.threshold, cooldown=args.cooldown, min_msgs=args.min_msgs)
    jobs = queue.Queue()
    counters = {"made": 0, "detected": 0, "pending": 0}
    for raw in leftovers:
        try:
            when = time.strftime("%H:%M:%S", time.localtime(int(os.path.basename(raw)[4:-4])))
        except ValueError:
            when = ""
        counters["pending"] += 1
        jobs.put((raw, 0, when))
    if leftovers:
        print(f"[watch] editing {len(leftovers)} clip(s) left over from the last run first", flush=True)
    threading.Thread(target=editor_worker, args=(jobs, args, counters), daemon=True).start()
    rec.start()
    reader.start()

    started = time.time()
    last_status = 0.0
    clip_times = []
    base_threshold = args.threshold
    last_adjust = started
    print(f"[watch] {args.channel} ({args.source.label}): threshold {args.threshold}, clip = {args.before}s before + "
          f"{args.after}s after, max {args.max_clips} clips, {args.hours}h. Ctrl+C to stop.",
          flush=True)
    try:
        while True:
            now = time.time()
            if now - started > args.hours * 3600:
                print("[watch] monitoring time limit reached", flush=True)
                break
            if counters["detected"] >= args.max_clips:
                print("[watch] clip limit reached", flush=True)
                break
            if rec.offline_since and now - rec.offline_since > 180:
                print("[watch] stream looks offline for 3 min", flush=True)
                break
            # keep the rate between --min-per-hour and --per-hour: if no clip for a while, lower the
            # bar 5 points every 6 min (floor 45); after each clip, go back to the chosen threshold.
            if args.min_per_hour and now - last_adjust >= 360 and s_ready(reader, now):
                gap = 3600 / args.min_per_hour
                last_clip = clip_times[-1] if clip_times else started
                if now - last_clip > gap and det.threshold > 45:
                    det.threshold = max(45, det.threshold - 5)
                    print(f"[watch] quiet stretch: sensitivity bar lowered to {det.threshold}", flush=True)
                last_adjust = now
            s = chatmod.score_at(reader.snapshot(), now, reader.started_at)
            event = det.update(now, s)
            if event and args.per_hour:
                recent = [t for t in clip_times if now - t < 3600]
                if len(recent) >= args.per_hour:
                    wait_min = int((3600 - (now - recent[0])) / 60) + 1
                    print(f"[clip] spike (score {event[1]}) skipped: {args.per_hour}/hour reached, "
                          f"next slot in ~{wait_min} min", flush=True)
                    event = None
            if event:
                spike_start, peak = event
                clip_times.append(now)
                det.threshold = base_threshold
                counters["detected"] += 1
                counters["pending"] += 1
                t0, t1 = spike_start - args.before, spike_start + args.after
                print(f"\n[clip] 🔥 highlight #{counters['detected']}: score {peak} "
                      f"({s['rate']:.1f} msg/s vs normal {s['normal']:.1f}); cutting...", flush=True)
                threading.Thread(target=cutter, args=(seg_dir, t0, t1, peak, jobs, work, counters,
                                                      args.platform == "twitch"),
                                 daemon=True).start()
            if now - last_status >= 5:
                last_status = now
                state = "warming up" if not s["ready"] else f"score {s['score']:>3}/{det.threshold}"
                if not rec.recording():
                    rec_state = "rec …"
                elif args.platform != "twitch":
                    rec_state = "rec ●"
                else:
                    rec_state = "rec ● ads✂" if rec.ads_filtered else "rec ● ADS ON"
                chat_state = "chat ●" if reader.connected else "chat …"
                print(f"[meter] {rec_state} {chat_state} | {s['rate']:.1f} msg/s · normal "
                      f"{s['normal']:.1f} · {state} · clips {counters['made']}/{counters['detected']}",
                      flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[watch] stopping...", flush=True)
    finally:
        # keep recording until the last highlight's "after" seconds are in, then stop
        deadline = time.time() + args.after + 2 * recorder.SEGMENT + 5
        while counters["pending"] > 0 and time.time() < deadline:
            time.sleep(1)
        rec.stop()
        reader.stop()
        if counters["pending"] > 0:
            print(f"[watch] finishing {counters['pending']} clip(s) still being edited...", flush=True)
        give_up = time.time() + 900
        while counters["pending"] > 0 and time.time() < give_up:
            time.sleep(2)
        jobs.put(None)
        time.sleep(1)
        shutil.rmtree(seg_dir, ignore_errors=True)   # the rolling recording; clips + recorder.log stay
        print(f"[watch] done: {counters['made']} clips in livewatch/clips/{args.channel}/ — open "
              "livewatch/clips/index.html", flush=True)


def test_file(args):
    """Edit one stretch of a local video as if it were a highlight (no Twitch needed)."""
    import subprocess
    work = os.path.join(HERE, "work", "test")
    os.makedirs(work, exist_ok=True)
    raw = os.path.join(work, "raw_test.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(args.test_start), "-t",
                    str(args.before + args.after), "-i", args.test_file, "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "18", "-c:a", "aac", raw], check=True)
    pipeline.make_clip(raw, args.channel, 99, CLIPS, hook_ai=not args.no_hook, sfx_max=args.sfx_max,
                       music=not args.no_music, music_db=args.music_db, shake=not args.no_shake,
                       tags=args.tags, facecam=args.facecam, platform=args.platform)


def main():
    p = argparse.ArgumentParser(description="Self-hosted live clipper for Twitch and YouTube.")
    p.add_argument("target", nargs="?",
                   help="Twitch channel (lacy, twitch.tv/lacy) or YouTube live (@handle, watch/live URL)")
    p.add_argument("--name", default=None,
                   help="name for the clips folder, notes file and captions (default: from the target)")
    p.add_argument("--threshold", type=int, default=60,
                   help="clip sensitivity 40-90 (lower = more clips). Default 60 = about 2x normal chat")
    p.add_argument("--before", type=int, default=30, help="seconds kept before the chat spike")
    p.add_argument("--after", type=int, default=10, help="seconds kept after the chat spike")
    p.add_argument("--cooldown", type=int, default=45, help="minimum seconds between clips")
    p.add_argument("--min-msgs", type=int, default=chatmod.MIN_MSGS,
                   help="messages needed in 10 s for a spike (lower it for small/quiet chats)")
    p.add_argument("--max-clips", type=int, default=40, help="stop after this many clips")
    p.add_argument("--per-hour", type=int, default=30, help="at most this many clips per hour (0 = no cap)")
    p.add_argument("--min-per-hour", type=int, default=5,
                   help="aim for at least this many per hour: lowers the bar in quiet stretches (0 = off)")
    p.add_argument("--hours", type=float, default=8, help="stop after this many hours")
    p.add_argument("--auto", action="store_true", help="wait for the channel to go live, and "
                                                        "start again after it goes offline")
    p.add_argument("--no-hook", action="store_true", help="skip the Claude hook/caption/sound plan")
    p.add_argument("--sfx-max", type=int, default=2,
                   help="max sound effects per clip (0 = none, 2 = subtle, 4 = heavy)")
    p.add_argument("--no-music", action="store_true", help="no background music")
    p.add_argument("--no-shake", action="store_true", help="no camera shake on loud moments")
    p.add_argument("--facecam", default=None,
                   help="streamer's webcam box in a 1920x1080 frame as x,y,w,h (e.g. Lacy: 60,0,400,278); "
                        "puts the face on top of every clip and skips slow auto-detection")
    p.add_argument("--no-adblock", action="store_true",
                   help="record directly (no streamlink ad skipping); use if recording keeps failing")
    p.add_argument("--tags", default=None,
                   help='hashtags every caption must carry, e.g. "#LACY #GTA6" '
                        '(default "#<name> #twitch" or "#<name> #shorts")')
    p.add_argument("--music-db", type=float, default=-20.0,
                   help="background music level in dB (-24 quieter, -16 louder)")
    p.add_argument("--test-file", help="edit a local video instead of watching Twitch")
    p.add_argument("--test-start", type=float, default=0)
    p.add_argument("--test-channel", default="test")
    args = p.parse_args()
    if args.facecam:
        try:
            args.facecam = tuple(int(v) for v in args.facecam.split(","))
            assert len(args.facecam) == 4 and args.facecam[2] > 0 and args.facecam[3] > 0
        except (ValueError, AssertionError):
            p.error("--facecam must be x,y,w,h, e.g. 60,0,400,278")

    if args.test_file:
        args.channel = sources.safe_name(args.name or args.test_channel)
        args.platform = "twitch"
        return test_file(args)
    try:
        args.source = sources.parse(args.target)
    except ValueError as e:
        p.error(str(e))
    args.platform = args.source.platform
    args.channel = sources.safe_name(args.name) if args.name else args.source.name
    args.threshold = max(40, min(90, args.threshold))
    while True:
        if args.auto:
            wait_until_live(args.source)
        elif not recorder.stream_url(args.source):
            print(f"{args.channel} is offline. Use --auto to wait for them to go live.")
            return
        watch(args)
        if not args.auto:
            return


if __name__ == "__main__":
    main()
