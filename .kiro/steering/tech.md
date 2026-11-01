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
