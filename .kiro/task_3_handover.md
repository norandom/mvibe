# Handover — upstream-rate-limit-queue, task group 3 complete (2026-11-10)

## Status
- Group 3 (plugin rate-limit module `packages/opencode-vibe-oidc/src/rate-limit.ts`) is done and committed:
  - `8b54240` 3.1 `RateLimitOptions`/`parseRateLimitOptions`, `rateFor`, `parseRetryAfter`, `classifyRateLimit` (238 tests)
  - `74714bb` 3.2 `UpstreamGate` scheduling core: per-key FIFO, pacing, cross-key `maxQueue`, deadline refusals, abort-aware waits (43 tests)
  - `344b227` 3.3 429 hold, re-send and exhaustion mirroring the proxy gate; exhaustion returns the same `Response` unread (39 tests)
- Evidence at `344b227`: `bun test` 1409 pass / 0 fail; lint, typecheck, build clean. Each task had an independent Opus review (3.1 and 3.2 at xhigh, 3.3 at xhigh before the user lowered reviewer effort).

## Learnings
- The TypeScript gate mirrors `mvibe_proxy/ratelimit.py` line for line; the deliberate differences are: exhaustion returns the upstream 429 unaltered (Req 4.3, no `retry-after` added), the give-up check also considers `readyAt()`, body cancel is started but not awaited, `GateDeps.wallNow` feeds HTTP-date parsing.
- `bun:test` spreads array-valued `test.each` cases into arguments; wrap them in one-element tuples.
- Fake sleeps in gate tests yield a macrotask so the 5 s `bounded` guard can fire on a regression.
