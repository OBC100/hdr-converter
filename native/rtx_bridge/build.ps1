# 编译 hdr_rtx_bridge.dll
param(
    [ValidateSet("Debug", "Release")]
    [string]$Config = "Release"
)

$ErrorActionPreference = "Stop"
if (-not $env:NV_RTX_VIDEO_SDK) {
    Write-Error "请先设置 NV_RTX_VIDEO_SDK 指向 RTX Video SDK 根目录"
}

$root = $PSScriptRoot
$build = Join-Path $root "build"
$out = Join-Path $root "out"
New-Item -ItemType Directory -Force -Path $build, $out | Out-Null

Push-Location $build
try {
    cmake .. -DCMAKE_BUILD_TYPE=$Config
    cmake --build . --config $Config
    $dll = Get-ChildItem -Recurse -Filter "hdr_rtx_bridge.dll" | Select-Object -First 1
    if (-not $dll) { Write-Error "未找到 hdr_rtx_bridge.dll" }
    Copy-Item $dll.FullName (Join-Path $out "hdr_rtx_bridge.dll") -Force
    Write-Host "OK: $($out)\hdr_rtx_bridge.dll"
}
finally {
    Pop-Location
}
