# Requirements Document

## Introduction
The `vibe-oidc` OpenCode provider plugin (`packages/opencode-vibe-oidc`) must activate promptly and predictably. Live evidence from 2026-11-08 (host OpenCode 2.2.6, installed release v1.1.0): the provider appeared only 7–15 minutes after service boot, or not at all, because plugin setup blocks on the first model-catalog refresh — a chain that spawns the authentication helper, reads stored credentials and performs an upstream model-list request with no deadline — while failures are replaced by a generic message and real diagnostics are invisible without special CLI flags. This feature makes registration independent of that chain, bounds every discovery attempt, and makes failure causes visible, so a slow, hung, or failing catalog never prevents the provider from registering and never hides the actual error.

## Boundary Context (Optional)
- **In scope**: Provider-plugin activation and registration behavior, model-catalog refresh timing and fallback, failure diagnostics visibility, and degraded-mode behavior of `packages/opencode-vibe-oidc`.
- **Out of scope**: Proxy behavior (`proxy.py`, `mvibe_proxy/`); the authentication flows themselves (sign-in, credential renewal, keyring handling — owned by `mvibe_auth` and Vibe); OpenCode host plugin-loading semantics; the authentication helper protocol and its version-coupling rules; changes to the user's installed environment (updated only from a published release per the standing 2026-11-03 rule).
- **Adjacent expectations**: The authentication helper keeps its existing bounded request protocol; the host continues to publish a provider only after its plugin setup completes (therefore Requirement 1 must not depend on host changes).

## Requirements

### Requirement 1: Prompt, network-independent provider registration
**Objective:** As an OpenCode user, I want the Mistral-Vibe provider to appear immediately when OpenCode starts, so that I never wait minutes for models or lose the provider entirely.

#### Acceptance Criteria
1. When the OpenCode host loads the provider plugin, the provider plugin shall complete provider registration without waiting for any upstream network request or authentication-helper process to finish.
2. When the host loads the plugin in a warm local environment, the provider plugin shall finish registration within 5 seconds, even while the upstream model endpoint is unreachable or slow.
3. When the provider registers before the first authenticated model discovery completes, the provider plugin shall present an empty model list together with a diagnostic stating that model discovery is pending, and the provider entry itself shall remain complete and selectable.
4. The provider plugin shall not delay or abandon provider registration based on model metadata availability or model discovery outcomes.

### Requirement 2: Bounded model discovery
**Objective:** As a user, I want every model discovery attempt to finish or fail within a known time, so a stalled upstream connection can never freeze provider activation.

#### Acceptance Criteria
1. While a model discovery attempt is in progress, the provider plugin shall enforce a deadline of at most 30 seconds per attempt.
2. If a discovery attempt exceeds its deadline, the provider plugin shall abandon that attempt, keep the last known good model list (or the baseline list), and record a diagnostic identifying the timeout.
3. When a discovery attempt succeeds, the provider plugin shall publish the refreshed model list without requiring a host restart or plugin reload.

### Requirement 3: Failure causes visible to the operator
**Objective:** As an operator, I want the real cause of a setup or discovery failure in the host log, so I can fix credential, network, or version problems without guessing.

#### Acceptance Criteria
1. If provider setup fails, the provider plugin shall record the failing step and the underlying cause at warning severity or higher in the host log.
2. If model discovery fails, the provider plugin shall record a diagnostic naming the failure class (for example timeout, unreachable, unauthorized, or invalid response) in the host log.
3. The provider plugin shall not replace the underlying failure cause with an unrelated generic message; generic guidance may accompany, but not substitute for, the recorded cause.
4. While recording failures and diagnostics, the provider plugin shall not include credentials, tokens, authorization headers, prompts, or conversation content.

### Requirement 4: Degraded mode without total outage
**Objective:** As a user, I want a usable, clearly-diagnosed provider even when authentication or discovery is broken, so my setup stays intact and recovers by itself.

#### Acceptance Criteria
1. If the authentication helper is unavailable, incompatible, or reports missing or expired credentials, the provider plugin shall still register the provider and record the authentication failure as a diagnostic.
2. When authentication or helper availability recovers (for example after a sign-in or reconnect), the provider plugin shall refresh the model catalog without requiring a host restart.
3. If a discovery attempt fails, the provider plugin shall retry discovery on the next trigger (credential or connection change, or plugin reload) rather than caching the failure permanently.

### Requirement 5: Compatibility and upgrade continuity
**Objective:** As a user upgrading OpenCode, I want the resilience behavior to arrive without protocol or installation changes, so the upgrade is low-risk.

#### Acceptance Criteria
1. The provider plugin shall deliver Requirements 1–4 without changes to the authentication helper protocol, its version-coupling rules, or the installed-release layout.
2. Where behavior depends on host plugin-loading semantics (for example publishing providers after setup completes), the provider plugin shall satisfy these requirements under the documented OpenCode 2.2.x host behavior without requiring host-side changes.
