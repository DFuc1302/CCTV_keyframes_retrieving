param([ValidateRange(1024,65535)][int]$Port = 8080, [ValidateSet('cuda','cpu')][string]$Device = 'cuda')
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run scripts\Setup-Windows.ps1 first.' }
$previousDevice = [Environment]::GetEnvironmentVariable('CCTV_DEVICE', 'Process')
Push-Location $repositoryRoot
try {
    $env:CCTV_DEVICE = $Device
    Write-Host "CCTV demo: http://127.0.0.1:$Port - Ctrl+C to stop."
    # One worker owns one CLIP instance. Never enable reload/multiple workers on 8GB.
    & $venvPython -m uvicorn app.cctv:create_app --factory --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) { throw "Server exited: $LASTEXITCODE" }
} finally {
    [Environment]::SetEnvironmentVariable('CCTV_DEVICE', $previousDevice, 'Process')
    Pop-Location
}
