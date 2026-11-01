# Set up Mistral-Vibe in OpenCode Desktop

Use this guide after normal Vibe browser sign-in. mvibe adds the server-side
provider; Desktop and the CLI load it from the same global OpenCode configuration.
There is no second Desktop credential store and no proxy requirement.

OpenCode documents this shared plugin loading behavior in its
[Desktop plugin guide](https://dev.opencode.ai/v2/docs/build/plugins/gui).
The local verification for this guide used the installed Desktop bundled CLI
and background service, both reporting OpenCode 2.2.5.

## Prepare a native login

Finish sign-in through normal `vibe` under the same OS and user account that runs
Desktop. The provider reads that native keyring credential. It never falls back
to an environment API key. A login inside WSL belongs to the Linux environment;
it does not automatically sign in native Windows Desktop.

Vibe's project says Windows works while official support targets Unix. On
Windows, use its [uv installation instructions](https://docs.mistral.ai/vibe/code/cli/install-setup)
for initial sign-in. OpenCode then provides the daily Desktop/CLI interface.
The [authentication writeup](vibe-authentication.md) explains the normal browser
credential and the separate experimental OIDC route.

## Choose interactive or unattended setup

From the extracted mvibe directory or checkout, guided PowerShell setup is:

```powershell
./install.ps1 -Interactive
```

Choose target `desktop` or `both`, auth source `existing-vibe`, and optionally
`vibe-oidc/mistral-large-4` as the global default. Connect and restart are choices,
not automatic side effects. The installer shows a summary before saving.

For repeatable unattended setup:

```powershell
./install.ps1 -Auto -Target Desktop -AuthSource existing-vibe -Model vibe-oidc/mistral-large-4 -Connect -Restart
```

Shell equivalent:

```bash
bash ./install.sh --yes --target desktop --auth-source existing-vibe --model vibe-oidc/mistral-large-4 --connect --restart
```

The target changes setup guidance and executable discovery; it does not isolate
Desktop and CLI preferences. The model setting is global to that server.
Omit the model option to preserve the existing default. Explicit auth-source
selection migrates the owned plugin entry; an omitted selection preserves it.
No prompt waits for input in a non-terminal run. Explicit interactive mode
requires a terminal; do not combine it with unattended mode.

Restart runs before connect so the background service can load the plugin.
Both are opt-in and make no inference request in existing-login mode. Restart
can interrupt active work. If a post-install action fails, the installer reports
that configuration was already saved and which operation needs retrying.

## Use Desktop's bundled CLI when needed

On Windows, the installer can discover Desktop's bundled CLI when no `opencode`
is on PATH and the target is `desktop` or `both`. Its usual per-user path is:

```text
%LOCALAPPDATA%\Programs\OpenCode\resources\opencode-cli.exe
```

For another location, pass `-OpenCode` or `--opencode`. On platforms where bundled
CLI discovery is unavailable, use an installed CLI or provide its executable.
uv and Bun must also be native executables. Their paths can be supplied explicitly.

The local Windows command is shown here so it can be repeated without relying
on PATH. Run it from `D:\Source\mvibe`:

```powershell
./install.ps1 -Auto -Target Desktop -AuthSource existing-vibe -Model vibe-oidc/mistral-large-4 -Connect -Restart -SkipPrepare -Bun "$env:USERPROFILE/.bun/bin/bun.exe" -Uv "$env:LOCALAPPDATA/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe/uv.exe" -OpenCode "$env:LOCALAPPDATA/Programs/OpenCode/resources/opencode-cli.exe"
```

`-SkipPrepare` is appropriate only for this already prepared checkout. A new
installation should omit it. The uv path above reflects this machine's WinGet
installation, not a universal Windows location.

The unattended command completed successfully here on 2026-11-01. The backend
then reported provider `vibe-oidc` and model `mistral-large-4` as its default,
with that selection present in the authenticated catalog. No inference request
or UI automation was used. An existing Desktop draft may still keep its model.

## Check the shared backend without a model call

PowerShell can use the bundled executable for these read-only operations:

```powershell
$oc = "$env:LOCALAPPDATA/Programs/OpenCode/resources/opencode-cli.exe"
& $oc debug paths config
$providers = & $oc api get /api/provider | ConvertFrom-Json
$providers.data | Where-Object id -eq 'vibe-oidc' | Select-Object id, activation
$models = & $oc api get /api/model | ConvertFrom-Json
$models.data | Where-Object providerID -eq 'vibe-oidc' | Select-Object providerID, id, name
& $oc api get /api/model/default
```

The provider should be enabled, your selected model should be available, and
the default model response should identify `vibe-oidc` and the chosen model ID.
The example Large 4 ID was present in this account's authenticated catalog;
availability can differ by account or service version.

The installer asks the CLI for the actual global config directory, preferring
an existing `opencode.jsonc` over `opencode.json`. It edits owned JSONC paths,
retains unrelated data and comments, and makes a backup before changes. The
local service loaded both files under `~/.config/opencode`, with JSONC later in
the config order. Use `-ConfigPath` only when you know the selected server will
load that file. A remote or differently configured server has its own setup.

## New sessions and existing drafts

Desktop gives an explicit draft or session model choice precedence over the
global default. Its source also considers agent configuration and recent models.
Setting the global model does not overwrite those existing choices.

After setup, start a new session without a saved model override, or change an
existing session's model to `Mistral-Vibe`. A new default of Large 4 does not
prove that an already-open OpenCode Go draft switched. This guide does not edit
opaque Desktop preference stores to force that switch.

The backend checks above verify provider loading, model availability and the
server default. They do not establish the currently displayed model in a Desktop
window. No UI automation is needed for installation or those checks.

## Context compression belongs to the client

Native Vibe performs a separate summarization request for context compaction;
it can use a configured compaction model on the active provider. Its first
attempt reuses the prompt prefix, with a fallback summarizer request when needed.
See [Vibe's compaction behavior](https://github.com/mistralai/mistral-vibe#compaction-and-output-limits).

Using mvibe in OpenCode reuses authentication, not the Vibe agent runtime.
OpenCode decides when to compact and asks the model provider to generate a
summary. It then stores and uses that summary. The mvibe provider and proxy
forward the summarization request; neither generates the summary locally or
inserts an extra native Vibe compression step.
Those requests use the provider OpenCode selects; mvibe does not force a request
for another provider through Mistral or change its authentication.
Accurate model limits still matter to OpenCode, which is why authenticated model
discovery and catalog metadata are checked separately from credential presence.
