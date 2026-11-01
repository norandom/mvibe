param(
    [string]$CaptureDir,
    [switch]$Oidc,
    [switch]$PortableStore,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$VibeArgs
)

$ErrorActionPreference = 'Stop'
# Resolve the uv-managed tool runtime explicitly; never use a Store alias or project Python.
$python = Join-Path $env:APPDATA 'uv/tools/mistral-vibe/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Installed uv mistral-vibe Python not found: $python"
}
$launcher = Join-Path $PSScriptRoot 'trace-native-vibe.py'
$arguments = @($launcher)
if ($Oidc) { $arguments += '--oidc' }
if ($PortableStore) { $arguments += '--portable-store' }
if ($CaptureDir) { $arguments += @('--capture-dir', $CaptureDir) }
if ($VibeArgs) { $arguments += '--'; $arguments += $VibeArgs }
& $python @arguments
exit $LASTEXITCODE
