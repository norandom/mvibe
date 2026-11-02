#!/usr/bin/env bash
# Live smoke test for the local mvibe proxy (OpenAI, Anthropic and Ollama dialects + embeddings).
#
# Run while the proxy is up (default http://127.0.0.1:8765, started via ./start_proxy.sh
# or ./start_proxy.ps1). No Authorization header is sent: upstream auth comes from the
# proxy's selected shared session (existing-vibe). Public test prompts only.
#
# Requirements: curl (JSON is extracted with grep/sed; the response shapes are pinned
# by tests/test_compat.py, so no jq/python needed).
# Usage:
#   ./smoketests/smoke.sh
#   BASE_URL=http://127.0.0.1:8799 CHAT_MODEL=mistral-large-4 ./smoketests/smoke.sh
#
# Prints a PASS/FAIL table and exits non-zero if any check fails.
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8765}"
CHAT_MODEL="${CHAT_MODEL:-ministral-8b-latest}"
EMBED_MODEL="${EMBED_MODEL:-mistral-embed}"
ANTHROPIC_VERSION="${ANTHROPIC_VERSION:-2023-06-01}"
MAX_TIME="${MAX_TIME:-120}"
BASE_URL="${BASE_URL%/}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

check() { # check <name> <ok:0/1> <detail>
    if [ "$2" -eq 0 ]; then PASS=$((PASS + 1)); r=PASS; else FAIL=$((FAIL + 1)); r=FAIL; fi
    printf '%-42s %-4s %s\n' "$1" "$r" "$3"
}

