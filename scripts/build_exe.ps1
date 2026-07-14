# 构建单文件 EXE（精简打包）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$env:PIP_CONFIG_FILE = Join-Path $Root "pip.conf"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 .venv，请先运行: python -m venv .venv"
}

& $Python -m pip install -r requirements.txt -e ".[build]" -i https://pypi.tuna.tsinghua.edu.cn/simple

# 清理旧产物
if (Test-Path "build\hdr_converter") { Remove-Item -Recurse -Force "build\hdr_converter" }
if (Test-Path "dist\JXR_HDR_Converter.exe") { Remove-Item -Force "dist\JXR_HDR_Converter.exe" }

& $PyInstaller --noconfirm --clean "hdr_converter.spec"

$Out = Join-Path $Root "dist\JXR_HDR_Converter.exe"
if (-not (Test-Path $Out)) {
    throw "打包失败：未生成 $Out"
}

$SizeMB = [math]::Round((Get-Item $Out).Length / 1MB, 1)
Write-Host ""
Write-Host "完成: $Out ($SizeMB MB)" -ForegroundColor Green
