param([string]$PythonExecutable = '', [string]$DataRoot = '')
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonExecutable) {
    $localPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
    $PythonExecutable = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}
$scriptPath = Join-Path $PSScriptRoot 'preflight_windows.py'
$preflightArguments = @($scriptPath, '--save')
if ($DataRoot) { $preflightArguments += @('--data-root', $DataRoot) }
& $PythonExecutable @preflightArguments
if ($LASTEXITCODE -ne 0) { throw "Preflight failed with exit code $LASTEXITCODE" }
Write-Host "Diagnostics saved under $repositoryRoot\outputs\preflight. Serving readiness is not verified."
