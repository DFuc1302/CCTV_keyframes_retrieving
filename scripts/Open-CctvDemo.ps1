param([ValidateRange(1024,65535)][int]$Port = 8080)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$url = "http://127.0.0.1:$Port"
try {
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\Setup-Windows.ps1 -Vision once before opening the demo.' }
    $existing = $false
    try { $null = Invoke-RestMethod "$url/cctv/workflow" -TimeoutSec 2; $existing = $true } catch { }
    if (-not $existing) {
        $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$Port)
        $probe.Server.ExclusiveAddressUse = $true
        try { $probe.Start() } finally { $probe.Stop() }
        $logs = Join-Path $root 'outputs\tests'
        New-Item -ItemType Directory -Path $logs -Force | Out-Null
        $token = [Guid]::NewGuid().ToString('N')
        # Start-Process rejects duplicate PATH/Path keys inherited from some hosts.
        $launcherPath = $env:Path
        Remove-Item Env:\PATH -ErrorAction SilentlyContinue
        $env:Path = $launcherPath
        $env:CCTV_PORT = "$Port"
        Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.cctv:create_app','--factory','--host','127.0.0.1','--port',"$Port",'--workers','1') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "launcher-$token.out.txt") -RedirectStandardError (Join-Path $logs "launcher-$token.err.txt") | Out-Null
        $until = (Get-Date).AddSeconds(120)
        do {
            try { $null = Invoke-RestMethod "$url/cctv/workflow" -TimeoutSec 2; $existing = $true } catch { Start-Sleep -Milliseconds 500 }
        } until ($existing -or (Get-Date) -gt $until)
        if (-not $existing) { throw "Server did not start. See outputs\tests\launcher-$token.err.txt" }
    }
    Start-Process $url
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message,'CCTV demo') | Out-Null
    exit 1
}
