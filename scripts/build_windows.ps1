$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $projectRoot

python -m pip install -e ".[build]"
python -m PyInstaller --clean --noconfirm .\PlanObfuscator.spec

Write-Host "Built: $projectRoot\dist\PlanObfuscator.exe"

