# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 MiHome-Windows contributors
<#
.SYNOPSIS
    MiHome-Windows 一键构建脚本
.DESCRIPTION
    自动完成：venv 创建 → 依赖安装 → Nuitka 编译（MSVC）
    需要：Python 3.10+、Visual Studio 生成工具（MSVC）、Windows 10/11 SDK
    注意：项目路径必须纯 ASCII，否则依赖扫描阶段必崩
.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean
#>

param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ============================================================
# 0. 路径检查：必须是纯 ASCII 路径
# ============================================================
# Nuitka 在 Windows 上要调用 depends.exe 分析 DLL 依赖，而该工具会把
# 路径里的非 ASCII 字符写成 0x04 占位字节（实测中文目录名会变成
# 一串 \x04），随后 Nuitka 校验文件是否存在必然失败、当场断言崩溃。
# 这个限制没法用编码设置绕过，只能让构建发生在英文路径下。
if ($Root -match '[^\x20-\x7E]') {
    Write-Host "[ERROR] 项目路径包含非 ASCII 字符，Nuitka 无法在此路径下构建" -ForegroundColor Red
    Write-Host "        当前路径: $Root" -ForegroundColor Red
    Write-Host ""
    Write-Host "原因：Nuitka 依赖的 depends.exe 不支持非 ASCII 路径，构建会崩在依赖扫描阶段。" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "解决办法（任选其一）：" -ForegroundColor Yellow
    Write-Host "  1) 把整个项目复制/移动到纯英文路径再构建，例如：" -ForegroundColor Yellow
    Write-Host "       E:\build\mihome     （不要放在含中文的目录里）" -ForegroundColor Yellow
    Write-Host "  2) 把当前文件夹改名成英文（如 MiHome-Windows）后重开终端再构建" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  复制后记得把 .venv 一起带上（或在目标目录重新跑一次本脚本让它自动装依赖）" -ForegroundColor Gray
    exit 1
}

# ============================================================
# 0.5 Nuitka 缓存目录：放到项目内，避免用户目录不可写
# ============================================================
# Nuitka 默认缓存到 %LOCALAPPDATA%\Nuitka\Nuitka\Cache；在受限账户、
# 受管控的机器或某些沙箱下该目录不可写，构建会以
# "failed to create cache directory" 直接失败。放到项目内既绕开权限问题，
# 也让缓存跟着代码走（换目录不用重新编译一遍）。
# 外部若显式设置了 NUITKA_CACHE_DIR，尊重外部设置。
if (-not $env:NUITKA_CACHE_DIR) {
    $env:NUITKA_CACHE_DIR = Join-Path $Root ".nuitka-cache"
}
New-Item -ItemType Directory -Force -Path $env:NUITKA_CACHE_DIR | Out-Null

# ============================================================
# 1. 自动创建 venv 并安装依赖
# ============================================================
$Pip = Join-Path $Root ".venv\Scripts\pip.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Pip)) {
    Write-Host "[1/3] Creating virtual environment..." -ForegroundColor Cyan

    # 尝试 py launcher，失败则用 python
    $PyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($PyCmd) {
        & py -3 -m venv (Join-Path $Root ".venv")
    } else {
        & python -m venv (Join-Path $Root ".venv")
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 创建 venv 失败，请确认 Python 3 已安装并加入 PATH" -ForegroundColor Red
        exit 1
    }

    Write-Host "[2/3] Installing dependencies... (showing pip progress; a 'new version of pip' notice is informational only)" -ForegroundColor Cyan
    # 不用 --quiet：依赖下载可达数百 MB，静默会让人误以为卡死；
    # pip 的升级提示/进度条直接透传
    & $Python -m pip install -e $Root
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 安装依赖失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "[3/3] 初始化完成`n" -ForegroundColor Green
} else {
    Write-Host "[OK] Virtual environment ready" -ForegroundColor Green
}

