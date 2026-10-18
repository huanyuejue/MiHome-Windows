# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MiHome-Windows contributors
<#
.SYNOPSIS
    把 dist\ 的构建产物打成 Windows 安装包（Setup.exe）
.DESCRIPTION
    1. 确认 dist\MiHome-Windows.exe 存在（不存在则提示先跑 build.ps1）
    2. 自动查找 Inno Setup 的 iscc.exe
    3. 用 installer.iss 编译出 dist\installer\MiHome-Windows-Setup-<版本>.exe
.EXAMPLE
    .\make_installer.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# ---- 1. 构建产物检查 ----
$Exe = Join-Path $Root "dist\MiHome-Windows.exe"
if (-not (Test-Path $Exe)) {
    Write-Host "[ERROR] 找不到 dist\MiHome-Windows.exe" -ForegroundColor Red
    Write-Host "请先构建： .\build.ps1" -ForegroundColor Yellow
    exit 1
}

# ---- 2. 版本号取自 app/__init__.py，保持单一信源 ----
$AppVersion = (Select-String -Path "app\__init__.py" -Pattern '^__version__\s*=\s*"(.+?)"').Matches[0].Groups[1].Value
Write-Host "版本: $AppVersion" -ForegroundColor Gray

# ---- 3. 定位 Inno Setup 编译器 ----
$Candidates = @(
    "${Env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$Env:ProgramFiles\Inno Setup 7\ISCC.exe",
    "${Env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$Env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${Env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
)
$Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    # 可能在别的盘/别的版本：先问命令，再按"各盘根目录下的 Inno Setup*"扫
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) {
        $Iscc = $cmd.Source
    } else {
        foreach ($drive in @("C", "D", "E", "F")) {
            $hit = Get-ChildItem "${drive}:\" -Directory -Filter "Inno Setup*" -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "ISCC.exe" } |
                Where-Object { Test-Path $_ } |
                Select-Object -First 1
            if ($hit) { $Iscc = $hit; break }
        }
    }
}
if (-not $Iscc) {
    Write-Host "[ERROR] 找不到 Inno Setup 的 ISCC.exe" -ForegroundColor Red
    Write-Host ""
    Write-Host "安装 Inno Setup 7 或 6（免费，约 10MB）：" -ForegroundColor Yellow
    Write-Host "  https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "或用 winget： winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "装好重跑： .\make_installer.ps1" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "（不想装安装包工具也行：dist\ 整个目录就是免安装版，" -ForegroundColor Gray
    Write-Host "  打 zip 发给对方，解压后直接运行 MiHome-Windows.exe）" -ForegroundColor Gray
    exit 1
}
Write-Host "Inno Setup: $Iscc" -ForegroundColor Green

# ---- 4. 编译安装包 ----
Write-Host "`n正在生成安装包..." -ForegroundColor Cyan
& $Iscc "/DMyAppVersion=$AppVersion" "installer.iss"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 生成安装包失败" -ForegroundColor Red
    exit 1
}

$Setup = Join-Path $Root "dist\installer\MiHome-Windows-Setup-$AppVersion.exe"
if (Test-Path $Setup) {
    $Size = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
    Write-Host ("`n生成成功: {0} ({1} MB)" -f $Setup, $Size) -ForegroundColor Green
} else {
    Write-Host "[ERROR] 未生成安装包文件" -ForegroundColor Red
    exit 1
}
