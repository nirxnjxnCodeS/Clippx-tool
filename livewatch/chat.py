"""Live Twitch chat reader + highlight detector (the "clip meter").

Reads a channel's public chat anonymously over Twitch IRC (no login needed) and
scores each moment by how fast chat is moving compared with that channel's own
normal pace, plus how repetitive it is (everyone spamming the same emote /
copypasta). A score at or above the threshold is a highlight.
"""
import bisect
import collections
import math
import random
import socket
import ssl
import statistics
import threading
import time

IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6697

WINDOW = 10.0          # seconds of chat that make up "now"
BASELINE = 300.0       # seconds of history that define "normal" for this channel
WARMUP = 100.0         # seconds of chat needed before anything can be flagged
MIN_MSGS = 10          # a spike needs at least this many messages in the window
FLOOR_RATE = 0.2       # msg/s: keeps a near-silent baseline from exploding the ratio


class ChatReader(threading.Thread):
    """Keeps (timestamp, user, text) for every chat message, reconnecting on errors."""

    def __init__(self, channel, keep=BASELINE + 60):
        super().__init__(daemon=True)
        self.channel = channel.lower()
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
            except Exception as e:  # network blips: reconnect
                self.connected = False
                print(f"[chat] disconnected ({e}); reconnecting in 5s", flush=True)
                self.stop_event.wait(5)

    def _session(self):
        raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=30)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=IRC_HOST)
        nick = f"justinfan{random.randint(10000, 99999)}"  # Twitch's anonymous read-only login
        sock.sendall(f"NICK {nick}\r\nJOIN #{self.channel}\r\n".encode())
        self.connected = True
        if self.started_at is None:
            self.started_at = time.time()
        buf = b""
        while not self.stop_event.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                sock.sendall(b"PING :keepalive\r\n")
                continue
            if not data:
                raise ConnectionError("server closed the connection")
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                self._handle(sock, line.decode("utf-8", "replace"))
        sock.close()

    def _handle(self, sock, line):
        if line.startswith("PING"):
            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        # :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
        if " PRIVMSG #" not in line or not line.startswith(":"):
            return
        prefix, _, rest = line[1:].partition(" PRIVMSG ")
        user = prefix.split("!", 1)[0]
        text = rest.partition(" :")[2]
        self.add(time.time(), user, text)

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


def score_at(messages, now, started_at, window=WINDOW, baseline=BASELINE):
    """Return a dict describing the chat at time `now` (score 0-100, rates, readiness)."""
    times = [m[0] for m in messages]          # appended in arrival order, so sorted

    def count(a, b):                          # messages with a < ts <= b, in O(log n)
        return bisect.bisect_right(times, b) - bisect.bisect_right(times, a)

    lo = bisect.bisect_right(times, now - window)
    recent = messages[lo:bisect.bisect_right(times, now)]
    rate_now = len(recent) / window
    ready = started_at is not None and now - started_at >= WARMUP
    # Normal pace: median of the window-sized rates over the baseline period,
    # excluding the current window so a spike does not raise its own bar.
    hist_start = max(now - baseline, started_at or now)
    rates = []
    t = now - window
    while t - window >= hist_start:
        rates.append(count(t - window, t) / window)
        t -= window / 2
    normal = statistics.median(rates) if rates else 0.0
    # After a chat outage (disconnect, stuck connection) the history is mostly empty and any
    # chat at all looks like a giant spike. Only trust the baseline when most of the history
    # windows actually had messages in them.
    filled = sum(1 for r in rates if r > 0) / len(rates) if rates else 0.0
    ratio = rate_now / max(normal, FLOOR_RATE)
    texts = [m[2].strip().lower() for m in recent]
    repetition = (1 - len(set(texts)) / len(texts)) if texts else 0.0
    score = 0.0
    if ratio > 1:
        score = 60 * math.log2(ratio)
    score += 15 * repetition
    score = max(0.0, min(100.0, score))
    return {
        "score": round(score),
        "rate": rate_now,
        "normal": normal,
        "msgs": len(recent),
        "repetition": repetition,
        "ready": ready and len(rates) >= 3 and filled >= 0.7,
    }


class Detector:
    """Turns chat scores into highlight events: threshold, minimum volume, cooldown, peak."""

    def __init__(self, threshold=60, cooldown=40.0, min_msgs=MIN_MSGS):
        self.threshold = threshold
        self.cooldown = cooldown
        self.min_msgs = min_msgs
        self.last_trigger = 0.0
        self.active = None  # (start_time, best_score) while a spike is in progress

    def update(self, now, s):
        """Feed one score dict; returns (spike_start_time, peak_score) when a spike ends."""
        hot = s["ready"] and s["msgs"] >= self.min_msgs and s["score"] >= self.threshold
        if self.active is None:
            if hot and now - self.last_trigger >= self.cooldown:
                self.active = (now, s["score"])
            return None
        start, best = self.active
        best = max(best, s["score"])
        # A spike ends when chat calms down or after 8 s, whichever comes first.
        if not hot or now - start >= 8:
            self.active = None
            self.last_trigger = now
            return start, best
        self.active = (start, best)
        return None
