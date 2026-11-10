# Handover — upstream-rate-limit-queue, task group 4 complete (2026-11-10)

## Status
- Group 4 (plugin integration) is done and committed:
  - `234e865` 4.1+4.2 (one dispatch): `provider.ts` reads the body once, attributes chat on the first attempt, rebuilds the outbound `Request` per attempt with a fresh credential snapshot, runs through `options.gate` keyed by model (`models` never paced), passes an exhausted 429 through unchanged with `x-should-retry: false`, and adds `queued_ms`/`rate_limit_rejections` with outcome `rate_limited` to the opt-in diagnostics line (27 tests; medium-effort independent review).
  - `d947cfd` 4.3 (controller, direct): `index.ts` parses `options.rateLimit` in `settings()` (invalid fields fail setup naming `rateLimit.<field>`), builds one `UpstreamGate` per plugin instance with hold warnings only when `diagnostics` is on, passes it to `createProviderFetch`, keeps `maxRetries = 0`. Integration finding fixed in the gate: an abort while an attempt is in flight detaches the attempt and releases the key (`#inFlight`), otherwise a superseded model-catalog discovery held the `models` key and `registration.test.ts` hung. The diagnostics line also carries `rate_limit_headers` (Req 1.4).
- Evidence at `d947cfd`: `bun test` 1441 pass / 0 fail; lint, typecheck, build clean; mutation check for the detachment test (fails without the fix, source restored).

## Learnings
- The catalog aborts a superseded discovery's signal; a transport that ignores the abort (test fakes) must not block the gate's per-key line, hence the detachment.
- Setup tests that use the SDK fetch must let the catalog's gated discovery finish first (`fakeBoundaries().discovered()` in `test/setup.test.ts`).
- Process: from 4.1 on, reviews are lightweight (controller mechanical checks plus a medium-effort reviewer when wanted); small adjacent tasks are combined; the controller may implement small tasks directly.

## Next
- Task 5 validation gate (python + bun gates green; native `dagger run ... ci.py check` as extra evidence), then `$kiro-validate-impl upstream-rate-limit-queue` (inline) and the feature-level completion verification.
- Then the `proxy-stats-propagation` spec (committed `d890743`, notes `7c75b6f`).