# ============================================================
# 2. 激活 MSVC 编译环境（自动定位，兼容不同 VS 版本/安装盘）
# ============================================================
# 以前这里写死 "C:\Program Files (x86)\...\2022\BuildTools\vcvars64.bat"：
# 装在别的版本（如 VS 18）或别的盘符时直接报"未找到"，让人误以为必须
# 重装成 2022。改为先问 vswhere，再按常见模式兜底扫描。
function Find-VcVars {
    # 1) 环境里已有（在 VS 开发者命令提示符里启动本脚本时）
    if ($Env:VSINSTALLDIR) {
        $candidate = Join-Path $Env:VSINSTALLDIR "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path $candidate) { return $candidate }
    }
    # 2) vswhere：微软官方定位工具，随 VS Installer 安装
    $vswhere = @(
        "${Env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe",
        "$Env:ProgramFiles\Microsoft Visual Studio\Installer\vswhere.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($vswhere) {
        # 2a) 先取安装根目录再拼路径：`-find` 在部分版本（如 VS 18）
        #     会返回空，而 `-property installationPath` 稳定可用。
        #     **必须加 -utf8**：VS 可能装在中文路径下（本机在
        #     D:\常用软件\Microsoft Visual Studio\...），不加时输出被按
        #     GBK 解码成乱码，拼出的路径 Test-Path 必为假。
        $installs = & $vswhere -utf8 -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
        foreach ($inst in @($installs)) {
            if (-not $inst) { continue }
            $candidate = Join-Path $inst.Trim() "VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $candidate) { return $candidate }
        }
        # 2b) 再试 -find（老版本 VS 只有这条路可靠）
        $found = & $vswhere -utf8 -products * -latest -find "VC\Auxiliary\Build\vcvars64.bat" 2>$null
        if ($found) { return ($found | Select-Object -First 1) }
    }
    # 3) 兜底扫描：VS 可能装在任意盘符的任意目录（本机就在 D:\常用软件\...），
    #    所以在每个固定磁盘上枚举两层目录，找名为 Microsoft Visual Studio 的文件夹
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        if ($drive.Root -notmatch '^[A-Za-z]:\\$') { continue }
        $level1 = Get-ChildItem $drive.Root -Directory -ErrorAction SilentlyContinue
        $roots = @($level1 | ForEach-Object { Join-Path $_.FullName "Microsoft Visual Studio" })
        $roots += @($level1 | ForEach-Object {
            Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "Microsoft Visual Studio" }
        })
        foreach ($root in $roots) {
            if (-not (Test-Path $root)) { continue }
            $hit = Get-ChildItem $root -Recurse -Filter vcvars64.bat -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($hit) { return $hit }
        }
    }
    return $null
}

$Vcvars = Find-VcVars
if (-not $Vcvars) {
    Write-Host "[ERROR] 找不到 MSVC 编译器（Visual Studio 未安装）" -ForegroundColor Red
    Write-Host ""
    Write-Host "安装 Visual Studio（Community 或 Build Tools 均可），勾选：" -ForegroundColor Yellow
    Write-Host "  「工作负载」→ 使用 C++ 的桌面开发（会连带 Windows SDK）" -ForegroundColor Yellow
    Write-Host "下载：https://visualstudio.microsoft.com/downloads/" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "装完重跑： .\build.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host "Activating MSVC environment: $Vcvars" -ForegroundColor Cyan

$MsvcEnv = cmd /c "`"$Vcvars`" >nul 2>&1 && set"
foreach ($Line in $MsvcEnv) {
    if ($Line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
    }
}

# ---- Windows SDK 检查（必须在编译前拦住）----
# 只装 "MSVC 生成工具" 而不装 Windows SDK 时，vcvars 依然能"激活成功"、
    # cl.exe 也存在，但编译阶段一定失败；Nuitka 还会自行回退到自带的备用
    # 工具链继续编译，把真正的环境问题掩盖掉（本项目只走 MSVC）。
# 这里主动检查，缺了就明确告诉用户要装哪个组件。
$kitsRoot = Join-Path ${Env:ProgramFiles(x86)} "Windows Kits\10"
$sdkVersions = @()
if (Test-Path (Join-Path $kitsRoot "Include")) {
    $sdkVersions = Get-ChildItem (Join-Path $kitsRoot "Include") -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending |
        Select-Object -ExpandProperty Name
}
if (-not $sdkVersions) {
    Write-Host "[ERROR] 缺少 Windows SDK —— MSVC 编译器在，但没有可用的 SDK" -ForegroundColor Red
    Write-Host ""
    Write-Host "打开「Visual Studio Installer」→ 修改你已装的 VS →" -ForegroundColor Yellow
    Write-Host "  「单个组件」标签 → 搜索 Windows SDK →" -ForegroundColor Yellow
    Write-Host "  勾选「Windows 11 SDK (10.0.26100.0)」（旧的 10.0.22621.0 也可以）" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  或者：「工作负载」标签 → 勾「使用 C++ 的桌面开发」→" -ForegroundColor Yellow
    Write-Host "  右侧「安装详细信息」里确认含 Windows 11 SDK，再点右下角「修改」" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "装完不需要重启电脑，重新运行 .\build.ps1 即可" -ForegroundColor Yellow
    exit 1
}
# 以实际安装的最高版本为准（不写死版本号：机器上若只有 22621 也能编）
if (-not $Env:WindowsSDKVersion -or $Env:WindowsSDKVersion -eq "0.0.0.0") {
    $Env:WindowsSDKVersion = "$($sdkVersions[0])\"
}
Write-Host "MSVC ready  (Windows SDK $($sdkVersions -join ', '))" -ForegroundColor Green

# ============================================================
# 3. 前置检查（Nuitka 缺失即补装，兼容先跑过 start.bat 的旧 venv）
# ============================================================
# 注意：探测必须经 cmd /c 在 cmd 层丢弃 stderr。脚本开头
# $ErrorActionPreference = "Stop" 时，PowerShell 会把带 2>$null 的
# 原生命令 stderr 转成终止性错误——全新 venv 没装 Nuitka，探测
# 必然失败，脚本会在「Installing Nuitka」分支之前直接死掉。

function Get-NuitkaVersion {
    $code = "from nuitka.Version import getNuitkaVersion; print(getNuitkaVersion())"
    $out = cmd /c "`"$Python`" -c `"$code`" 2>nul"
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    return ($out | Out-String).Trim()
}

