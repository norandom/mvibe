# Smoketests

Live smoke tests for the local mvibe proxy. They check the OpenAI dialect
(`/v1/models`, `/v1/chat/completions` incl. streaming, `/v1/embeddings`), the
Anthropic dialect (`POST /v1/messages` incl. SSE streaming, dialect-switched
`GET /v1/models` via the `anthropic-version` header) and the Ollama dialect
(`/api/version`, `/api/tags`, `/api/chat`, adapter-shaped 404).

Start the proxy first (default loopback `127.0.0.1:8765`), then run one of:

```powershell
# Windows PowerShell
pwsh -NoProfile -File smoketests/smoke.ps1
# optional: pwsh -NoProfile -File smoketests/smoke.ps1 -BaseUrl http://127.0.0.1:8799 -ChatModel mistral-large-4 -EmbedModel mistral-embed
```

```bash
# Linux / macOS / WSL (WSL reaches the Windows proxy on 127.0.0.1 when localhost forwarding is on)
./smoketests/smoke.sh
# optional: BASE_URL=http://127.0.0.1:8799 CHAT_MODEL=mistral-large-4 ./smoketests/smoke.sh
```

Requirements: `curl` (the `.sh` variant extracts JSON with grep/sed only — the response
shapes are pinned by `tests/test_compat.py` — so no jq/python needed). Both print a
PASS/FAIL table and exit non-zero on any failure.

Notes:

- No `Authorization` header is sent; upstream authentication comes from the
  proxy's selected shared session (default `existing-vibe`). Keep the proxy on
  loopback — it has no caller auth.
- Only public test prompts are used ("Reply with exactly OK.", "hello"); do not
  point these scripts at private data.
- Defaults assume a chat-capable model (`ministral-8b-latest`) and an embedding
  model (`mistral-embed`) that your account lists in `GET /v1/models`. Pick exact
  IDs from that list; reasoning models may need a larger `max_tokens` for a
  plain "OK" answer.
- Endpoint reference: `docs/proxy-guide.md` ("Ollama- and Anthropic-compatible
  endpoints"). Live evidence goes to `docs/live-results.md`.
