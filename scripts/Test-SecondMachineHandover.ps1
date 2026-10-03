param(
    [Parameter(Mandatory = $true)][string]$ReferenceInventory,
    [Parameter(Mandatory = $true)][string]$OriginMachine,
    [string]$Manifest = 'configs/cctv.demo.json',
    [string]$Output,
    [string]$ExpectedCommit
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run scripts\Setup-Windows.ps1 first.' }
# Resolve caller-supplied paths before entering the repository.
$ReferenceInventory = [IO.Path]::GetFullPath($ReferenceInventory)
if ($Output) { $Output = [IO.Path]::GetFullPath($Output) }
if ($Manifest -ne 'configs/cctv.demo.json') { $Manifest = [IO.Path]::GetFullPath($Manifest) }
$arguments = @('-m', 'scripts.verify_second_machine_handover',
    '--reference-inventory', $ReferenceInventory, '--origin-machine', $OriginMachine,
    '--manifest', $Manifest, '--powershell-version', $PSVersionTable.PSVersion.ToString(),
    '--powershell-executable', (Get-Process -Id $PID).Path)
if ($Output) { $arguments += @('--output', $Output) }
if ($ExpectedCommit) { $arguments += @('--expected-commit', $ExpectedCommit) }
Push-Location $repositoryRoot
try {
    & $venvPython @arguments
    $handoverExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $handoverExitCode