$NuitkaVer = Get-NuitkaVersion
if (-not $NuitkaVer) {
    Write-Host "Installing Nuitka... (downloading compiler toolchain, may take a minute)" -ForegroundColor Cyan
    if (-not $Env:PIP_INDEX_URL -and -not $Env:PIP_INDEX) {
        Write-Host "  提示：国内网络可用镜像加速（先设置环境变量再重跑）：" -ForegroundColor Gray
        Write-Host '    $env:PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"' -ForegroundColor Gray
    }
    # --prefer-binary：有 wheel 就装 wheel。Nuitka 若走 sdist，要先拉
    # build 依赖现场打包，网络差时这一步能卡很久
    & $Python -m pip install --prefer-binary nuitka ordered-set zstandard
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 安装 Nuitka 失败" -ForegroundColor Red
        Write-Host "可换国内镜像重试：" -ForegroundColor Yellow
        Write-Host '  .venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple nuitka ordered-set zstandard' -ForegroundColor Yellow
        exit 1
    }
    $NuitkaVer = Get-NuitkaVersion
    if (-not $NuitkaVer) {
        Write-Host "[ERROR] Nuitka 未安装" -ForegroundColor Red
        exit 1
    }
}
Write-Host "Nuitka $NuitkaVer" -ForegroundColor Green

