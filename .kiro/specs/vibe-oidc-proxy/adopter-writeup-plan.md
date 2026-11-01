# COBESY adopter composition plan

Target behavior: an adopter installs the provider, reuses normal Vibe login,
connects Desktop/CLI and verifies a model choice without reading build internals.
Hidden assumption: installing a server provider must require a second Desktop
integration or a daily Vibe terminal workflow. Explain the shared config and
retain the initial same-OS Vibe sign-in prerequisite.
Anchor: the adopter following the first README command, with maintainers keeping
the installer help and guide consistent. No wider organization is inferred.

Governing idea: use normal Vibe authentication from OpenCode's supported interface
through one guided or unattended setup. Lead with that result, then prerequisites,
guided installation, explicit automation and first use. Put optional proxy and
builder work behind links. Treat Windows support accurately: Vibe works there
but officially targets Unix; do not claim Windows sign-in is unnecessary.

| Reader decision | Message at that point |
|---|---|
| Is this for me? | Desktop/CLI provider, native Windows use case, account-specific quota evidence |
| What must exist first? | OpenCode, uv, pinned Bun, normal same-OS Vibe login |
| Guide me or automate? | Interactive terminal flow versus explicit unattended flags |
| Why is Desktop still on Go? | Global default differs from a saved draft/session selection |
| How do I check it? | Supported read-only backend APIs, no model inference |
| Where are internals? | Linked auth, proxy and development guides |

Disproof condition: revise setup claims if Desktop loads a different server/config
or a fresh unselected draft does not use the verified global default. Do not treat
the backend check as a screenshot-level confirmation. The user forbids computer
use, so all remaining local setup and checks use files/CLI/service APIs.

Validation: keep the first screen adopter-focused, distinguish choices from
side effects, show copyable commands next to their explanations, keep model and
quota evidence scoped, verify all new flags, and apply the humanizer pass to
remove hype, decorative punctuation and generic tutorial endings.
