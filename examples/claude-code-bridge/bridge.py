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