# ---- 给 Nuitka 打 vswhere -utf8 补丁（幂等）----
# Nuitka 4.x 调用 vswhere 查 MSVC 安装路径时没加 -utf8，而中文 Windows 上
# vswhere 默认按 ANSI 输出；VS 装在中文目录（如 D:\常用软件\...）时，
# Nuitka 按 UTF-8 解码这段路径会抛 UnicodeDecodeError，构建直接崩。
# 这里检测并补上该参数；上游修好后（检测到已存在）自动跳过。
function Repair-NuitkaVswherePatch {
    param([string]$PythonPath)
    $site = cmd /c "`"$PythonPath`" -c `"import nuitka,os;print(os.path.dirname(nuitka.__file__))`" 2>nul"
    if (-not $site) { return }
    $file = Join-Path ($site | Out-String).Trim() "freezer\DllDependenciesWin32.py"
    if (-not (Test-Path $file)) { return }
    $text = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
    if ($text -match '"-utf8"') { return }
    $needle = "            vswhere_path,"
    if (-not $text.Contains($needle)) { return }
    $text = $text.Replace($needle, $needle + "`n            `"-utf8`",")
    [System.IO.File]::WriteAllText($file, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "已修补 Nuitka：vswhere 加 -utf8（中文路径下必需）" -ForegroundColor Gray
}
Repair-NuitkaVswherePatch -PythonPath $Python

# ============================================================
# 4. 清理
# ============================================================
if ($Clean -and (Test-Path "dist")) {
    Remove-Item "dist" -Recurse -Force
}

# ============================================================
# 5. 构建
# ============================================================

$NuitkaArgs = @(
    "run.py"
    "--standalone"
    "--enable-plugin=pyside6"
    "--windows-console-mode=disable"
    "--windows-icon-from-ico=app\ui\icon.ico"
    "--include-package=app.siui"
    "--include-package=qtawesome"
    "--include-package-data=qtawesome"
    "--include-data-files=app\ui\icon.png=app/ui/icon.png"
    "--include-data-files=app\ui\tray_icon.png=app/ui/tray_icon.png"
    "--include-data-files=app\ui\tray_icon_light.png=app/ui/tray_icon_light.png"
    "--nofollow-import-to=tkinter,unittest,pytest"
    # tools/ 与 tests/ 不参与打包：Nuitka 只跟随 run.py 的 import 链，
    # 它们本来就不会被带进去；这里显式排除以防将来被间接引用
    "--nofollow-import-to=tools,tests"
    "--noinclude-dlls=qt6datavisualization.dll"
    "--noinclude-dlls=qt6pdf.dll"
    "--jobs=4"
    "--assume-yes-for-downloads"
    "--output-dir=dist"
    "--output-filename=MiHome-Windows.exe"
    # 版本号自动从 app/__init__.py 的 __version__ 读取，单一信源
    $AppVersion = (Select-String -Path "app\__init__.py" -Pattern '^__version__\s*=\s*"(.+?)"').Matches[0].Groups[1].Value
    Write-Host "  Version: $AppVersion" -ForegroundColor Gray
    "--product-name=MiHome-Windows"
    "--product-version=$AppVersion"
    "--file-version=$AppVersion"
    "--copyright=Copyright (C) 2026 MiHome-Windows contributors"
)

# 强制用 MSVC（与上游作者一致）。不加这条时，Nuitka 一旦发现 MSVC/SDK
# 不可用会自行回退到自带的备用工具链（还会联网下载），既慢又掩盖真正的
# 环境问题；加上之后它会直接报错退出，问题一眼可见。
$NuitkaArgs += "--msvc=latest"

Write-Host "`nBuilding MiHome-Windows..." -ForegroundColor Cyan
Write-Host "(这是最耗时的一步，通常需要几分钟；期间会输出 Nuitka 各阶段进度，请勿关闭窗口)`n" -ForegroundColor Yellow
$BuildStart = Get-Date
& $Python -m nuitka @NuitkaArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 构建失败" -ForegroundColor Red
    exit 1
}
$Elapsed = (Get-Date) - $BuildStart
Write-Host ("`n编译耗时 {0:00}:{1:00}" -f [int]$Elapsed.TotalMinutes, $Elapsed.Seconds) -ForegroundColor Gray

# ============================================================
# 6. 整理输出
# ============================================================
if (Test-Path "dist\run.dist") {
    robocopy "dist\run.dist" "dist" /move /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    Remove-Item "dist\run.build" -Recurse -Force -ErrorAction SilentlyContinue
}

$Exe = "dist\MiHome-Windows.exe"
if (Test-Path $Exe) {
    # ---- 隐私自检：产物里绝不能出现用户数据 ----
    # 本程序把配置/缓存写在数据目录（源码模式=项目根，构建版=%LOCALAPPDATA%），
    # 其中 devices_cache.json、board.json 等含设备清单；auth.json 是登录凭据。
    # Nuitka 默认只跟随 import 与显式 --include-data-files，不应带进这些文件；
    # 这里再兜底检查一次，一旦出现就当作构建失败，避免打包分发时泄露。
    $PersonalFiles = @(
        "auth.json", "devices_cache.json", "board.json", "schedules.json",
        "settings.json", "tray.json", "workbench.json", "device_icons.json"
    )
    $Leaked = @()
    foreach ($name in $PersonalFiles) {
        $Leaked += Get-ChildItem "dist" -Recurse -File -Filter $name -ErrorAction SilentlyContinue
    }
    if ($Leaked.Count -gt 0) {
        Write-Host "`n[ERROR] 产物里发现用户数据文件，已中止（不允许打包分发）" -ForegroundColor Red
        $Leaked | ForEach-Object { Write-Host "        $($_.FullName)" -ForegroundColor Red }
        Write-Host "这些文件应只存在于数据目录，检查 build.ps1 的 --include-data-files 是否被误加。" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "隐私自检通过：产物内未包含登录凭据与设备数据" -ForegroundColor Green

    $Size = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
    Write-Host "`n构建成功! $Exe ($Size MB)" -ForegroundColor Green
} else {
    Write-Host "[ERROR] 找不到输出文件" -ForegroundColor Red
    exit 1
}
