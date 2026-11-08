# Token economics (optional)

Token economics cuts the prompt cost of resent conversation history. Agent
clients resend the full history on every turn, and large file-read tool results
are repeated turn after turn. When you enable economics, the proxy replaces
*historical* file-read tool results — ones the model has already seen and acted
on — with short, deterministic, clearly marked summaries produced by an
embedded [mmhmem](https://github.com/norandom/mmhmem) child running in the same
container. Clients are unaware; responses are relayed byte-exact as before; the
feature is **off by default** and **fail-open**: any failure in the economics
path relays the original request unchanged.

Every replacement starts with this fixed marker line (shown with its
placeholders resolved), followed by the child's summary:

```text
[mvibe econ] Summary of an earlier file read (<source hint>, <line count> lines, sha256:<first 12 hex chars>). The exact text was shown earlier in this conversation; read the file again for exact lines.
<mmhmem summary...>
```

The source hint is the read header's file path when the result carries a
`Read file <path>, lines A-B` line, else the tool call's `filePath` /
`file_path` / `path` argument, else `unknown source`.

**History-only rule.** Only results that sit *before* the trailing group are
ever eligible. The trailing group is the contiguous run of tool-result messages
at the end of the message list — the freshest results the model is about to act
on — and it is never touched. A result is further eligible only when its call
identifier correlates to a preceding assistant tool call, its tool name is in
the configured allowlist, and its string content reaches the configured minimum
size. Only the `content` field of an eligible message is ever rewritten; role,
call identifier, position and every other field stay untouched.

## Enabling

### Docker (the supported deployment)

Economics targets the Docker image from `docker-compose.yml`, which embeds the
mmhmem binary at `/usr/local/bin/mmhmem` (see the next section). Enable it by
setting the environment in `docker-compose.yml` (the commented block):

```yaml
    environment:
      MVIBE_ECON_MMHMEM_BIN: /usr/local/bin/mmhmem   # this single key enables economics
      # Optional tuning (defaults shown):
      MVIBE_ECON_PROJECT: mvibe
      MVIBE_ECON_TOOLS: read
      MVIBE_ECON_MIN_BYTES: "4096"
      MVIBE_ECON_TIMEOUT_MS: "45000"
      MVIBE_ECON_MEMO_MAX: "2000"
      MVIBE_ECON_RETRY_S: "300"
      MVIBE_ECON_CONCURRENCY: "2"
      # Child (mmhmem) settings — see "The embedded child":
      AGENT_MEM_DATA_DIR: /data/mmhmem
      AGENT_MEM_LOG_FILE: /data/mmhmem/audit/mmhmem.log
      # ... plus the child's worker settings
    command: ["--host", "0.0.0.0", "--port", "8765", "--econ"]
    # and uncomment the mmhmem-data volume (below + bottom of the file)
```

- `MVIBE_ECON_MMHMEM_BIN` alone enables economics. While it is unset or blank
  the proxy spawns no child, makes no extra calls, and relays bodies exactly as
  before.
- `--econ` is optional: it makes incomplete or invalid configuration a startup
  **error** (exit code 2) instead of a warning. Without the flag, a bad
  `MVIBE_ECON_*` value produces a stderr warning naming the key and economics
  stays disabled while the proxy serves normally.

### Without Docker

Non-Docker installs need a separately installed mmhmem binary on `PATH` from a
release that contains the `shunt_content` tool (companion spec
`shunt-content-tool`, v0.5.2+). The configuration key takes the path to that
executable file — not a bare command name:

```bash
mvibe$ MVIBE_ECON_MMHMEM_BIN="$(command -v mmhmem)" \
  uv run --locked --extra proxy -- python proxy.py --econ --log
```

The path must exist and be executable; otherwise startup reports the key and
disables economics (or exits 2 with `--econ`).

## Configuration reference

Validated once at startup. A malformed value is an `EconConfigError` naming the
key: with `--econ` the proxy exits 2 with that message; without it the message
is printed as a stderr warning and economics stays disabled.

| Key | Default | Meaning | Error behavior |
| --- | --- | --- | --- |
| `MVIBE_ECON_MMHMEM_BIN` | unset ⇒ economics **off** | Path to the mmhmem binary (in Docker: `/usr/local/bin/mmhmem`). Blank/unset disables economics entirely. | Set but missing or not executable ⇒ error naming `MVIBE_ECON_MMHMEM_BIN`. |
| `MVIBE_ECON_PROJECT` | `mvibe` | Project name passed to the child's `shunt_content` call. | Blank falls back to the default; never errors. |
| `MVIBE_ECON_TOOLS` | `read` | Comma-separated allowlist of tool names, matched case-insensitively. Only `read` is tested in v1. | Hard-excluded names (below) are dropped with one stderr warning naming them; a list of only hard-excluded names leaves nothing eligible. |
| `MVIBE_ECON_MIN_BYTES` | `4096` | Minimum UTF-8 byte size for a string tool-result content to be eligible. | Must be an integer ≥ 256. |
| `MVIBE_ECON_TIMEOUT_MS` | `45000` | Timeout for one background warm-up call to the child. | Must be an integer ≥ 1. |
| `MVIBE_ECON_MEMO_MAX` | `2000` | Bound of the in-process memo (LRU). | Must be an integer ≥ 1. |
| `MVIBE_ECON_RETRY_S` | `300` | Minimum wait before a failed content hash is fetched again. | Must be an integer ≥ 0. |
| `MVIBE_ECON_CONCURRENCY` | `2` | Maximum simultaneous background warm-up calls. | Must be an integer ≥ 1. |

Hard-excluded tool names are never eligible in v1, even when listed:
`edit`, `write`, `bash`, `shell`, `execute`, `patch`, `multiedit`, and any
name starting with `mmhmem`. Editing, writing and shell results carry the
state changes and error lines the model acts on; mmhmem's own results must
never be re-summarized.

`--econ` without `MVIBE_ECON_MMHMEM_BIN` exits 2 with:
`error: --econ requires MVIBE_ECON_MMHMEM_BIN (path to the mmhmem binary) to enable token economics`.

## The embedded child

The Docker image embeds the statically linked `x86_64-unknown-linux-musl`
binary from mmhmem's CI-published release for `ARG MMHMEM_VERSION`, verified
against the release `SHA256SUMS` at build time and installed at
`/usr/local/bin/mmhmem`. It is present even while economics is off — enabling
it is purely an environment decision.

- **`MMHMEM_VERSION` must always point at an already-published mmhmem release**
  (`0.5.2` by default, the first release shipping `shunt_content`). Override it at
  build time if needed, e.g. `MMHMEM_VERSION=0.5.3 docker compose up --build`.
- **Child environment.** The child is launched as `mmhmem stdio` and inherits
  the proxy container's environment, so you configure it through the same
  compose `environment` block: durable state via `AGENT_MEM_DATA_DIR`, the
  hash-only audit log via `AGENT_MEM_LOG_FILE`, and its worker settings (an
  OpenAI-compatible endpoint the child calls to produce summaries). A child
  without a reachable worker reports tool errors; the proxy stays fail-open and
  relays raw content. mmhmem's local stdio profile reads
  `WORKER_API_URL` / `WORKER_MODEL` / `WORKER_API_KEY` (merged with
  `.mmhmem.json`).
- **Volumes.** mmhmem state is optional and off by default: uncomment the
  `mmhmem-data:/data/mmhmem` mount and the named volume at the bottom of
  `docker-compose.yml` together with `AGENT_MEM_DATA_DIR` /
  `AGENT_MEM_LOG_FILE`. Without it, child state lives only in the container's
  writable layer. The proxy container runs as uid 1000 (`app`); make sure the
  mounted volume is writable by that user (a freshly created named volume is
  root-owned — fix ownership once before first start).
- **No ports.** The child runs in the local stdio profile: zero mandatory
  environment variables, no token, and no listener. No port is published for
  it; the only published port remains the proxy's own.
- **Static binary & CI verification.** Because the embedded binary is
  statically linked against musl, it does not depend on the `python:3.12-slim`
  runtime image's glibc version. The Dagger CI pipeline (`.dagger/ci.py check`)
  builds the image and verifies both `/usr/local/bin/mmhmem --version` and the
  `proxy.py` entrypoint locally and in GitHub Actions.

## Trust and privacy boundary

- Tool-result contents **never traverse a network to reach mmhmem**. They are
  written only to the stdin pipe of the supervised child in the same container;
  the child opens no listener, and the proxy never calls itself. There is no
  HTTP hop, no TLS, and no token on the wire for this path.
- **The child — and, if configured, its worker-model egress — see full
  tool-result contents by design.** Producing a summary requires the content;
  the child sends it to the configured worker endpoint. The effective trust
  boundary is therefore that worker endpoint and its provider. Point it at
  infrastructure you control, or leave economics off.
- Security-critical content (for example PEM private-key headers, bearer or
  API-key assignments, cloud access-key identifiers) is detected **child-side**
  and bypassed without involving the worker; the proxy memoizes the bypass so
  the content is never sent to the child's worker on later resends. It is still
  relayed to Mistral as part of the request, exactly as without economics.
- mmhmem's own audit log records **hashes only** — never content.
- Proxy diagnostics, exports, and error messages never contain content, source
  hints, file paths, or content hashes: economics events carry integers and
  outcome enums only, and errors from the child are sanitized to a short class
  tag.

## How it behaves

- **First sight: raw + warm-up.** The first request carrying an eligible result
  relays it unchanged and schedules one background fetch (per distinct content
  hash, deduplicated, bounded by `MVIBE_ECON_CONCURRENCY`). The request path
  never waits on the child — summaries are only ever taken from the memo — so
  economics adds no request latency.
- **Memoized replacement.** Once a summary exists, every later resend of the
  same content is replaced by the stored marker-plus-summary text. The memo
  stores the final rendered replacement, so repeats are byte-identical for the
  life of the proxy process.
- **Bypass.** When the child reports a bypass (security-critical content, below
  threshold, unsupported kind), the result is relayed raw and the bypass is
  permanent for the process: no further fetches for that content.
- **Failure back-off.** A failed fetch (child error, timeout, crash) is
  memoized and not retried before `MVIBE_ECON_RETRY_S`. A crashed child is
  restarted with capped exponential back-off (1 s doubling, at most 30 s). An
  evicted memo entry (LRU at `MVIBE_ECON_MEMO_MAX`) simply causes one raw relay
  before re-warm — never an error.
- **Fail-open everywhere.** Any exception in the economics path logs a warning
  and relays the original body. Requests without tool-result messages skip the
  economics path entirely. Responses are never touched, and the
  account-attribution metadata attached to each request is unchanged.
- **Startup failures are not fail-open.** With economics enabled, a configured
  binary that exists but cannot be spawned or does not speak the stdio MCP
  protocol fails proxy startup with an actionable message naming
  `MVIBE_ECON_MMHMEM_BIN` (unset the key to disable economics). A missing or
  non-executable path is instead a configuration error (warning, or exit 2 with
  `--econ`).
- **Known limitation: Ollama.** Ollama's wire format carries no tool-call
  identifier on tool results, so results cannot be correlated to a tool name.
  Ollama-dialect histories always relay raw and make zero mmhmem calls —
  fail-closed by protocol, never a wrong rewrite. OpenAI and Anthropic-dialect
  requests benefit equally.

## Prompt-cache interplay

Replacing historical content changes the upstream request body, so a provider
prompt cache primed on the raw history will not match the rewritten prefix:
**expect a cache re-key (cache miss and re-priming) on the first
economics-enabled resends.** After that, replacement text is deterministic per
content — the marker embeds a short content-hash prefix and the line count, and
the memo returns byte-identical text for the same content — so a warmed history
produces a stable prefix that caches normally. Two cases re-key again: after a
proxy restart (the memo is in-process, so the first resend relays the raw
content before re-warm) and after an eviction/re-warm; once re-warmed, mmhmem's
deterministic summaries and `cached` outcome reproduce the same replacement
text, so the prefix stabilizes again. Content before the first replaced result
is untouched, so long leading system/user prefixes keep their cache value.

## Reading econ events

Economics diagnostics flow through the existing opt-in channels: `--log`
(JSON lines on stdout) and `--observe` (OpenObserve export; see
[observability](observability.md)). Per request, the existing `proxy_request`
event gains `econ_*` fields — but only when at least one result was eligible
(no eligible result, no econ fields):

| Field | Meaning |
| --- | --- |
| `econ_outcome` | `applied` for a normal pass, `error` when the rewrite itself raised (body relayed raw). |
| `econ_eligible` / `econ_replaced` / `econ_bypassed` / `econ_pending` | Count of eligible results, replaced ones, permanently bypassed ones, and ones relayed raw awaiting a summary (first sight or inside back-off). |
| `econ_in_bytes` / `econ_out_bytes` | UTF-8 byte sums over the eligible results' content before and after the rewrite (raw-relayed results contribute to both). |
| `econ_est_saved_tokens` | `(econ_in_bytes − econ_out_bytes) // 4` — a **labelled estimate** using the same ÷4 heuristic as mmhmem, counting only actually replaced content. It can be negative in edge cases (a summary longer than the original). |

Background warm-ups emit standalone `econ_warm` events with `econ_outcome`
(`summarized`, `cached`, `bypassed`, `failed`), `econ_latency_ms`, and
`econ_in_bytes` (the fetched content's size). Judge savings from these events,
not from the fact that a child is configured: a deployment whose warm events
show `failed` (for example an unreachable worker) is relaying everything raw
and saving nothing.

## Manual live check (not part of the offline gate)

This optional check exercises economics end-to-end against a real mmhmem child
and worker; the release gate stays offline by design. Record results in
[live results](live-results.md) when performed. The example uses the Docker
deployment with the embedded child:

1. **Configure.** In `docker-compose.yml`, ensure `MMHMEM_VERSION` points to a
   published release that contains `shunt_content` (v0.5.2+), uncomment the
   economics block, point the child's worker settings at a reachable endpoint,
   uncomment the `mmhmem-data` volume, and add `--econ` (and `--log`, to see
   the events) to the command:

   ```yaml
       environment:
         MVIBE_ECON_MMHMEM_BIN: /usr/local/bin/mmhmem
         AGENT_MEM_DATA_DIR: /data/mmhmem
         AGENT_MEM_LOG_FILE: /data/mmhmem/audit/mmhmem.log
         WORKER_API_URL: https://your-worker-endpoint
         WORKER_MODEL: your-provider/your-model
         WORKER_API_KEY: change-me
       command: ["--host", "0.0.0.0", "--port", "8765", "--econ", "--log"]
   ```

2. **Start and watch:** `docker compose up --build -d` then
   `docker compose logs -f proxy`. Startup without an
   `economics startup failed` message means the child spawned and handshook.

3. **Turn 1 — first sight.** Send a two-turn-shaped chat request whose history
   contains one large (`MVIBE_ECON_MIN_BYTES`, default 4096 bytes) file-read
   result. With a real client, simply read a >4 KiB file, then ask a follow-up
   question so the history (including the read result) is resent. Synthetic
   equivalent:

   ```bash
   python3 - <<'PY' > /tmp/econ-body.json
   import json
   filler = '# economics live check padding line\n' * 220   # > 4096 UTF-8 bytes
   messages = [
       {'role': 'assistant', 'tool_calls': [{'id': 't1', 'type': 'function',
           'function': {'name': 'read',
                        'arguments': json.dumps({'filePath': '/tmp/notes.md'})}}]},
       {'role': 'tool', 'tool_call_id': 't1',
        'content': 'Read file /tmp/notes.md, lines 1-220\n' + filler},
       {'role': 'user', 'content': 'Reply with exactly OK.'},
   ]
   print(json.dumps({'model': 'ministral-8b-latest', 'messages': messages}))
   PY
   curl -s http://127.0.0.1:8765/v1/chat/completions \
        -H 'Content-Type: application/json' --data-binary @/tmp/econ-body.json
   ```

   The logs show one `proxy_request` event with `econ_outcome: applied`,
   `econ_eligible: 1`, `econ_pending: 1` (relayed raw), followed by an
   `econ_warm` event with `econ_outcome: summarized` and its latency.

4. **Turn 2 — resend.** Send the same body with one appended user message
   asking the model to quote what it was shown, e.g.
   `"Quote verbatim the first line of the file content shown earlier in this conversation."`.
   The logs now show `econ_replaced: 1` and `econ_est_saved_tokens` greater
   than zero, and the model quotes the marker line — the proxy never prints
   content itself (events carry ints and outcome enums only), but the model
   sees the rewritten history, so its answer starts with
   `[mvibe econ] Summary of an earlier file read (…)`. The replacement text
   — marker plus summary — is byte-pinned by the offline tests in
    `tests/test_econ.py`; the marker template itself lives in
    `mvibe_proxy/econ.py` (`MARKER`).

5. **Troubleshoot.** Repeated `econ_warm` events with `econ_outcome: failed`
   mean the child or worker is erroring (check `WORKER_*`, the endpoint, and
   `MMHMEM_VERSION`): requests still succeed, relaying raw content — that is
   the fail-open guarantee.
