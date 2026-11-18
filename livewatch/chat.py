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


