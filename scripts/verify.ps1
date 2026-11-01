<#
.SYNOPSIS
Run mandatory offline acceptance using explicitly prepared tools.
.EXAMPLE
pwsh -NoProfile -File scripts/verify.ps1 -Bun C:/tools/bun.exe -HostBinary C:/tools/opencode.exe
.DESCRIPTION
Prepare the locked uv environment, exact Bun and pinned isolated host separately.
Python uses two pytest-xdist workers; -PythonWorkers 0 enables serial debugging.
This command installs nothing from the network and runs no live account checks.
Each invocation writes reports/verification/<run-id>/acceptance.json with named
executed test outcomes, mandatory step results and required documentary evidence.
Exit 1 means a mandatory check failed or required evidence is missing/unverified.
Required documents owned by later implementation tasks remain acceptance blockers.
#>
param(
    [Parameter(Mandatory = $true)][string]$Bun,
    [Parameter(Mandatory = $true)][string]$HostBinary,
    [string]$ReportRoot = (Join-Path $PSScriptRoot '../reports/verification'),
    [ValidateRange(0, 64)][int]$PythonWorkers = 2
)
# Run only prepared tools. Online setup and current-stable discovery are separate actions.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$provider = Join-Path $root 'packages/opencode-vibe-oidc'
$runId = [guid]::NewGuid().ToString('N')
$directory = Join-Path ([IO.Path]::GetFullPath($ReportRoot)) $runId
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$started = [DateTimeOffset]::UtcNow.ToString('o')
$steps = [System.Collections.Generic.List[object]]::new()
$savedEnvironment = @{}
foreach ($key in @('VIBE_TEST_UV_PYTHON', 'VIBE_TEST_PYTHON', 'OPENCODE_TEST_BINARY', 'VIBE_HOST_REPORT', 'MVIBE_STARTUP_REPORT', 'UV_OFFLINE', 'BUN_CONFIG_NO_CLEAR_TERMINAL', 'OPENCODE_DISABLE_AUTOUPDATE')) {
    $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
function Invoke-Check([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory) {
    $code = 1
    Push-Location $WorkingDirectory
    try {
        Write-Host "Running mandatory offline check: $Name"
        & $Executable @Arguments
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 1 }
    } catch { Write-Host "Unable to execute $Name; prepare the documented tools and dependencies first." }
    finally { Pop-Location }
    $steps.Add(@{name=$Name; exit_code=$code; status=$(if ($code -eq 0) {'passed'} else {'failed'})})
}
Push-Location $root
try {
    $uv = (Get-Command uv -CommandType Application -ErrorAction Stop).Source
    $Bun = (Resolve-Path -LiteralPath $Bun -ErrorAction Stop).Path
    $HostBinary = (Resolve-Path -LiteralPath $HostBinary -ErrorAction Stop).Path
    $env:UV_OFFLINE = 'true'
    $env:OPENCODE_DISABLE_AUTOUPDATE = 'true'
    $env:BUN_CONFIG_NO_CLEAR_TERMINAL = 'true'
    # Resolve only through uv; never use Windows Store/bare Python aliases.
    $runtime = & $uv run --locked --offline python -c 'import sys; print(sys.executable)'
    if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathFullyQualified([string]$runtime) -or -not (Test-Path -LiteralPath $runtime)) {
        throw 'Prepare the locked uv-managed environment first.'
    }
    $env:VIBE_TEST_UV_PYTHON = [string]$runtime
    $env:VIBE_TEST_PYTHON = [string]$runtime
    $env:OPENCODE_TEST_BINARY = $HostBinary
    $env:VIBE_HOST_REPORT = Join-Path $directory 'host.json'
    $env:MVIBE_STARTUP_REPORT = Join-Path $directory 'startup.json'
    # No install command: compare prepared package metadata with exact declarations.
    $toolCheck = @'
const fs = require("node:fs");
const p = JSON.parse(fs.readFileSync("package.json", "utf8"));
const lock = JSON.parse(fs.readFileSync("bun.lock", "utf8").replace(/,\s*([}\]])/g, "$1"));
const declared = {...p.dependencies, ...p.devDependencies};
const locked = {...lock.workspaces[""].dependencies, ...lock.workspaces[""].devDependencies};
if ("bun@" + Bun.version !== p.packageManager) throw Error("Prepare pinned Bun");
if (Object.keys(declared).length !== Object.keys(locked).length) throw Error("Manifest/lock mismatch");
for (const [name, version] of Object.entries(declared)) {
 if (locked[name] !== version || lock.packages[name][0] !== name + "@" + version) throw Error("Manifest/lock mismatch");
 if (JSON.parse(fs.readFileSync("node_modules/" + name + "/package.json", "utf8")).version !== version) throw Error("Prepare locked dependencies");
}
console.log("Prepared locked provider tool versions PASS");
'@
    Invoke-Check 'tools' $Bun @('--no-install', '-e', $toolCheck) $provider
    Invoke-Check 'python' $uv @('run', '--locked', '--offline', 'pytest', '-n', [string]$PythonWorkers, '--dist=loadscope', "--junitxml=$(Join-Path $directory 'python.xml')") $root
    Invoke-Check 'provider' $Bun @('--no-install', 'test', '--reporter=junit', "--reporter-outfile=$(Join-Path $directory 'provider.xml')") $provider
    Invoke-Check 'typecheck' $Bun @('--no-install', 'run', 'typecheck') $provider
    Invoke-Check 'provider-build' $Bun @('--no-install', 'run', 'build') $provider
    Invoke-Check 'python-build' $uv @('build', '--offline', '--no-build-isolation') $root
    Invoke-Check 'bridge-process' $Bun @('--no-install', 'run', 'test/bridge-process.ts') $provider
    Invoke-Check 'bridge-root-exit' $Bun @('--no-install', 'run', 'test/bridge-root-exit.ts') $provider
    Invoke-Check 'host-setup' $Bun @('--no-install', 'run', 'test:host') $provider
    Invoke-Check 'host' $Bun @('--no-install', 'run', 'test:consumer-host') $provider
    Invoke-Check 'tool-baseline' $Bun @('--no-install', 'run', 'test:tool-baseline', '--output', (Join-Path $directory 'tool-baseline')) $provider
    @{run_id=$runId; mode='offline'; started=$started; steps=@($steps.ToArray())} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $directory 'execution.json') -Encoding utf8
    & $uv run --locked --offline python (Join-Path $PSScriptRoot 'verification.py') --reports $directory
    $result = $LASTEXITCODE
    Write-Host "Acceptance evidence: $(Join-Path $directory 'acceptance.json')"
    exit $result
} catch {
    @{kind='offline-acceptance'; status='failed'; reason='Prepare uv, exact Bun, locked dependencies and isolated host before offline verification'; live='unverified'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory 'acceptance.json') -Encoding utf8
    Write-Host "Offline verification failed: prepare uv, exact Bun, locked dependencies and isolated host. Report: $directory/acceptance.json"
    exit 1
} finally {
    foreach ($key in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process') }
    Pop-Location
}
