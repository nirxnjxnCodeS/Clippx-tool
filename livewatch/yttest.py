"""Quick check of a YouTube live stream before clipping it:
    docker exec -it -w /app openshorts-backend python livewatch/yttest.py https://www.youtube.com/@dylan.page./live
Saves one frame (to find the facecam box) and counts live chat messages for 40 s."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import recorder  # noqa: E402
import sources  # noqa: E402
import ytchat  # noqa: E402

if len(sys.argv) != 2:
    sys.exit("usage: yttest.py <YouTube live URL or @handle>")
src = sources.parse(sys.argv[1])
if src.platform != "youtube":
    sys.exit("give a YouTube live URL or @handle")
url = recorder.stream_url(src)
print("stream:", "FOUND" if url else "NOT LIVE (or yt-dlp blocked)")
if url:
    frame = os.path.join(HERE, "work", "yt_frame.jpg")
    os.makedirs(os.path.dirname(frame), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", url, "-frames:v", "1", "-vf", "scale=1920:1080",
                    frame], timeout=90)
    print("frame:", frame if os.path.exists(frame) else "failed")
print("video id:", ytchat.video_id(src.url))
reader = ytchat.YouTubeChatReader(src.url)
reader.start()
for i in range(8):
    time.sleep(5)
    msgs = reader.snapshot()
    print(f"chat: connected={reader.connected} messages={len(msgs)}"
          + (f"  last: {msgs[-1][1]}: {msgs[-1][2][:60]}" if msgs else ""), flush=True)
reader.stop()
