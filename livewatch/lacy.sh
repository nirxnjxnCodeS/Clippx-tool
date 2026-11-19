#!/usr/bin/env bash
# Clip Lacy's GTA 6 streams: waits for him to go live, facecam on top, campaign tags.
# Extra flags pass through, e.g.  ./livewatch/lacy.sh --threshold 70 --sfx-max 3
set -euo pipefail
exec docker exec -it -w /app openshorts-backend python livewatch/watch.py lacy \
  --auto \
  --facecam 60,0,400,278 \
  --tags "#LACY #GTA6" \
  "$@"
