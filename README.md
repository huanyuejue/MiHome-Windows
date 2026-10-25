# MiHome-Windows

米家设备的 Windows 桌面控制端。基于 [mijiaAPI](https://github.com/Do1e/mijia-api)
构建图形界面，扫码登录后即可在本地窗口中查看和控制家里的全部米家设备。

> 本项目基于[huanyuejue](https://github.com/huanyuejue/MiHome-Windows)开源项目制作，在此感谢原作者！

## 功能

- **扫码登录**：米家 APP 扫二维码，登录凭据与上游依赖的 CLI 共用
- **设备列表**：按家庭、房间分组，实时显示在线状态，支持隐藏无功能设备
- **场景执行**：主页「场景」选项卡列出米家 APP 中创建的手动场景，一键执行（可在设置中隐藏该入口）
- **定时开关**：给指定设备配「每天某时刻打开/关闭」，同一设备可配多条（如 7:00 开灯、23:00 关灯）；到点自动执行并提示，错过时间不补执行只提醒。**需应用保持运行**（最小化到托盘也算），关掉程序不会触发
- **设备控制**：根据设备 spec 元数据自动生成控件——布尔属性映射开关、数值属性映射滑块（自动套用范围与步长）、枚举属性映射下拉框
- **动作执行**：设备支持的动作渲染为按钮，执行前二次确认
- **系统托盘**：最小化到托盘，支持快捷设备控制、场景一键执行、小爱音响快捷控制、小爱语音指令，托盘快捷窗口支持单列/双列卡片切换与设备/场景混排排序
- **桌面挂件**：常驻桌面的设备控制面板——自选放哪些设备与场景、无级调透明度、单排最多放 9 张设备卡片、一键收起为细横条、背景色可调；窗口可置顶可锁定；开关在「设置 → 应用功能 → 桌面挂件」，开启后随应用启动自动显示；设备清单与托盘快捷窗口相互独立，卡片顺序可直接在面板上拖动调整
- **定时任务**：针对具体设备定时设置开启、关闭
- **虚拟开关支持**：针对多键开关虚拟的独立开关支持独立控制
- **主题配色**：深色 / 浅色 / 跟随系统；**主题色可自定义**（内置绿/粉/黄/蓝四档，也可打开取色板任选颜色，按钮、开关、选中态与浮窗统一跟随）；托盘图标自动跟随 Windows 任务栏深浅色
- **界面缩放**：50%–200% 无级调节（叠加在系统缩放之上），需重启生效
- **开机自启动**：可选，写入当前用户注册表（HKCU Run）
- **小爱语音**：主界面右下角悬浮按钮，输入文字指令发送给在线小爱音箱，可指定默认输出音箱
- **本地语音控制**：按全局热键说话，录音在本机识别，直接开关设备 / 执行场景，无需小爱音箱；具体见[0.4.1Tag](https://github.com/wlttechnology/MiHome-Windows/releases/tag/V0.4.1)
- **版本检测**：启动时自动检查 GitHub Releases 新版本，也可在关于页手动检测
- **本地缓存**：设置、托盘/工作台配置与设备缓存存放在用户数据目录（详见下方路径说明）
- **上游可升级**：mijiaAPI 仅作为 PyPI 依赖锁定在 `>=5,<6`，升级后跑 `python -m tests.smoke_test` 即可确认兼容性

## 运行

要求 Python >= 3.10

### 一键运行

```powershell
git clone https://github.com/huanyuejue/MiHome-Windows.git
cd MiHome-Windows

# 双击 start.bat，或命令行执行：
start.bat
```

`start.bat` 自动完成：创建 venv → 安装依赖 → 启动程序，无需手动配置环境。

### 手动运行

```powershell
git clone https://github.com/huanyuejue/MiHome-Windows.git
cd MiHome-Windows

python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python.exe run.py
```

首次启动会弹出扫码窗口；之后凭据长期复用，失效时再次扫码即可。

## 本地缓存与数据存储

程序运行时会在以下位置生成配置和缓存文件，方便用户备份或排查问题：

### 应用数据（Releases版）

路径：`%LOCALAPPDATA%\MiHome-Windows\`

| 文件 | 说明 |
|------|------|
| `settings.json` | 应用设置（主题、缩放、托盘、自启动等） |
| `tray.json` | 托盘快捷控制面板的设备/场景配置与显示顺序 |
| `workbench.json` | 工作台（设备详情页）的自定义布局 |
| `board.json` | 桌面挂件配置（启用开关、设备/场景列表与顺序、每行列数、透明度、位置尺寸、折叠态、背景色、是否置顶、是否锁定） |
| `schedules.json` | 定时开关规则（设备、时刻、动作、是否启用、最后执行日期） |
| `devices_cache.json` | 设备列表与状态缓存，启动时优先从缓存加载以加快首屏显示 |

> 路径中的 `%LOCALAPPDATA%` 通常为 `C:\Users\<用户名>\AppData\Local`。
> 旧版曾写在 exe 同目录，首次启动会自动迁移至此。

### 应用数据（源码模式）

路径：项目根目录（与 `run.py` 同级），文件名与Releases版一致

### 米家账号登录凭据

路径：`~/.config/mijia-api/auth.json`

这是 mijiaAPI 的认证文件，扫码登录后长期复用。失效时程序会自动提示重新扫码。

> 路径中的 `~` 在 Windows 上为 `C:\Users\<用户名>`。

**卸载重装为什么不用重新扫码？** 因为凭据不在安装目录、也不在
`%LOCALAPPDATA%\MiHome-Windows` 里：卸载程序只会问要不要删后者，`auth.json`
始终保留。所以「卸载 → 重装 → 打开已是登录态」是预期行为。想验证全新安装的流程，
把 `auth.json` 临时改名（如 `auth.json.bak`）再启动即可——那才会弹二维码。

## 构建可执行文件

项目使用 **[Nuitka](https://nuitka.net/)** 将 Python 源码编译打包为原生 Windows 可执行文件（standalone 模式：把 Python 解释器、全部依赖与资源文件整合进一个免安装目录，最终产出 `dist\MiHome-Windows.exe`）。Nuitka 是真编译器——把代码编译为 C 再编译为机器码，而非 PyInstaller 式的"打包字节码"，这也是需要 VS Build Tools 的原因。

### 前置条件

| 工具 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.10 | 需加入系统 PATH，构建脚本会自动创建 venv |
| C 编译器 | **MSVC（VS Build Tools / VS Community）** | 必须与 **Windows SDK** 一起装，见下 |
| 项目路径 | **纯 ASCII** | 路径含中文会导致构建失败，见下 |

**① 装 MSVC + Windows SDK（必须两个都装）**

打开 **Visual Studio Installer** → 修改已装的 VS：

- 「工作负载」标签 → 勾 **使用 C++ 的桌面开发**（会连带 Windows SDK）；
- 或「单个组件」标签 → 搜 `Windows SDK` → 勾 **Windows 11 SDK (10.0.26100.0)**
  （有 10.0.22621.0 也可以）。

> 只装 MSVC 不装 SDK 时，`cl.exe` 存在、vcvars 也能"激活成功"，但编译阶段
> 必然失败。构建脚本会**提前检查 SDK 并明确报错**。

**② 项目路径必须是纯英文**

Nuitka 依赖的 `depends.exe` 不支持非 ASCII 路径（会把中文写成 `0x04` 占位
字节），构建会在依赖扫描阶段崩掉。项目目录请放在纯英文路径下，
例如 `E:\IDE\DeepSeekHarness\MiHome-Windows-Main`。构建脚本会先检查路径并提示。

### 一键构建

```powershell
cd MiHome-Windows-Main

.\build.ps1
```

脚本自动完成：创建 venv → 安装依赖（含 Nuitka 本体）→ 激活 MSVC 环境 → Nuitka 编译
→ 输出到 `dist\MiHome-Windows.exe`，并做一次**隐私自检**（确认产物里没有
登录凭据与设备数据）。

> **产物不带个人米家信息**：账号凭据、设备列表、挂件/定时配置都只存在用户目录
> （见上节），编译时不会打进 `dist\`；隐私自检发现任何一份就报错终止。
> 别人拿到你的 `dist\` 或安装包，仍然是他自己的账号、自己的设备。

> **产物是"一个 exe + 一堆依赖目录"**，不是单文件。只拷 `MiHome-Windows.exe`
> 到别的机器是跑不起来的，必须整个 `dist\` 目录一起分发（或做安装包，见下）。

构建参数（打包资源清单、图标、排除项等）集中维护在 `build.ps1` 的 `$NuitkaArgs`；
`build_msvc.bat` 只是转发到它的双击入口。

首次构建耗时较长（创建 venv + 下载依赖 + Nuitka 编译，通常 5–15 分钟，视机器而定）



## 项目结构

```
app/
├── core/                       # 核心层
│   ├── service.py              # mijiaAPI 适配层，全项目唯一 import mijiaAPI 的模块
│   ├── jobs.py                 # 串行任务队列，所有米家网络调用的后台通道
│   ├── models.py               # 数据模型
│   ├── _json_store.py          # JSON 持久化公共基础（数据目录/迁移/原子写）
│   ├── cache.py                # 设备缓存
│   ├── settings_store.py       # 应用设置持久化（含开机自启动注册表）
│   ├── tray_store.py           # 托盘配置持久化
│   ├── workbench_store.py      # 工作台配置持久化
│   ├── board_store.py          # 桌面挂件配置持久化（设备/列数/透明度/几何）
│   ├── schedule_store.py       # 定时开关规则持久化
│   ├── scheduler.py            # 定时判定（纯逻辑：到点/错过，不依赖 Qt）
│   ├── update_checker.py       # GitHub Releases 新版本检查（后台线程 + 信号）
│   └── restart.py              # 应用自重启（缩放等设置需重启生效时一键重启）
├── ui/                         # 界面层
│   ├── main_window.py          # 主窗口（无边框标题栏）
│   ├── tray/                   # 系统托盘
│   │   ├── quick_window.py     #   快捷控制面板（单列/双列卡片）
│   │   ├── audio_bar.py        #   音响控制栏
│   │   ├── controller.py       #   托盘控制器
│   │   └── manager_dialog.py   #   托盘项目管理对话框
│   ├── device_card.py          # 设备卡片组件
│   ├── device_dialog.py        # 设备详情对话框
│   ├── workbench_panel.py      # 工作台面板（属性/动作）
│   ├── workbench_item.py       # 工作台属性/动作项
│   ├── widgets_board.py        # 桌面挂件窗口（常驻置顶/拖动/透明度/折叠横条）
│   ├── board_manager.py        # 桌面挂件管理对话框（设备/场景分页勾选 + 外观设置）
│   ├── schedule_dialog.py      # 定时任务对话框（按设备配时间与开/关）
│   ├── prop_widgets.py         # 属性控件（开关、滑块、下拉框）
│   ├── power_button.py         # 三态电源按钮（卡片/托盘/详情共用）
│   ├── overlay_dialog.py       # 遮罩对话框基类（详情/设置/抽屉共用）
│   ├── about_dialog.py         # 关于对话框（版本信息、手动检测更新）
│   ├── settings_dialog.py      # 设置对话框（主题界面/应用功能双分类）
│   ├── voice_fab.py            # 语音悬浮球
│   ├── toast.py                # 轻量通知浮层
│   ├── update_flow.py          # 版本检查界面流程（弹框提示/静默反馈）
│   ├── login_dialog.py         # 扫码登录对话框
│   ├── add_drawer.py           # 设备添加抽屉
│   ├── typewriter.py           # 打字机效果组件
│   ├── si_theme.py             # 主题中枢（深/浅调色板 + 全局 QSS 生成）
│   ├── theme_service.py        # 主题编排（跟随系统/浅色/深色）
│   ├── restart.py              # 应用自重启
│   ├── icon.ico / icon.png     # 应用图标
│   └── tray_icon.png / tray_icon_light.png  # 托盘图标
├── siui/                       # 内置 SiliconUI 组件库（GPL-3.0）
│   ├── components/             # UI 组件
│   ├── core/                   # 核心工具
│   └── gui/                    # 图形工具
└── __init__.py                 # 版本号 + 工具函数

tests/
├── smoke_test.py               # 上游升级后的接口兼容性自检
├── theme_test.py               # 主题切换回归（离屏像素断言）
├── board_test.py               # 桌面挂件回归（离屏渲染/收起/透明度/管理对话框）
├── latency_test.py             # 开关响应速度回归（队列优先级/单次写/乐观回滚）
├── race_test.py                # 开关状态回弹回归（过期轮询不得覆盖本地操作）
├── schedule_test.py            # 定时开关回归（到点/错过/停用/界面接线）
├── audio_test.py               # 托盘音响栏回归（音箱下拉/独立偏好/音量渲染）
└── subdevice_test.py           # 多键面板虚拟子设备的通道映射回归

tools/
└── diag_switch.py              # 只读诊断：打印云端对多键面板各 did/siid 的原始返回

run.py                          # 程序入口
start.bat                       # Windows 一键运行（双击运行）
build_msvc.bat                  # Windows 一键构建（双击运行，转发 build.ps1）
build.ps1                       # PowerShell 构建脚本（Nuitka 参数唯一来源）
pyproject.toml                  # 项目配置
LICENSE                         # GPL-3.0 许可证
```

## 依赖说明

| 包名 | 版本 | 用途 |
|------|------|------|
| mijiaAPI | >=5,<6 | 米家 API 封装 |
| PySide6 | >=6.7 | Qt6 绑定 |
| qrcode | >=8 | 登录二维码生成 |
| qtawesome | >=1.4 | Material Design 图标 |
| numpy | - | SiliconUI 动画插值 |
| sherpa-onnx | >=2,<3 | 本地语音识别引擎（离线；识别模型按需下载，不进安装包） |
| typing_extensions | - | SiliconUI 在 Python 3.10 下所需的类型别名 |

## 排查问题

出问题时先看日志：源码模式在项目根的 `debug.log`，构建版在
`%LOCALAPPDATA%\MiHome-Windows\debug.log`（`run.py` 运行期隐藏控制台，
日志是唯一线索）。云端返回的原始错误码可用诊断脚本查看：

```powershell
.venv\Scripts\python.exe -X utf8 -m tools.diag_switch
```


## 开源许可

本项目基于 [GPL-3.0](LICENSE) 或更高版本发布。

### 第三方组件

本程序使用了以下 GPL-3.0 协议的开源组件，在此向原作者致谢：

- [mijia-api](https://github.com/Do1e/mijia-api) - 米家 API 封装
- [PySide6-SiliconUI](https://github.com/H1DDENADM1N/PySide6-SiliconUI) - UI 组件库（已内置至 `app/siui/`）
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) - 本地语音识别运行时（Apache-2.0）
- SenseVoice / Paraformer 识别模型 - 由 sherpa-onnx 官方发布，**按需下载、不随安装包分发**，遵循各自模型协议

对本项目代码的使用、修改与分发同样须遵循 GPL-3.0。

本程序不含任何担保。请自行承担使用风险，并遵守小米的服务条款。
