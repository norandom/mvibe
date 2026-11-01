[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$Uv = 'uv',
    [string]$Bun = 'bun',
    [string]$OpenCode,
    [ValidateSet('cli', 'desktop', 'both')][string]$Target,
    [switch]$Interactive,
    [Alias('Yes')][switch]$Auto,
    [ValidateSet('existing-vibe', 'oidc')][string]$AuthSource,
    [string]$Model,
    [switch]$Connect,
    [switch]$Restart,
    [switch]$SkipPrepare,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Output 'Install direct Mistral-Vibe: ./install.ps1 [-Target cli|desktop|both] [-Interactive | -Auto (alias -Yes)] [-AuthSource existing-vibe|oidc] [-Model vibe-oidc/<model-id>] [-Connect] [-Restart] [-ConfigPath FILE] [-Uv EXECUTABLE] [-Bun EXECUTABLE] [-OpenCode EXECUTABLE] [-SkipPrepare]'
    Write-Output 'Requires native uv, Bun 1.4.4 and OpenCode 2. Preparation may download locked dependencies. Terminal-only prompts default on; -Auto disables them. CLI/Desktop share global config. Restart/connect require opt-in; OIDC connection may open a browser.'
    exit 0
}
try {
    if ($Interactive -and $Auto) {
        [Console]::Error.WriteLine('-Interactive and -Auto/-Yes cannot be combined.')
        exit 1
    }
    $nativeBun = (Get-Command -Name $Bun -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    if ($nativeBun -match '[\\/]WindowsApps[\\/]') { throw 'A native Bun executable is required.' }
    $installer = Join-Path $PSScriptRoot 'packages/opencode-vibe-oidc/scripts/install.js'
    if (-not (Test-Path -LiteralPath $installer)) { $installer = Join-Path $PSScriptRoot 'packages/opencode-vibe-oidc/scripts/install.ts' }
    $installerArgs = @($installer, '--uv', $Uv, '--bun', $nativeBun)
    if ($OpenCode) { $installerArgs += @('--opencode', $OpenCode) }
    if ($Target) { $installerArgs += @('--target', $Target.ToLowerInvariant()) }
    if ($AuthSource) { $installerArgs += @('--auth-source', $AuthSource.ToLowerInvariant()) }
    if ($PSBoundParameters.ContainsKey('Model')) { $installerArgs += @('--model', $Model) }
    if ($Interactive) { $installerArgs += '--interactive' }
    if ($Auto) { $installerArgs += '--yes' }
    if ($Connect) { $installerArgs += '--connect' }
    if ($Restart) { $installerArgs += '--restart' }
    if ($ConfigPath) { $installerArgs += @('--config-path', $ConfigPath) }
    if ($SkipPrepare) { $installerArgs += '--skip-prepare' }
    & $nativeBun @installerArgs
    exit $LASTEXITCODE
} catch {
    Write-Error 'Installer could not start. Supply a native Bun 1.4.4 executable with -Bun; use -Help for options.'
    exit 1
}
