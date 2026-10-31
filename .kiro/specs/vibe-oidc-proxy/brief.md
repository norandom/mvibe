# Brief: vibe-oidc-proxy

## Problem
Developers want OpenCode and similar OpenAI-compatible agents to use Vibe's separate Mistral browser OIDC flow and subscription identity. The proxy exists but its behavior, internal contracts and live compatibility have not been systematically verified.

## Current State
The repository contains a standalone Python proxy and 13 authentication/helper modules. README identifies the helpers as copied from Mistral Vibe 3.0.4 with local imports. There are no existing specs, core steering documents, tests or test configuration. All repository files were untracked at discovery; no baseline commit exists. Static inspection is evidence of implementation, not evidence that authentication or inference works.

See [current-state.md](current-state.md) for the code inventory and [verification-plan.md](verification-plan.md) for candidate checks.

## Desired Outcome
Capture intended behavior and existing internals, distinguish implemented behavior from unverified assumptions, and create traceable offline code tests before deliberate live verification. Preserve Vibe OIDC authentication throughout.

## Approach
Use one brownfield specification: characterize the implementation, generate reviewable requirements, analyze gaps, document design and then create an implementation/test task plan. Build offline tests with synthetic credentials and mocked external services before a separately enabled live smoke test.

The user approved this single-spec scope. The user explicitly clarified that Vibe OIDC is separate from API-key authentication: API-key authentication and fallback are excluded. An OpenCode placeholder `apiKey` is a client configuration value only; it is not an upstream credential.

Alternatives considered: live-first testing would not provide a deterministic baseline; replacing the authentication stack would expand scope before existing behavior is understood. Retaining the current stack has no static blocker to offline characterization. Dependency interoperability and live viability remain unverified; no external maintenance or compatibility assessment was performed.

## Scope
- **In**: Vibe browser OIDC/PKCE, token validation/storage/refresh, organization/workspace resolution, loopback proxy routes, streaming and tool-call passthrough, configuration, failure behavior, offline tests, and staged live verification.
- **Out**: Mistral API-key authentication or fallback, Responses API, new provider protocols, public hosting, broad agent compatibility claims, and live account activity during specification capture.

## Boundary Candidates
- Local proxy API, lifecycle and agent-facing compatibility.
- Vibe OIDC browser callback, exchange, validation and refresh.
- Shared credential storage, concurrency and tenant selection.
- HTTP transport, destination policy and verification infrastructure.

These are responsibility seams inside one spec, not independent new features.

## Out of Boundary
Mistral owns issuer registration, subscription entitlement, available models and remote service behavior. Agent clients own their configuration and protocol implementation. The proxy cannot establish those systems' compatibility from static inspection.

## Upstream / Downstream
- **Upstream**: Existing Vibe-derived authentication code, Mistral OIDC services and subscription inference endpoints, OS keyring, Python/uv, and client OpenAI-compatible transport.
- **Downstream**: Requirements, gap analysis, design, offline tests and an opt-in live smoke procedure; later authentication-copy upgrades require regression verification.

## Existing Spec Touchpoints
- **Extends**: None; no existing spec.
- **Adjacent**: Installed Vibe can share the keyring slot but is not modified by this work.

## Constraints
- Use Vibe's separate browser OIDC flow; do not introduce API-key authentication or fallback.
- Preserve loopback binding and avoid real credentials in fixtures or artifacts.
- Tests must isolate the real keyring, browser and remote network by default.
- Changes to Vibe-derived modules must preserve license attribution and document deviations.
- English is the discovery language; set formal spec language to `en` during initialization.
- Human review remains required for Requirements -> Design -> Tasks -> Implementation. Discovery approval is not approval of unwritten downstream artifacts.

## Discovery Status
Discovery scope approved and static exploration completed on 2026-10-31. This directory is a discovery handoff, not a generated or approved requirements/design/task specification. Next: initialize the spec, then generate requirements from this brief.
