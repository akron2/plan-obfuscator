$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    py -3.13 -m venv .venv
}
& $python -m pip install --require-hashes -r requirements.lock
& $python -m pip install -e ".[build]"
& $python -m PyInstaller --clean --noconfirm .\PlanObfuscator.spec

Write-Host "Built: $projectRoot\dist\PlanObfuscator.exe"

