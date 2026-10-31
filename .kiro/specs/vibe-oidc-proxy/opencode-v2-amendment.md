# OpenCode V2 target amendment

On 2026-10-31 the user clarified that the target is OpenCode 2 with automatic updates, and reported a local 2.2.6 installation. This supersedes the original 1.19.5 SDK target. At amendment time, task 1.1 was complete and task 1.2 awaited V2 setup. All implementation tasks are now checked in tasks.md; the final feature gate and evidence limits are recorded in validation.md.

## Verified evidence

- Local `C:/Users/mariu/AppData/Local/Programs/OpenCode/resources/opencode-cli.exe --version` returned `opencode v2.2.5`; the adjacent version file also reports 2.2.5. Desktop executable metadata reports 2.2.5.0. User-reported 2.2.6 remains distinct from these observations; do not claim it tested.
- Published V2 packages are named `@opencode/plugin` and `@opencode/cli`. The legacy `@opencode-ai/plugin` and `opencode-ai` packages follow a separate V1 release line.
- [Official migration guide](https://opencode.ai/v2/docs/build/plugins/migrate-v1) requires Plugin.define, setup context registrations, directory-based plugin configuration and returned cleanup.
- [V2 server API](https://opencode.ai/v2/docs/build/plugins/server) documents provider/integration transforms and command authentication.
- [Tagged 2.2.5 integration implementation](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/integration.ts) stores successful command stdout as a key-shaped credential. The plugin will emit only a fixed non-secret marker. OAuth callbacks require actual OAuth credential fields; external methods persist metadata without running a login callback. Neither supplies the old V1 callback contract.
- Examined tagged integration source SHA256: EC5F1299DF972928BAF457B277ECB2146ACD1B429870B9E61F96EB96A3F82EBE.

## Design and task correction

The shared Python AuthSession remains the only OIDC implementation and credential owner. The V2 provider registers command-based browser sign-in using a Python login command. Only a non-secret success marker reaches OpenCode connection storage; requests ignore that marker and obtain current headers through the managed Python bridge. There is no API-key authentication option. Task 7.2 explicitly owns this integration and its cancellation/failure tests.

Exact SDK/host dependencies remain locked for reproducible offline verification. Compatibility targets current stable V2, with an explicit network-enabled preparation/check action for newer releases and a recorded host/SDK pair. Future auto-updates are unverified until compatibility checks execute. Request handling never installs dependencies.

The prior design/task validation reports describe the original V1 graph; they do not establish V2 runtime compatibility. Task-local review and final validation must use this amendment. No live account action was performed during this discovery.
