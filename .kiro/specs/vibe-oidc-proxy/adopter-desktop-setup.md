# Adopter and Desktop setup extension

The user requests an adopter-focused README using COBESY, interactive and
unattended installers for CLI/Desktop, and local Desktop configuration. The
user explicitly forbids computer use: use files, CLI and supported service APIs
only. No UI automation or paid inference is permitted for verification.

Requirements/design: Desktop and CLI load the same global server plugin; do not
invent a second provider, proxy requirement or credential store. Resolve the
actual OpenCode config directory through the selected native CLI. A Desktop
target can use the bundled CLI when no independent CLI is installed. Preserve
JSONC, backups, unrelated options and explicitly chosen auth source. Existing
Vibe login remains recommended; no environment API key or implicit OIDC fallback.

Add `--target cli|desktop|both`, `--interactive`, `--yes`, `--auth-source`,
`--model`, `--connect` and `--restart`, with PowerShell equivalents including
`-Auto` for unattended setup. Default interactive behavior is terminal-only;
non-terminal runs never wait for input. Explicit interactive mode without a
terminal fails before changes. Conflicting modes and invalid options fail
before configuration mutation. Installer prompts use injected testable input.

Only explicit model selection changes the global default. Validate its provider/
model syntax, preserve unrelated provider choices when no selection is given,
and document Desktop/session overrides. Explicit auth-source selects or migrates
that source; absent selection preserves existing installs. New entries default
to existing-vibe. Restart and connect are opt-in post-install operations. Order
restart before connect; neither action sends an inference request. Report a
post-install failure accurately if configuration has already been saved.

- [x] Installer options/prompts, wrappers, targeted tests and independent review.
- [x] Adopter README and Desktop guide with verified support claims, current
  auth prerequisites, copyable commands, evidence limits and project memory.
- [x] Use the resulting PowerShell command to configure local Desktop and
  verify the backend's loaded provider/default model without a model call.

Official Vibe source says Windows works while official support targets Unix;
do not describe it as wholly unavailable on Windows. Explain same-OS native
keyring requirements rather than implying a WSL login automatically signs in
native Windows OpenCode. Put builder/test internals behind linked documentation.

Local configuration verification: the reviewed PowerShell command ran with
`-Auto -Target Desktop -AuthSource existing-vibe -Model vibe-oidc/mistral-large-4
-Connect -Restart -SkipPrepare` and explicit native tool paths. It exited zero.
The bundled CLI's supported `/api/model/default` returned provider `vibe-oidc`
and ID `mistral-large-4`; `/api/model` included that selection. No inference was
sent. This establishes backend defaults and connection, not the current model
displayed in an existing Desktop draft. No computer use occurred after the
user prohibited it; all setup and final verification used CLI/config/service APIs.

Installer review APPROVED with 1,047 Bun tests, lint, typecheck and the real
PowerShell copied-wrapper recording smoke. CI now runs the wrapper smoke under
Dagger and in each native OS job. Public Markdown guides are included in the
release archive; its focused regression failed before the builder change and
passed afterward. Documentation/release review and full Python checks follow.

Documentation and release packaging review APPROVED after the complete Python
suite passed 1,172 tests and Ruff passed. Local Markdown target checks passed
for 13 adopter/operator/steering documents. The README now leads with adopter
prerequisites and setup; optional proxy and builder details moved to linked
guides, and the release archive ships the public Markdown guides.

User questions clarified context compaction: OpenCode orchestrates and asks the
provider to generate the summary. The adapter/proxy forward that request; they
do not import Vibe's compaction runtime. Existing synthetic compaction-purpose
tests passed (24 focused tests including the surrounding metadata/traffic cases).
No forced real long-context compaction was performed.

WSL Dagger check exited zero: 1,172 Python tests, 1,047 Bun tests, static/build
checks and the native Linux shell-wrapper forwarding smoke all passed. Evidence
is in `reports/adopter-desktop-dagger.log`. PowerShell forwarding was checked
separately on Windows. Local setup was executed through the guide's PowerShell
command and confirmed through supported backend APIs; existing UI selections
remain outside the verification claim.

Desktop model visibility follow-up: the backend exposed 18 Vibe models in the
Desktop project's context, while the user reported none in its model picker.
An owned empty session using `vibe-oidc/mistral-large-4` was created and removed
through the API without inference. The user then fully quit/reopened Desktop
and confirmed the models appeared. The guide and steering record that recovery;
no second module installation or credential change was required. This is a
user-observed UI result, not UI automation or a new quota-counter test.

A separate integration naming defect used raw ID `vibe-oidc` in Connect Provider.
The SDK integration update now sets its display name to `Mistral-Vibe`, keeping
IDs and auth commands intact. Both auth routes failed their new regression tests
before the change and passed afterward; focused registration tests passed 24,
the full Bun suite passed 1,049, and lint/typecheck/build passed. No background
service restart was performed for this naming change during the user's session.
The preceding cross-platform Actions run 41309805277 completed successfully.
