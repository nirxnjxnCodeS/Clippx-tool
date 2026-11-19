# Clippx

Clippx clips live streams while they are still running. It watches a Twitch or YouTube
live stream and spots the moments chat goes wild. Each one becomes a post-ready 9:16
short with a hook, word-by-word captions, music and sound effects. Clips arrive in a
local gallery and, if you set it up, on your phone through Telegram.

It runs on your own machine. You don't need a clipping service or a cloud account.

## How it works

```
live stream ──► rolling recorder (2 s segments, last few minutes kept)
     │
live chat ───► clip meter: chat speed vs the channel's normal pace + spam/emote repetition
                    │ score crosses the threshold
                    ▼
               cut the moment (30 s before the spike, 10 s after)
                    ▼
               transcribe (faster-whisper, word timings)
                    ▼
               9:16 layout: streamer's facecam on top + gameplay below,
               or face tracking, or a centre crop
                    ▼
               one AI call: hook, caption, music mood, sound-effect placements
                    ▼
               burn captions + pill hook, mix music (ducked under speech) and effects,
               face punches and shakes on the loudest moments
                    ▼
               gallery page  ·  Telegram  ·  clip + .json with hook and caption
```

On Twitch it also skips the ads inserted into the stream and trims the "commercial
break" screen out of a clip.

## Quick start

You need Docker. The backend container provides ffmpeg, yt-dlp, faster-whisper and the
face tracking.

```bash
cp .env.example .env
docker compose up -d --build backend

# Twitch
docker exec -it -w /app openshorts-backend python livewatch/watch.py <channel> --auto

# YouTube
docker exec -it -w /app openshorts-backend python livewatch/watch.py @handle --auto
```

Before clipping a YouTube stream, `python livewatch/yttest.py <url>` checks that the
stream and chat can be read. It also saves a frame for measuring the facecam box.

`--auto` waits for the stream to go live and starts again the next time it does. Clips
are written to `livewatch/clips/<name>/`. Open `livewatch/clips/index.html` to watch them
and copy their captions.

### What you can watch

| Platform | Examples |
|---|---|
| Twitch | `lacy`, `twitch.tv/lacy`, `https://www.twitch.tv/lacy` |
| YouTube | `@handle`, `youtube.com/@handle/live`, `https://youtu.be/<id>`, `youtube.com/live/<id>` |

## Configuration (`.env`)

| Variable | What it does |
|---|---|
| `LLM_BASE_URL`, `LLM_MODEL` | Any OpenAI-compatible server writes the hooks, captions and sound plan. To use your Claude plan, run the bridge in [`examples/claude-code-bridge/`](examples/claude-code-bridge/README.md). Without one, clips get a plain caption and no hook. |
| `TELEGRAM_BOT_TOKEN` | Sends every clip to your phone. Message your bot, then run `python livewatch/telegram_setup.py` once. |
| `LW_MUSIC_DIR`, `LW_SFX_DIR` | Your own licensed music (with a `moods.json`) and sound effects. Defaults: `Music for bg/` and `sfx for shorts/`. Neither is included in the repo. |
| `WHISPER_MODEL` | faster-whisper model for captions (default `small`). |

## Useful options

| Option | Effect |
|---|---|
| `--threshold 40-90` | Sensitivity of the clip meter (default 60, about twice the normal chat pace). Lower gives more clips. |
| `--per-hour N` / `--min-per-hour N` | Cap the clip rate, or lower the bar during quiet stretches. |
| `--before` / `--after` | Seconds kept before and after the chat spike (default 30 / 10). |
| `--facecam x,y,w,h` | A fixed webcam box in 1080p coordinates, for streamers whose facecam is too small to detect. |
| `--tags "#A #B"` | Hashtags every caption must carry. |
| `--name` | Name for the clips folder, the notes file and the captions. |
| `--sfx-max N`, `--no-music`, `--no-shake`, `--no-hook` | How much editing each clip gets. |
| `--test-file video.mp4 --test-start 120` | Run the editor on a local file, without a stream. |

To keep the hook writer accurate, put facts it must respect (pronouns, names of
friends or characters) in `livewatch/notes/<name>.txt`.

More detail is in [livewatch/README.md](livewatch/README.md).

## Branches

- `main`: Clippx, for any Twitch channel or YouTube live stream.
- `lacy-twitch-stream`: the original tool, built to clip Lacy's GTA 6 streams on Twitch,
  with a one-command preset (`livewatch/lacy.sh`).

## Built on OpenShorts

Clippx is built on [OpenShorts](https://github.com/mutonby/openshorts) by the OpenShorts
authors. Clippx reuses its container, facecam detection, face tracking and fonts. The
full OpenShorts app (long videos to shorts, dashboard, MCP and more) is still in this
repository. Its documentation is in [docs/OPENSHORTS.md](docs/OPENSHORTS.md).

## License

The OpenShorts core and the Clippx code are under the [MIT License](LICENSE). See [NOTICE](NOTICE).

**Exception:** the `cloud/` directory comes from OpenShorts and is under the
[OpenShorts Commercial License](cloud/LICENSE), not MIT. It may not be offered to others
as a paid or hosted service. Clippx does not use `cloud/`.

Only clip and post streams you have the rights or permission to use.
