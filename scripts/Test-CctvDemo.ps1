param(
    [ValidateRange(1024,65535)][int]$Port = 8080,
    [ValidateSet('cuda','cpu')][string]$Device = 'cuda',
    [ValidateRange(1,600)][int]$StartupTimeout = 120,
    [string]$Output = '',
    [switch]$ExistingServer,
    [switch]$RequireOverlays
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run scripts\Setup-Windows.ps1 first.' }
$runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
if (-not $Output) { $Output = "outputs/demo-data/demo-smoke-$runId" }
$testLog = Join-Path $repositoryRoot "outputs/tests/demo-smoke-$runId.txt"
$smokeArguments = @('-m', 'scripts.run_cctv_demo_smoke', '--port', "$Port", '--device', $Device,
    '--startup-timeout', "$StartupTimeout", '--output', $Output)
if ($ExistingServer) { $smokeArguments += '--existing-server' }
if ($RequireOverlays) { $smokeArguments += '--require-overlays' }
Push-Location $repositoryRoot
try {
    Write-Host "CCTV HTTP smoke log: $testLog"
    & $venvPython (Join-Path $PSScriptRoot 'run_python_logged.py') --log $testLog -- @smokeArguments
    $smokeExitCode = $LASTEXITCODE
    Get-Content -LiteralPath $testLog -Encoding UTF8 -Tail 22
    if ($smokeExitCode -ne 0) { throw "Demo smoke failed ($smokeExitCode); see $testLog" }
} finally {
    Pop-Location
}
