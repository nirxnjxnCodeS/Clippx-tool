# livewatch

Watches a live Twitch or YouTube stream, clips the moments chat reacts to, and edits each one into a
post-ready 9:16 short. It needs no clipping service and runs on your own machine.

```
chat.py       anonymous chat reader + spike detector (the "clip meter")
recorder.py   rolling 2 s segments of the stream, cut by wall-clock time
sources.py    Twitch channel / YouTube live URL -> what to record and read
adcheck.py    drops / trims Twitch "commercial break" screens
pipeline.py   transcribe -> 9:16 layout (facecam on top, face tracking, or centre crop)
              -> AI hook + caption -> captions + pill hook -> gallery / Telegram
audiofx.py    background music, timed sound effects, face punches, shakes on loud peaks
watch.py      ties it together (CLI)
```

## How a clip is made

1. The chat reader keeps the last ~6 minutes of messages. Every second the
   detector compares the last 10 s with the channel's own normal pace. Twice the
   normal rate is a score of 60, and repeated emotes or copypasta add up to 15 points.
2. When the score crosses the threshold, the watcher waits for the "after" seconds
   to be recorded. It then cuts `--before` + `--after` seconds out of the rolling buffer.
3. The editor transcribes the cut and lays it out in 9:16. A single LLM call writes the hook, the caption
   and the sound plan. Then it burns everything in and writes the clip plus a `.json`
   next to it.

## Running

The editor needs the OpenShorts container: it has ffmpeg, yt-dlp, faster-whisper and the face tracking.

```bash
docker compose up -d backend
docker exec -it -w /app openshorts-backend python livewatch/watch.py <target> --auto
```

`<target>` can be any of:

| platform | examples |
|---|---|
| Twitch | `lacy`, `twitch.tv/lacy`, `https://www.twitch.tv/lacy` |
| YouTube | `@handle`, `youtube.com/@handle/live`, `https://youtu.be/<id>`, `youtube.com/live/<id>` |

On YouTube, chat is read the way the web player reads it (`ytchat.py`), using the
"Live chat" view, which shows every message. It needs no API key and no login. Use `--name`
to choose the clips folder and the caption name, which otherwise come from the handle or
video id. The older form `watch.py dylan --youtube <url>` also works.

Check a YouTube live before clipping it:

```bash
docker exec -it -w /app openshorts-backend python livewatch/yttest.py https://www.youtube.com/@handle/live
```

It saves one frame to `livewatch/work/yt_frame.jpg`, where you can measure the facecam
box for `--facecam`. It then counts chat messages for 40 s.

Clips land in `livewatch/clips/<channel>/`, and the gallery is at `livewatch/clips/index.html`.

Optional:

- **AI hooks/captions/sound plan**: set `LLM_BASE_URL` (any OpenAI-compatible server,
  e.g. the Claude Code bridge in `examples/claude-code-bridge/`) and `LLM_MODEL` in `.env`.
- **Telegram delivery**: set `TELEGRAM_BOT_TOKEN` in `.env`, then message your bot and run
  `python livewatch/telegram_setup.py` once.
- **Music and sound effects**: put your own licensed files in `Music for bg/` (with a
  `moods.json` mapping file name to moods) and `sfx for shorts/`. You can also point
  `LW_MUSIC_DIR` / `LW_SFX_DIR` elsewhere. Neither folder is in the repo.
- **Channel notes**: `livewatch/notes/<channel>.txt` holds facts the hook writer must
  respect (pronouns, names).

Test the editor on a local file without a stream:

```bash
python livewatch/watch.py --test-file some.mp4 --test-start 120 --test-channel demo
```

Useful flags: `--threshold` (40-90; lower gives more clips), `--per-hour` / `--min-per-hour`,
`--facecam x,y,w,h` (a fixed webcam box in 1080p coordinates; skips detection),
`--tags`, `--sfx-max`, `--no-music`, `--no-shake`, `--no-hook`.
