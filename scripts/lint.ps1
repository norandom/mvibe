[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Import-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -ErrorAction Stop
$lintRoot = Split-Path -Parent $PSScriptRoot
$lintFiles = @((Join-Path $lintRoot 'install.ps1')) + @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' | Select-Object -ExpandProperty FullName)
$findings = @($lintFiles | ForEach-Object { Invoke-ScriptAnalyzer -Path $_ -Settings (Join-Path $lintRoot 'PSScriptAnalyzerSettings.psd1') })
if ($findings.Count -gt 0) {
    $findings | Format-Table -AutoSize
    exit 1
}
Write-Output 'PowerShell lint passed.'
