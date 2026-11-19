"""YouTube live chat reader with the same interface as chat.ChatReader.

Reads a live stream's public chat the way the YouTube web player does (no API key, no
login): load the live_chat page once for the client config and the first continuation
token, then poll get_live_chat with each new token. Uses the "Live chat" (all messages)
view, not "Top chat", so the message rate is the real one.
"""
import collections
import json
import re
import subprocess
import threading
import time
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(page_url):
    """Video id of the live stream behind a channel /live URL or a watch URL, or None."""
    m = re.search(r"(?:v=|youtu\.be/|/live/)([A-Za-z0-9_-]{11})", page_url)
    if m:
        return m.group(1)
    proc = subprocess.run(["yt-dlp", "--no-warnings", "--no-playlist", "--print", "id", page_url],
                          capture_output=True, text=True, timeout=60)
    vid = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    return vid if VIDEO_ID_RE.match(vid) else None


def _fetch(url, body=None):
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _object_after(text, marker):
    i = text.find(marker)
    if i < 0:
        return None
    i = text.find("{", i + len(marker))
    return json.JSONDecoder().raw_decode(text[i:])[0]


def _first_continuation(conts):
    for c in conts or []:
        for inner in c.values():
            if isinstance(inner, dict) and inner.get("continuation"):
                return inner["continuation"], inner.get("timeoutMs", 2000) / 1000
    return None, 2.0


def _message_text(renderer):
    out = []
    for run in renderer.get("message", {}).get("runs", []):
        if "text" in run:
            out.append(run["text"])
        elif "emoji" in run:
            shortcuts = run["emoji"].get("shortcuts") or [run["emoji"].get("emojiId", "")]
            out.append(shortcuts[0])
    return "".join(out)


class YouTubeChatReader(threading.Thread):
    """Keeps (timestamp, user, text) for every live chat message, reconnecting on errors."""

    def __init__(self, page_url, keep=360.0):
        super().__init__(daemon=True)
        self.page_url = page_url
        self.keep = keep
        self.messages = collections.deque()
        self.lock = threading.Lock()
        self.started_at = None
        self.stop_event = threading.Event()
        self.connected = False

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._session()
            except Exception as e:  # network blips, stream restarted under a new id: start over
                self.connected = False
                print(f"[chat] YouTube chat disconnected ({type(e).__name__}: {e}); retrying in 10s",
                      flush=True)
                self.stop_event.wait(10)

    def _session(self):
        vid = video_id(self.page_url)
        if not vid:
            raise ConnectionError("no live stream found")
        html = _fetch(f"https://www.youtube.com/live_chat?is_popout=1&v={vid}")
        key = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
        context = _object_after(html, '"INNERTUBE_CONTEXT"')
        data = _object_after(html, "ytInitialData")
        if not (key and context and data):
            raise ConnectionError("live chat page had no chat (chat off, or stream not live)")
        renderer = data.get("contents", {}).get("liveChatRenderer", {})
        cont = None
        try:   # "Live chat" (every message) is the last entry of the view selector
            items = (renderer["header"]["liveChatHeaderRenderer"]["viewSelector"]
                     ["sortFilterSubMenuRenderer"]["subMenuItems"])
            cont = items[-1]["continuation"]["reloadContinuationData"]["continuation"]
        except (KeyError, IndexError, TypeError):
            pass
        if not cont:
            cont, _ = _first_continuation(renderer.get("continuations"))
        if not cont:
            raise ConnectionError("no chat continuation (stream ended?)")
        api = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={key.group(1)}&prettyPrint=false"
        first = True
        while not self.stop_event.is_set():
            resp = json.loads(_fetch(api, {"context": context, "continuation": cont}))
            live = resp.get("continuationContents", {}).get("liveChatContinuation")
            if not live:
                raise ConnectionError("chat ended")
            now = time.time()
            if not first:   # the first answer replays recent history; only count new messages
                for action in live.get("actions", []):
                    item = action.get("addChatItemAction", {}).get("item", {})
                    r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                    if r:
                        self.add(now, r.get("authorName", {}).get("simpleText", ""), _message_text(r))
            first = False
            self.connected = True
            if self.started_at is None:
                self.started_at = now
            cont, wait = _first_continuation(live.get("continuations"))
            if not cont:
                raise ConnectionError("chat ended")
            self.stop_event.wait(min(max(wait, 1.0), 5.0))

    def add(self, ts, user, text):
        with self.lock:
            self.messages.append((ts, user, text))
            cutoff = ts - self.keep
            while self.messages and self.messages[0][0] < cutoff:
                self.messages.popleft()

    def snapshot(self):
        with self.lock:
            return list(self.messages)

    def stop(self):
        self.stop_event.set()