req() { # req <method> <path> <outfile> [bodyfile] [extra curl args...]; prints "code time"
    local method="$1" path="$2" out="$3"; shift 3
    local body=""
    if [ $# -gt 0 ] && [ -f "$1" ]; then body="$1"; shift; fi
    local args=(-s --max-time "$MAX_TIME" -X "$method" "$BASE_URL$path" -o "$TMP/$out" -w '%{http_code} %{time_total}')
    if [ -n "$body" ]; then args+=(-H 'Content-Type: application/json' --data-binary "@$body"); fi
    args+=("$@")
    curl "${args[@]}"
}

first_string() { # first_string <file> <json key> -> first "key": "value"
    grep -o "\"$2\": *\"[^\"]*\"" "$1" 2>/dev/null | head -1 | sed "s/\"$2\": *\"//; s/\"$//"
}

# 1. health
meta=$(req GET /health health.json); code=${meta%% *}
if [ "$code" = 200 ] && grep -q '"status": *"ok"' "$TMP/health.json"; then ok=0; else ok=1; fi
check 'GET /health' "$ok" "HTTP $code"

# 2. models, OpenAI shape
meta=$(req GET /v1/models models.json); code=${meta%% *}
n=$(grep -o '"id"' "$TMP/models.json" 2>/dev/null | wc -l)
if [ "$code" = 200 ] && [ "$n" -gt 0 ]; then ok=0; else ok=1; fi
check 'GET /v1/models (OpenAI shape)' "$ok" "HTTP $code, $n models"

# 3. chat, non-streaming
cat > "$TMP/chat.json" <<EOF
{"model": "$CHAT_MODEL", "messages": [{"role": "user", "content": "Reply with exactly OK."}], "max_tokens": 64, "stream": false}
EOF
meta=$(req POST /v1/chat/completions chat-resp.json "$TMP/chat.json"); code=${meta%% *}
answer=$(first_string "$TMP/chat-resp.json" content)
finish=$(first_string "$TMP/chat-resp.json" finish_reason)
if [ "$code" = 200 ] && [ -n "$answer" ]; then ok=0; else ok=1; fi
check 'POST /v1/chat/completions' "$ok" "HTTP $code, answer '$answer', finish $finish"

# 4. chat, streaming
cat > "$TMP/chat-stream.json" <<EOF
{"model": "$CHAT_MODEL", "messages": [{"role": "user", "content": "Reply with exactly OK."}], "max_tokens": 64, "stream": true}
EOF
meta=$(req POST /v1/chat/completions chat-stream.txt "$TMP/chat-stream.json" -N); code=${meta%% *}
frames=$(grep -c '^data:' "$TMP/chat-stream.txt" 2>/dev/null); frames=${frames:-0}
done=$(grep -c '\[DONE\]' "$TMP/chat-stream.txt" 2>/dev/null); done=${done:-0}
content=$(grep '^data:' "$TMP/chat-stream.txt" 2>/dev/null | sed 's/^data: //' | grep -v '\[DONE\]' \
    | grep -o '"content": *"[^"]*"' | sed 's/"content": *"//; s/"$//' | tr -d '\n')
if [ "$code" = 200 ] && [ "$frames" -gt 0 ] && [ "$done" -gt 0 ] && [ -n "$content" ]; then ok=0; else ok=1; fi
check 'POST /v1/chat/completions (stream)' "$ok" "HTTP $code, $frames data frames, content '$content'"

# 5. embeddings
cat > "$TMP/emb.json" <<EOF
{"model": "$EMBED_MODEL", "input": ["hello"]}
EOF
meta=$(req POST /v1/embeddings emb-resp.json "$TMP/emb.json"); code=${meta%% *}
dims=$(sed -n 's/.*"embedding": *\[\(.*\)\].*/\1/p' "$TMP/emb-resp.json" 2>/dev/null | tr ',' '\n' | wc -l)
if [ "$code" = 200 ] && [ "$dims" -gt 0 ]; then ok=0; else ok=1; fi
check 'POST /v1/embeddings' "$ok" "HTTP $code, model $EMBED_MODEL, $dims dims"

# 6. Anthropic messages, non-streaming
cat > "$TMP/msg.json" <<EOF
{"model": "$CHAT_MODEL", "max_tokens": 64, "messages": [{"role": "user", "content": "Reply with exactly OK."}]}
EOF
meta=$(req POST /v1/messages msg-resp.json "$TMP/msg.json" -H "anthropic-version: $ANTHROPIC_VERSION"); code=${meta%% *}
type=$(first_string "$TMP/msg-resp.json" type)
stop=$(first_string "$TMP/msg-resp.json" stop_reason)
text=$(first_string "$TMP/msg-resp.json" text)
if [ "$code" = 200 ] && [ "$type" = message ]; then ok=0; else ok=1; fi
check 'POST /v1/messages (Anthropic)' "$ok" "HTTP $code, stop_reason $stop, content '$text'"

# 7. Anthropic messages, SSE stream
cat > "$TMP/msg-stream.json" <<EOF
{"model": "$CHAT_MODEL", "max_tokens": 64, "stream": true, "messages": [{"role": "user", "content": "Reply with exactly OK."}]}
EOF
meta=$(req POST /v1/messages msg-stream.txt "$TMP/msg-stream.json" -N -H "anthropic-version: $ANTHROPIC_VERSION"); code=${meta%% *}
events=$(grep '^event:' "$TMP/msg-stream.txt" 2>/dev/null | sed 's/^event: //' | tr '\n' ' ')
if [ "$code" = 200 ] && grep -q '^event: message_start' "$TMP/msg-stream.txt" && grep -q '^event: message_stop' "$TMP/msg-stream.txt"; then ok=0; else ok=1; fi
check 'POST /v1/messages (Anthropic stream)' "$ok" "HTTP $code, events: $events"

# 8. Anthropic models (paginated shape via header)
meta=$(req GET /v1/models models-anthropic.json -H "anthropic-version: $ANTHROPIC_VERSION"); code=${meta%% *}
n=$(grep -o '"id"' "$TMP/models-anthropic.json" 2>/dev/null | wc -l); n=${n:-0}
hm=$(grep -oE '"has_more": *(true|false)' "$TMP/models-anthropic.json" 2>/dev/null | head -1 | sed 's/.*: *//')
if [ "$code" = 200 ] && [ "$n" -gt 0 ] && [ -n "$hm" ]; then ok=0; else ok=1; fi
check 'GET /v1/models (Anthropic dialect)' "$ok" "HTTP $code, $n models, has_more=$hm"

# 9. Ollama version + tags
meta=$(req GET /api/version ollama-version.json); code=${meta%% *}
ver=$(first_string "$TMP/ollama-version.json" version)
if [ "$code" = 200 ] && [ -n "$ver" ]; then ok=0; else ok=1; fi
check 'GET /api/version (Ollama)' "$ok" "HTTP $code, version $ver"
meta=$(req GET /api/tags ollama-tags.json); code=${meta%% *}
n=$(grep -o '"name"' "$TMP/ollama-tags.json" 2>/dev/null | wc -l)
if [ "$code" = 200 ] && [ "$n" -gt 0 ]; then ok=0; else ok=1; fi
check 'GET /api/tags (Ollama)' "$ok" "HTTP $code, $n models"

# 10. Ollama chat
cat > "$TMP/ollama-chat.json" <<EOF
{"model": "$CHAT_MODEL", "messages": [{"role": "user", "content": "Reply with exactly OK."}], "stream": false}
EOF
meta=$(req POST /api/chat ollama-chat-resp.json "$TMP/ollama-chat.json"); code=${meta%% *}
if grep -q '"done": *true' "$TMP/ollama-chat-resp.json" 2>/dev/null; then donef=true; else donef=false; fi
msg=$(first_string "$TMP/ollama-chat-resp.json" content)
if [ "$code" = 200 ] && [ "$donef" = true ]; then ok=0; else ok=1; fi
check 'POST /api/chat (Ollama)' "$ok" "HTTP $code, done=$donef, message '$msg'"

# 11. unknown /api/* -> adapter-shaped 404
meta=$(req GET /api/nope ollama-404.json); code=${meta%% *}
err=$(first_string "$TMP/ollama-404.json" error)
if [ "$code" = 404 ] && [ -n "$err" ]; then ok=0; else ok=1; fi
check 'GET /api/nope (expect 404)' "$ok" "HTTP $code, $err"

echo "------------------------------------------------------------"
echo "SUMMARY: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then exit 0; else exit 1; fi
