# Claude Code bridge (moment picker on your Claude plan)

Lets OpenShorts' transcript-based moment picker use Claude through a logged-in
Claude Code CLI, instead of Gemini or Ollama. Runs on the host, standard library only.

```bash
npm install -g @anthropic-ai/claude-code
claude                                    # log in once (personal account)
python3 examples/claude-code-bridge/bridge.py    # leave running
```

`.env`:

```
LLM_BASE_URL=http://host.docker.internal:8787/v1
LLM_MODEL=sonnet
LLM_TIMEOUT=600
GEMINI_API_KEY=...   # still used for layouts and silent footage
```

Then `docker compose restart backend`.

- Every request is one `claude -p` call and counts against your plan's usage
  limits. When the limit is hit the bridge answers 429.
- `BRIDGE_CONCURRENCY` (default 2) caps parallel `claude` processes.
- Optional `BRIDGE_TOKEN`: set it for the bridge and as `LLM_API_KEY` in `.env`.
- Listens on 127.0.0.1 only; Docker Desktop reaches it via `host.docker.internal`.
