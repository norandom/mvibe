[CmdletBinding()]
param(
    [Alias('Host')][string]$BindHost = '127.0.0.1',
    [string]$Uv = 'uv',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ProxyArgs,
    [switch]$Help
)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Output 'Start the local Vibe proxy: ./start_proxy.ps1 [-Host 127.0.0.1|0.0.0.0] [-Uv EXECUTABLE] [proxy.py arguments]'
    Write-Output 'Binds to loopback by default; -Host 0.0.0.0 exposes all interfaces and prints a warning, because the proxy has no caller authentication.'
    Write-Output 'Remaining arguments are forwarded to proxy.py: --port 8765, --auth-source existing-vibe|oidc, --login, --log.'
    Write-Output 'Requires native uv. Runs the locked project environment with the proxy extra: uv run --locked --extra proxy -- python proxy.py.'
    exit 0
}
try {
    $uvCommand = Get-Command -Name $Uv -CommandType Application -ErrorAction Stop | Select-Object -First 1
    if ($uvCommand.Source -match '[\\/]WindowsApps[\\/]') { throw 'A native uv executable is required.' }
    & $uvCommand.Source run --locked --extra proxy -- python (Join-Path $PSScriptRoot 'proxy.py') --host $BindHost @ProxyArgs
    exit $LASTEXITCODE
} catch {
    Write-Error 'Proxy could not start. Supply a native uv executable with -Uv; use -Help for options.'
    exit 1
}
