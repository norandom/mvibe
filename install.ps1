[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$Uv = 'uv',
    [string]$Bun = 'bun',
    [string]$OpenCode = 'opencode',
    [switch]$SkipPrepare,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Output 'Install direct Mistral-Vibe: ./install.ps1 [-ConfigPath FILE] [-Uv EXECUTABLE] [-Bun EXECUTABLE] [-OpenCode EXECUTABLE] [-SkipPrepare]'
    Write-Output 'Requires native uv, Bun 1.4.4 and OpenCode 2. Default preparation may download locked dependencies. No login or restart is performed.'
    exit 0
}
try {
    $nativeBun = (Get-Command -Name $Bun -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    if ($nativeBun -match '[\\/]WindowsApps[\\/]') { throw 'A native Bun executable is required.' }
    $installerArgs = @((Join-Path $PSScriptRoot 'packages/opencode-vibe-oidc/scripts/install.ts'), '--uv', $Uv, '--bun', $nativeBun, '--opencode', $OpenCode)
    if ($ConfigPath) { $installerArgs += @('--config-path', $ConfigPath) }
    if ($SkipPrepare) { $installerArgs += '--skip-prepare' }
    & $nativeBun @installerArgs
    exit $LASTEXITCODE
} catch {
    Write-Error 'Installer could not start. Supply a native Bun 1.4.4 executable with -Bun; use -Help for options.'
    exit 1
}
