param([switch]$FullLegacy)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw 'Run scripts\Setup-Windows.ps1 first.' }
$testOutput = Join-Path $repositoryRoot 'outputs\tests'
New-Item -ItemType Directory -Path $testOutput -Force | Out-Null
$testEnvironment = @{
    DATA_ROOT = (Join-Path $testOutput 'data')
    KEYFRAMES_DIR = (Join-Path $testOutput 'keyframes')
    MAPFILES_DIR = (Join-Path $testOutput 'maps')
    VIDEOS_DIR = (Join-Path $testOutput 'videos')
    META_DB = (Join-Path $testOutput 'data\meta.db')
    FAISS_DIR = (Join-Path $testOutput 'data\faiss_index')
    ASR_CACHE_FILE = (Join-Path $testOutput 'data\asr_cache.json')
    BACKEND_MODE = 'sqlite'
    STORAGE_BACKEND = 'sqlite'
    AUTO_INDEX_ON_STARTUP = '0'
    ALLOW_HEAVY_RUNTIME_JOBS = '0'
    OCR_ON_DEMAND = '0'
    SERVE_READ_ONLY = '0'
    REQUIRE_SERVE_BUNDLE = '0'
    FEDERATED_DATASET_CONFIG = ''
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    HF_HOME = (Join-Path $testOutput 'model-cache\huggingface')
    TORCH_HOME = (Join-Path $testOutput 'model-cache\torch')
    YOLO_CONFIG_DIR = (Join-Path $testOutput 'ultralytics')
    MPLCONFIGDIR = (Join-Path $testOutput 'matplotlib')
    CLIP_BATCH_SIZE = '4'
    SEARCH_LOG_DIR = (Join-Path $testOutput 'telemetry')
}
$previousEnvironment = @{}
foreach ($name in $testEnvironment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location $repositoryRoot
try {
    foreach ($name in $testEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $testEnvironment[$name], 'Process')
    }
    foreach ($name in @('HF_HOME', 'TORCH_HOME', 'YOLO_CONFIG_DIR', 'MPLCONFIGDIR')) {
        New-Item -ItemType Directory -Path $testEnvironment[$name] -Force | Out-Null
    }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    & $venvPython (Join-Path $PSScriptRoot 'verify_runtime.py') --require-cuda
    if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }
    & $venvPython -m compileall -q app pipeline scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed.' }
    # Tests use isolated generated paths. This writable sandbox is not serving mode.
    if ($FullLegacy) {
        $testArguments = @('-m', 'unittest', 'discover', '-v')
        $testLog = Join-Path $testOutput 'legacy-tests.txt'
    } else {
        $testArguments = @('-m', 'unittest', '-v', 'test_ui_assets', 'test_folder_filter',
            'test_n_top_ocr', 'test_align_official_keyframe_maps', 'test_cctv_manifest',
            'test_cctv_ingestion', 'test_cctv_index', 'test_cctv_api', 'test_cctv_preview', 'test_cctv_traffic', 'test_cctv_detect', 'test_cctv_track', 'test_cctv_congestion', 'test_cctv_traffic_api', 'test_python_logged', 'test_powershell_scripts')
        $testLog = Join-Path $testOutput 'core-tests.txt'
    }
    # PS 5.1 converts redirected native stderr into NativeCommandError under Stop.
    # Capture it inside Python; warnings remain in the log and real failures stay nonzero.
    $loggedRunner = Join-Path $PSScriptRoot 'run_python_logged.py'
    & $venvPython $loggedRunner --log $testLog -- @testArguments
    $testExitCode = $LASTEXITCODE
    Get-Content -LiteralPath $testLog -Encoding UTF8 -Tail 18
    if ($testExitCode -ne 0) { throw "Tests failed; see $testLog" }
} finally {
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    Pop-Location
}
