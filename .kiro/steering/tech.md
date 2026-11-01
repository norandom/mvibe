# Technology and verification

Updated: 2026-11-01. Bootstrapped to preserve the shared-auth architecture and
the working native Vibe authentication path.

Python 3.12+ owns authentication and transport policy. Use uv or uvx for Python
preparation and execution, including existing uv-managed interpreters. Never
invoke or install Windows Store Python. Preserve the locked dependency setup.

The OpenCode adapter uses TypeScript and pinned Bun 1.4.4. Keep `bun.lock` and
the package manifest aligned. Authentication crosses the bounded Python stdio
bridge; TypeScript must not duplicate credential storage or renewal.

Read [authentication.md](authentication.md) before changing sign-in, keyring,
credential forwarding, quota attribution or request diagnostics. Keep credentials
bound to allowed destinations and out of ordinary diagnostics.

Use pytest with two xdist workers and scope scheduling by default; serial runs
are for focused debugging. Run Ruff for Python and ESLint, type checks, build
and Bun tests for the provider. Test actual OpenCode consumer behavior with the
prepared host, including streaming, cancellation, tools and no replay.

Reuse Dagger through WSL locally and GitHub Actions for Linux checks. Native
Windows/macOS checks remain separate evidence. Tests must not require a local
Vibe installation, real keyring contents or network access; use pinned fixtures
for native contracts and explicit mocked OS credential boundaries.

OpenCode auto-updates. Record actual host versions and hashes separately from
the pinned SDK/CLI reference. Do not treat a user-reported version as verified.
Do not run paid inference solely to satisfy an offline test gate. Keep live
counter checks distinct from transport and offline regression evidence.

Use configuration files, CLI and supported service APIs for local setup and
verification. The user forbids computer/UI automation for this work; do not
resume it without a new explicit request. Desktop and CLI share the server's
global plugin/config. A global model default applies to fresh selections;
saved draft/session choices can override it. Do not edit opaque Desktop stores.
The adopter installers offer terminal prompts and unattended `--yes` / `-Auto`
flags. Restart/connect require explicit choices; model changes are global and
opt-in. Local Desktop backend setup was verified with Mistral-Vibe Large 4.
If backend/CLI Vibe models are present but Desktop's model picker omits them,
fully quit and reopen Desktop before changing installation or credentials.
The user confirmed this restored the models on 2026-11-01 after service setup.
Never rebuild a checkout while the user's service loads its plugin. On OpenCode
2.2.5, plugin reload can leave a cached SDK referencing a disposed bridge.
Ctrl+C and `opencode -c` do not necessarily replace that service; use an explicit
service restart after preparing an upgrade. Local installation now uses a stable
packaged snapshot outside this checkout. Keep it immutable during development.
Do not resurrect disposed bridges or replay requests to mask stale host caches.

OpenCode orchestrates context compaction and calls the provider for its summary.
The adapter forwards that request using shared auth. Reusing Vibe authentication
does not import Vibe's agent runtime, compaction policy or tool executor.
