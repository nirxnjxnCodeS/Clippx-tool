#!/usr/bin/env python3
"""Claude Code bridge: an OpenAI-compatible /v1/chat/completions on your Mac.

Runs on the HOST (not in Docker), next to a logged-in `claude` CLI. Each request
is answered by one `claude -p` call, so it uses your own Claude plan's limits.
OpenShorts reaches it through LLM_BASE_URL like any other local LLM server:

    LLM_BASE_URL=http://host.docker.internal:8787/v1
    LLM_MODEL=sonnet

Text only: the moment picker's transcript passes come here. Frame/video stages
(layout picker, silent footage) still need GEMINI_API_KEY.

Standard library only. Usage:  python3 bridge.py [--port 8787] [--model sonnet]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
TIMEOUT = float(os.environ.get("BRIDGE_TIMEOUT", "600"))
# Concurrent `claude` processes. Keep it low: they all share one plan's limits.
SLOTS = threading.BoundedSemaphore(int(os.environ.get("BRIDGE_CONCURRENCY", "2")))
# An empty working dir, so no project CLAUDE.md or files leak into the prompt.
WORKDIR = tempfile.mkdtemp(prefix="claude-bridge-")
# Optional shared secret: set it here and as LLM_API_KEY in OpenShorts' .env.
TOKEN = (os.environ.get("BRIDGE_TOKEN") or "").strip()

DEFAULT_MODEL = "sonnet"


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def build_prompt(body: dict) -> tuple[str, str]:
    """(system, user) from an OpenAI chat body, with the JSON schema inlined."""
    system_parts, user_parts = [], []
    for m in body.get("messages") or []:
        (system_parts if m.get("role") == "system" else user_parts).append(_text(m.get("content")))
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_schema":
        schema = (fmt.get("json_schema") or {}).get("schema")
        if schema:
            system_parts.append(
                "Your reply must be ONE JSON object that validates against this JSON Schema. "
                "No prose, no markdown fences.\n" + json.dumps(schema))
    elif fmt.get("type") == "json_object":
        system_parts.append("Your reply must be ONE JSON object. No prose, no markdown fences.")
    return "\n\n".join(p for p in system_parts if p), "\n\n".join(user_parts)


def strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


# Lean mode replaces Claude Code's own (large) system prompt and drops its tools,
# so a call costs roughly the transcript instead of ~30k tokens of agent setup.
# Turned off automatically if this CLI version rejects the flags.
_LEAN = {"on": os.environ.get("BRIDGE_LEAN", "1") != "0"}


def _cmd(system: str, model: str, lean: bool) -> list:
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json", "--model", model, "--max-turns", "1"]
    if lean:
        # --strict-mcp-config (with no --mcp-config) loads no MCP servers and
        # --disable-slash-commands loads no skills: together with --tools ""
        # this took a test call from ~63k input tokens to ~1k.
        cmd += ["--system-prompt", system or "You are a helpful assistant.", "--tools", "",
                "--strict-mcp-config", "--disable-slash-commands"]
    elif system:
        cmd += ["--append-system-prompt", system]
    return cmd


def _exec(cmd: list, user: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, input=user, capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=WORKDIR)
    except subprocess.TimeoutExpired:
        raise BridgeError(504, f"claude did not answer within {TIMEOUT:.0f}s")
    except FileNotFoundError:
        raise BridgeError(500, f"claude CLI not found ({CLAUDE_BIN}); install Claude Code and log in")


def run_claude(system: str, user: str, model: str) -> dict:
    if not SLOTS.acquire(timeout=TIMEOUT):
        raise BridgeError(503, "bridge busy")
    try:
        proc = _exec(_cmd(system, model, _LEAN["on"]), user)
        err = (proc.stderr or "").lower()
        if _LEAN["on"] and proc.returncode != 0 and not (proc.stdout or "").strip() \
                and ("unknown option" in err or "unknown argument" in err or "error: option" in err):
            print("[bridge] this claude version lacks --system-prompt/--tools; using full mode",
                  flush=True)
            _LEAN["on"] = False
            proc = _exec(_cmd(system, model, False), user)
    finally:
        SLOTS.release()
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise BridgeError(502, f"claude exit {proc.returncode}: {(proc.stderr or out)[:400]}")
    u = data.get("usage") or {}
    print(f"[bridge] lean={_LEAN['on']} input={u.get('input_tokens')} "
          f"cache_read={u.get('cache_read_input_tokens')} "
          f"cache_write={u.get('cache_creation_input_tokens')}", flush=True)
    if data.get("is_error") or proc.returncode != 0:
        # Usage limit, auth expired, etc. 429 lets the caller's retry logic back off.
        msg = str(data.get("result") or proc.stderr or "claude error")[:400]
        status = 429 if "limit" in msg.lower() else 502
        raise BridgeError(status, msg)
    return data


def completion(body: dict, default_model: str) -> dict:
    model = (body.get("model") or default_model).strip() or default_model
    system, user = build_prompt(body)
    if not user:
        raise BridgeError(400, "no user message")
    data = run_claude(system, user, model)
    text = strip_fences(str(data.get("result") or ""))
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) \
        + int(usage.get("cache_creation_input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


def make_handler(default_model: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if not TOKEN:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

        def do_GET(self):
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                return self._send(200, {"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "anthropic"}
                    for m in ("sonnet", "opus", "haiku")]})
            if self.path.rstrip("/") in ("", "/health"):
                return self._send(200, {"ok": True})
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                return self._send(404, {"error": {"message": "not found"}})
            if not self._authorized():
                return self._send(401, {"error": {"message": "bad token"}})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(400, {"error": {"message": "invalid JSON body"}})
            if body.get("stream"):
                return self._send(400, {"error": {"message": "stream not supported"}})
            started = time.time()
            try:
                result = completion(body, default_model)
            except BridgeError as e:
                print(f"[bridge] {e.status} {e.message}", flush=True)
                return self._send(e.status, {"error": {"message": e.message}})
            u = result["usage"]
            print(f"[bridge] 200 {result['model']} in={u['prompt_tokens']} "
                  f"out={u['completion_tokens']} {time.time() - started:.1f}s", flush=True)
            self._send(200, result)

        def log_message(self, *_):  # quiet default access log
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="claude model alias used when the request names none (sonnet|opus|haiku)")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.model))
    print(f"[bridge] listening on http://{args.host}:{args.port}/v1  (claude: {CLAUDE_BIN})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
