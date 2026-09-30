param([string]$DemoName = 'cctv-demo')
$ErrorActionPreference = 'Stop'
if ($DemoName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$') {
    throw 'DemoName must contain only ASCII letters, digits, underscores or hyphens.'
}
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$demoRoot = Join-Path (Join-Path $repositoryRoot 'outputs') $DemoName
if (Test-Path -LiteralPath $demoRoot) {
    throw 'Demo folder already exists. Use a new DemoName; original fixtures are never overwritten.'
}
$ffmpegExecutable = (Get-Command ffmpeg -ErrorAction Stop).Source
$inputs = Join-Path $demoRoot 'inputs'
New-Item -ItemType Directory -Path $inputs -Force | Out-Null
$syntheticVideo = Join-Path $inputs 'synthetic-road.avi'
& $ffmpegExecutable -nostdin -v error -f lavfi -i 'testsrc2=size=160x90:rate=10' -t 1.6 -an -c:v mjpeg -threads 1 -q:v 3 $syntheticVideo
if ($LASTEXITCODE -ne 0) { throw 'Synthetic video generation failed.' }
$manifest = [ordered]@{
    schema_version = 1
    raw_root = 'inputs'
    cameras = @([ordered]@{ camera_id = 'CAM_DEMO'; name = 'Synthetic test camera' })
    videos = @([ordered]@{ video_id = 'DEMO_ROAD'; camera_id = 'CAM_DEMO'; path = 'synthetic-road.avi' })
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $demoRoot 'manifest.json') -Encoding utf8
Write-Host "Synthetic fixture created at $demoRoot. This is not real CCTV or a traffic benchmark."
