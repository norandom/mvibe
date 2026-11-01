# First GitHub release delivery

User requested Dagger in WSL, GitHub Actions, Linux/Windows/macOS distribution,
prebuilt artifacts and appropriate linters on 2026-11-01. Target repository:
`norandom/mvibe`. This extends existing packaging/operator requirements 9.1-9.5,
10.1-10.3 and 12.8/12.10; authentication and inference behavior are unchanged.

## Requirements

1. Produce version-matched Python wheel and bundled JavaScript provider in a
   portable release archive with SHA256 checksums, licenses and both installers.
   Install from the built artifacts without TypeScript compilation or a checkout;
   uv, Bun and OpenCode 2 remain explicit prerequisites. No credentials included.
2. Use the same Dagger pipeline locally through WSL and on GitHub Actions for
   lint/tests/build. Test release installation on native Linux, Windows and macOS
   before publishing the first release. Keep all account access out of CI.
3. Add pinned pragmatic correctness linters for Python, TypeScript, shell,
   PowerShell and Actions without imposing a repository-wide formatting rewrite.
4. Tag/version validation and least-privilege publication must prevent release
   uploads before checks pass. Preserve JSONC settings/backups and OIDC ownership.

## Design

Use Dagger's Python SDK through `dagger run`, with locked SDK dependencies and
explicit container tool versions. Linux containers perform repeatable checks and
produce a universal wheel/JavaScript archive. GitHub native runners validate the
same archive on the three operating systems; containers do not prove native
Windows/macOS behavior. The archive includes a locked uv runtime project using
the prebuilt local wheel and bundled installer/provider, with no development
dependencies. Dependencies and a managed Python may download during installation;
request handling remains offline with respect to dependency installation.

## Deliverables

- [x] 1. Prebuilt bundle and release-mode shell/PowerShell installers; focused tests.
- [ ] 2. Dagger checks/build, linters and native Actions gates; first GitHub release.

Each deliverable receives independent review and fresh verification. Publication
is authorized by the user's explicit request; no additional confirmation required.

Deliverable 1 independently APPROVED after correcting SDK externalization.
Fresh Windows archive installation and actual extracted-bundle OpenCode 2.2.5
integration passed all 12 synthetic host checks. Python wheel/runtime imports,
JSONC backup/idempotency and archive/wheel checksums passed independently.
Effect and the plugin SDK remain host-provided; the installer is self-contained.

## Quota attribution release hold

Before publication, the user reported that the API usage counter increases
instead of the expected Vibe quota. Publication is held; no release tag exists.
Local Vibe 3.0.4 source confirms its deliberate client User-Agent is missing from
both adapter routes. OIDC defaults/token kind/tenant-selection semantics match
in source, but that does not establish actual account billing attribution.

- [x] 3. Restore shared Vibe-derived client metadata with transparent adapter
  identification, captured-request regressions for both routes, unchanged OIDC
  and destination/redirect/no-replay policies, and independent review.

Do not fabricate plan/session/telemetry fields or claim a billing fix from an
HTTP success. Vibe's completion metadata also influences request analytics;
the server's quota classification must be confirmed through observed counters
or provider documentation/support before claiming subscription usage. Further
live inference is deferred while that distinction is investigated.

Task 3 independently APPROVED: shared resolved snapshots now include the
Vibe-derived User-Agent plus the installed mvibe-auth version; the plugin accepts
only this additional metadata header. Fresh canonical verification passed all
11 stages, 1,041 Python tests, 987 Bun tests and 97 parent mappings:
`reports/verification/7536cdc500d844ac9eb85bf4543f5578/acceptance.json`.
No quota result is inferred from this offline evidence.

The preceding release candidate passed Dagger and native Linux, Windows and
macOS jobs in https://github.com/norandom/mvibe/actions/runs/41293805140.
Publication remains held for quota attribution despite those functional gates.

The user's subsequent disposed-helper error exposed OpenCode 2.2.5 retaining
cached SDKs across watched plugin reloads. The bridge correctly stays disposed;
do not resurrect it or change credentials. Builds of this live checkout trigger
reloads. After builds completed, the actual background service was restarted
with `opencode service restart`, successfully, for a clean user retest. Evidence
and limits: `reports/disposed-helper-debug.md`. A durable host cache invalidation
change belongs upstream and needs an isolated reload regression first.

### Complete native request parity

The adapter-suffixed User-Agent above did not resolve the user's API-counter
observation. The subsequent [native request parity amendment](native-request-parity.md)
replaces it with the exact native User-Agent and adds the previously missing
completion metadata through shared Python policy. Independent review approved
actual-host captures covering overlapping sessions, native read continuations
and auxiliary generation. Dagger checks passed. The local provider was rebuilt
and `opencode service restart` succeeded. Publication is still held pending the
user's counter retest; none of these offline checks proves Vibe quota usage.
