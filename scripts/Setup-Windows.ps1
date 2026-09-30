param([switch]$Vision)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'

function Invoke-PythonChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

if ($env:OS -ne 'Windows_NT') { throw 'This setup supports native Windows only.' }
if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-PythonChecked -Executable 'py' -Arguments @('-3.11', '-m', 'venv', (Join-Path $repositoryRoot '.venv'))
}
Invoke-PythonChecked -Executable $venvPython -Arguments @(
    '-c', 'import sys; assert sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32'
)
Invoke-PythonChecked -Executable $venvPython -Arguments @(
    '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip', 'setuptools', 'wheel'
)
$lockPath = Join-Path $repositoryRoot 'requirements\windows-py311-cu126.lock.txt'
if (Test-Path -LiteralPath $lockPath) {
    Invoke-PythonChecked -Executable $venvPython -Arguments @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', $lockPath,
        '--extra-index-url', 'https://download.pytorch.org/whl/cu126'
    )
} else {
    Invoke-PythonChecked -Executable $venvPython -Arguments @(
        '-m', 'pip', 'install', '--disable-pip-version-check', 'torch==2.7.1', 'torchvision==0.22.1',
        '--index-url', 'https://download.pytorch.org/whl/cu126'
    )
    Invoke-PythonChecked -Executable $venvPython -Arguments @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r',
        (Join-Path $repositoryRoot 'requirements\windows-dev.txt')
    )
}
if ($Vision) {
    Invoke-PythonChecked -Executable $venvPython -Arguments @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r',
        (Join-Path $repositoryRoot 'requirements\windows-vision.txt')
    )
}
Invoke-PythonChecked -Executable $venvPython -Arguments @('-m', 'pip', 'check')
Invoke-PythonChecked -Executable $venvPython -Arguments @(
    (Join-Path $PSScriptRoot 'verify_runtime.py'), '--require-cuda'
)
Write-Host "Environment ready: $venvPython"
