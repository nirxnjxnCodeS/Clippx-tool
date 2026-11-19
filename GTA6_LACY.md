# Clippx: Lacy GTA 6 branch

This branch is set up for one job: clipping **Lacy's GTA 6 streams on Twitch** while he
is live. The general product (YouTube + Twitch, any channel) is on `main`.

## Run it

```bash
docker compose up -d backend
python3 examples/claude-code-bridge/bridge.py     # on the host, for AI hooks (optional)
./livewatch/lacy.sh
```

`lacy.sh` runs `livewatch/watch.py lacy` with:

| flag | why |
|---|---|
| `--auto` | waits for Lacy to go live and starts again after he goes offline |
| `--facecam 60,0,400,278` | his webcam box in a 1080p frame: it is too small for auto-detection, so it is fixed, and his face always sits on top of the clip |
| `--tags "#LACY #GTA6"` | every caption names LACY and carries the campaign tags |

The hook writer reads `livewatch/notes/lacy.txt` for facts it must respect: Lacy's
pronouns, the GTA 6 protagonists (Lucia, Jason), and the friends who may be on stream.

Clips appear in `livewatch/clips/lacy/` and on the gallery page `livewatch/clips/index.html`.
Each clip comes with a copy-ready caption. Set up Telegram (see `livewatch/README.md`)
to get every clip on your phone as it is made.

## Tuning during a stream

- Too many clips: `./livewatch/lacy.sh --threshold 70` or `--per-hour 15`.
- Too few in slow story missions: `--min-per-hour 8` lowers the bar in quiet stretches.
- Louder edits: `--sfx-max 4`. Cleaner ones: `--sfx-max 0 --no-shake`.
- Fix a wrong hook on a finished clip:
  `python -c "import sys; sys.path.insert(0,'livewatch'); import pipeline; pipeline.replace_hook('livewatch/clips/lacy/<clip>.mp4', ['NEW LINE 1', 'NEW LINE 2'])"`
