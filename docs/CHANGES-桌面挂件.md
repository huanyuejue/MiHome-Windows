# 桌面挂件与响应速度改造 —— 改动记录 / 迁移重放指南

> **用途**：记录本次改造的完整内容，并说明如何把它重放到其他版本。
> **当前状态**：**0.3.4**（§18 扫码卡死 / §19 背景色与置顶 / §20 搜索与多窗 / §21 管理窗布局重排）；后续开发以 0.3.4 为基线。
> **原始基线**：2026-10-18 的 0.2.0 工作副本（无 git 仓库，故以「函数锚点 + 完整代码」记录）。
> **重放方式**：新增文件整份复制；修改文件按 §4 的锚点定位后替换/插入。

---

## 0. 怎么用这份文档

1. **换新版之前先把改动文件另存一份**（这是最容易忘的一步）：新版若是整目录覆盖，
   下列 5 个文件会被直接冲掉——先复制到项目外：
   `app/core/board_store.py`、`app/ui/widgets_board.py`、`app/ui/board_manager.py`、
   `tests/board_test.py`、`tests/latency_test.py`。
   > 只有这 5 个是"整份新增"，其余都是对既有文件的局部修改，覆盖后就照 §4 逐处补。
2. **先做 §2 的顺序**：core 层 → ui 层 → 接线。反了会 ImportError。
3. **不要按行号找**：行号会随上游变化，全部用**函数名 / 类名**定位。
4. 每个修改点都标了「上游若已重写该函数怎么办」（§5）。
5. 全部重放完，跑 §6 的四个测试 + 手工验收，**四项测试必须 exit 0**。

---

## 1. 速查表

| 文件 | 类型 | 行数 | 关键内容 |
|---|---|---|---|
| `app/core/board_store.py` | **新增** | 152 | `board.json`：设备清单/列数/透明度/几何/折叠态/启动显示 |
| `app/ui/widgets_board.py` | **新增** | 918 | 挂件窗口：常驻置顶、拖动、透明度、折叠横条、卡片 |
| `app/ui/board_manager.py` | **新增** | 494 | 挂件管理对话框：勾选+排序+列数+透明度实时预览 |
| `tests/board_test.py` | **新增** | 391 | 挂件回归（8 组 + 折叠条拖动/点击） |
| `tests/latency_test.py` | **新增** | 195 | 开关响应速度回归（优先级/单次写/乐观回滚） |
| `app/core/jobs.py` | **整份重写** | 147 | 优先级队列 + daemon 线程 + 主线程定时器投递 |
| `app/core/service.py` | 改 1 个函数 | — | `toggle_power(did, current=None)` |
| `app/ui/power_button.py` | 改 1 个分支 | — | 忙碌态保留目标状态色 |
| `app/ui/main_window.py` | 改 11 处 | — | 挂件接线、独立网页线程、乐观开关 |
| `app/ui/tray/controller.py` | 改 4 处 | — | 托盘菜单两个入口 + 文案跟随 |
| `app/ui/tray/quick_window.py` | 改 2 处 | — | 托盘行乐观开关 |
| `app/ui/settings_dialog.py` | 改 4 处 | — | 「启动时显示桌面挂件」开关 |
| `README.md` / `.gitignore` | 改 | — | 文档 + 忽略 `board.json`、锁文件 |
| `tests/theme_test.py` | 改 1 处 | — | 退出路径（daemon 线程不再 join） |

**没有改动**：`pyproject.toml`、`run.py`、`app/core/_json_store.py`、`app/core/models.py`、`app/core/settings_store.py`、`app/core/tray_store.py`、`app/ui/device_card.py`、`app/ui/overlay_dialog.py`、`app/ui/tray/draggable_row.py`。

---

## 2. 重放顺序

```
① app/core/jobs.py            ← 整份替换（其他文件都要 import 优先级常量）
② app/core/service.py         ← toggle_power 加参数
③ app/core/board_store.py     ← 新增（无依赖）
④ app/ui/power_button.py      ← 忙碌态配色
⑤ app/ui/widgets_board.py     ← 新增（依赖 ①③④ + PowerButton/SiColors）
⑥ app/ui/board_manager.py     ← 新增（依赖 ③ + OverlayDialog/_DraggableRow）
⑦ app/ui/main_window.py       ← 接线（依赖 ①⑤⑥）
⑧ app/ui/tray/controller.py   ← 托盘菜单（依赖 ⑦）
⑨ app/ui/tray/quick_window.py ← 托盘行乐观开关（依赖 ①）
⑩ app/ui/settings_dialog.py   ← 设置开关（依赖 ③）
⑪ tests/ + README / .gitignore
```

---

## 3. 新增文件（整份复制）

直接把这 5 个文件按原路径放进新版：

```
app/core/board_store.py
app/ui/widgets_board.py
app/ui/board_manager.py
tests/board_test.py
tests/latency_test.py
```

**它们的外部依赖（新版必须有，缺了就补）**：

| 依赖 | 用在哪 |
|---|---|
| `app/core/jobs.py` 的 `PRIORITY_CONTROL` | `widgets_board.py`、`quick_window.py`、`main_window.py` |
| `app/ui/power_button.py` 的 `PowerButton(size, icon_size=)` | 挂件卡片、托盘行 |
| `app/ui/si_theme.py` 的 `SiColors`（`WINDOW_BG`/`CARD`/`LINE`/`TEXT_*`/`THEME`/`STATE_OFF`/`SCROLLBAR*`/`BTN_HOVER`） | 全部 ui |
| `app/ui/overlay_dialog.py` 的 `OverlayDialog` | `board_manager.py` |
| `app/ui/tray/draggable_row.py` 的 `_DraggableRow` | `board_manager.py` |
| `app/core/models.py` 的 `DeviceInfo` | 全部 |
| `app/core/service.py` 的 `power_states` / `toggle_power` / `set_power_state` | 挂件拉状态与写开关 |
| `qtawesome`、`shiboken6` | 图标、对象存活判断 |

> `board_manager.py` 复用了托盘管理对话框的 `_DraggableRow`（拖拽排序行）。
> 若新版把它改名/移走，改这一行 import：
> `from app.ui.tray.draggable_row import _DraggableRow`

---

## 4. 修改清单

> 下面只列**影响行为**的改动。另有几处纯注释/文案改动（`power_button.py` 模块 docstring、
> `main_window.show_settings()` 末尾一句注释、`_push_tray_metrics` 的 docstring），
> 不重放也不影响功能。

### 4.1 `app/core/jobs.py` —— 整份替换

**为什么整份换**：队列从 FIFO 改成优先级、线程模型从 `QThread` 改成 daemon 线程、结果投递从跨线程 `emit` 改成主线程 `QTimer`。三件事互相耦合，逐行改容易漏。

**新版对外接口（调用方只需知道这三条）**：

```python
from app.core.jobs import PRIORITY_CONTROL, PRIORITY_DATA, PRIORITY_IDLE, JobExecutor

jobs = JobExecutor(parent)                    # 签名不变
jobs.submit(fn, on_success=..., on_error=..., priority=PRIORITY_CONTROL)
jobs.shutdown()                               # 默认不阻塞；shutdown(wait=2.0) 才等
```

- `priority` 默认 `PRIORITY_DATA`，**不传 = 老行为**（除了排序更合理）。
- `shutdown()` 语义向后兼容，只是不再阻塞等线程。

**核心实现要点（若你不整份替换，这三点必须保住）**：

1. `queue.PriorityQueue`，条目 `(priority, next(self._seq), _Task)`；毒丸 `(PRIORITY_CONTROL, seq, None)`。
2. worker 是 `threading.Thread(daemon=True)`，**不要**再用 `QThread` + `run` 赋值（Windows 上 `QThread.wait()` 偶发访问越界）。
3. worker 只把结果放 `queue.Queue`，**由主线程的 `QTimer(16ms)` 取出去执行回调**；不要从 worker 里 `Signal.emit()`。

---

### 4.2 `app/core/service.py` —— `toggle_power` 增加 `current` 参数

**锚点**：`MijiaService.toggle_power`

```python
def toggle_power(self, did: str, current: bool | None = None) -> bool:
    """切换到开关的反面，返回新状态。

    `current` 是调用方已知的当前状态（界面层的开关记忆）：给了就
    直接写反值，**省掉一次读往返**——米家云端单次往返 0.3~0.8 秒，
    少一次就能让点击明显更跟手。

    不给 `current` 时退回"先读后写"，读与写串在同一任务里完成
    （调用方经单线程队列提交），否则两次轮询之间会出现读后写的竞态。
    """
    if current is None:
        current = self.power_state(did)
    if current is None:
        raise ServiceError("设备不支持开关控制或已离线")
    new_state = not current
    self.set_power_state(did, new_state)
    return new_state
```

> 其余服务层方法**没有改动**。`set_power_state`、`power_states` 保持上游原样。

---

### 4.3 `app/ui/power_button.py` —— 忙碌态保留目标状态色

**为什么**：界面改成「乐观翻转 + 失败回滚」后，点下就把按钮切到目标状态。原来的忙碌态把底色刷成 `SURFACE_PRESSED`，翻转根本看不见，用户又觉得"点了没反应"。

**锚点**：`PowerButton._apply` 里的 `if self._busy:` 分支

```python
if self._busy:
    # 忙碌态：保留目标状态色（乐观翻转要看得见），只降一档并禁点
    if self._state is None:
        self.setIcon(qta.icon('mdi.circle-outline', color=SiColors.ICON_DIM))
        self.setStyleSheet(
            f"QPushButton#powerBtn {{ background: {SiColors.SURFACE_PRESSED};"
            f" border: 2px solid {SiColors.STATE_UNKNOWN_BORDER};"
            f" border-radius: {radius}px; }}"
        )
        return
    color = SiColors.THEME_DIM if self._state else SiColors.STATE_OFF
    self.setIcon(qta.icon('mdi.power', color=SiColors.ICON_DIM))
    self.setStyleSheet(
        f"QPushButton#powerBtn {{ background: {color}; border: none;"
        f" border-radius: {radius}px; }}"
    )
    return
```

> 顺带把模块 docstring 首行改成「设备卡片、托盘快捷行、挂件卡片、详情电源行共用」，并补一段说明为什么忙碌态要保留状态色。

---

### 4.4 `app/ui/main_window.py` —— 11 处

#### 4.4.1 import（文件顶部 import 区）

```python
from app.core.jobs import PRIORITY_CONTROL, PRIORITY_IDLE, JobExecutor
from app.ui.board_manager import BoardManagerDialog
from app.ui.widgets_board import WidgetsBoard
```

> `JobExecutor` 原来就有，合并成一行；新增另外两个常量与两个新模块。

#### 4.4.2 `MainWindow.__init__` —— 第二条后台线程

**锚点**：`__init__` 开头 `self._service = MijiaService()` 之后

```python
self._service = MijiaService()
self._jobs = JobExecutor(self)
# 第二条后台线程：只跑「纯 requests 的外部网页抓取」（产品页中文名、
# 图标图片下载）。它们慢（实测单个产品页 2.4 秒、超时 30 秒）且与
# 设备控制无关，留在主队列里会把用户点的开关堵在后面——这正是
# 「点开关半天没反应」的根因。这些调用用的是模块级 requests
# 而非上游共享 Session，分线程不违反线程安全前提。
self._web_jobs = JobExecutor(self)
```

#### 4.4.3 `MainWindow.__init__` —— 新增两个状态字段

**锚点**：`self._localize_busy = False` 附近

```python
# 产品页名称回退的异步查询防重入
self._localize_busy = False
# 已尝试过产品页查询的型号：失败后不再每 5 秒重试（实测该站点
# 单次 2.4 秒，网络差时重试会把控制队列灌满）
self._localize_attempted: set[str] = set()
```

#### 4.4.4 `MainWindow.__init__` —— 创建挂件

**锚点**：托盘 `try/except` 创建块之后（`self._tray = None` 那个 `except` 结尾）

```python
# 桌面挂件：常驻桌面的设备控制板，与托盘快捷窗口并列的独立入口。
# 构造失败不得影响主流程（挂件是增强功能，缺失只少个面板）
self._board_manager: BoardManagerDialog | None = None
try:
    self._board = WidgetsBoard(self._service, self._jobs, None)
    self._board.manage_requested.connect(self.show_board_manager)
    self._board.open_device_requested.connect(self._on_open_device)
    # 挂件内开关后回传，保持主界面卡片与开关记忆一致
    self._board.power_changed.connect(self._apply_power_state)
except Exception as e:
    logger.warning("桌面挂件创建失败: %s", e)
    self._board = None
```

> 注意：`WidgetsBoard` 的 **parent 必须是 None**（顶级窗口），否则会跟主窗口一起隐藏。

#### 4.4.5 「更多」菜单加两个入口

**锚点**：`self._more_menu.addAction("托盘管理")...` 之后

```python
self._more_menu.addAction("托盘管理").triggered.connect(self.show_tray_manager)
self._more_menu.addAction("桌面挂件").triggered.connect(self.show_board)
self._more_menu.addAction("管理桌面挂件").triggered.connect(self.show_board_manager)
self._more_menu.addAction("设置").triggered.connect(self.show_settings)
```

#### 4.4.6 `_startup_load` —— 缓存命中后按设置决定是否显示挂件

**锚点**：`_startup_load` 末尾 `self._apply_devices(devices)`

```python
self._show_status("已从本地缓存加载，点击右上角菜单刷新可同步最新设备")
self._apply_devices(devices)
self._apply_board_autostart()
```

#### 4.4.7 新增 `_apply_board_autostart`

**锚点**：紧跟 `_startup_load` 之后新增方法

```python
def _apply_board_autostart(self) -> None:
    """按设置决定挂件是否在设备就绪后自动显示。

    独立于托盘的「启动时最小化到托盘」开关：托盘管的是主窗口怎么
    起步，挂件管的是桌面是否多一块面板，两者互不覆盖。
    """
    if self._board is None:
        return
    from app.core import board_store
    if board_store.get_autostart():
        self.show_board()
    else:
        self._board.hide()
```

#### 4.4.8 `_on_devices_loaded` —— 拉到新列表后也走一次自动显示

**锚点**：`_on_devices_loaded` 末尾

```python
self._apply_devices(devices)
device_cache.save(devices, self._known_power, self._metrics)
self._apply_board_autostart()
```

#### 4.4.9 `_apply_devices` —— 刷新时同步挂件

**锚点**：`_apply_devices` 末尾

```python
self._update_voice_fab()
self._update_tray_devices()
self._update_board_devices()
self._load_card_icons()
```

#### 4.4.10 新增挂件方法组（放在 `_update_tray_devices` 之后，独立一段）

```python
def _update_board_devices(self) -> None:
    """设备列表 / 开关状态变化后同步桌面挂件。

    挂件用全量列表而非当前房间筛选后的列表：它是常驻面板，
    不该随主界面的房间切换而增删卡片。
    """
    if self._board is not None:
        try:
            self._board.set_devices(self._all_devices, self._known_power)
        except Exception:
            logger.warning("同步挂件设备失败", exc_info=True)

# ---------- 桌面挂件 ----------

def show_board(self) -> None:
    """显示桌面挂件（托盘菜单 / 管理对话框 / 设置开关共用入口）。"""
    if self._board is None:
        return
    self._board.set_devices(self._all_devices, self._known_power)
    self._board.show()
    self._board.raise_()

def hide_board(self) -> None:
    if self._board is not None:
        self._board.hide()

def toggle_board_collapse(self) -> None:
    """托盘菜单「收起挂件」：折叠为细横条 / 已折叠则展开。"""
    if self._board is None:
        return
    self.show_board()
    self._board.toggle_collapse()

def show_board_manager(self) -> None:
    """打开挂件设备管理对话框。

    透明度与列数在对话框内即时预览到真实挂件窗口（所见即所得），
    因此即使点「取消」，这两项的调整也已生效并落盘；「保存」只
    决定设备清单与顺序是否写入。
    """
    if not self._all_devices:
        Toast.info(self, "请先刷新获取设备列表", 2500)
        return
    parent = None if self.isHidden() else self
    dlg = BoardManagerDialog(self._all_devices, parent)
    self._board_manager = dlg
    dlg.opacity_preview.connect(self._on_board_opacity_preview)
    dlg.columns_preview.connect(self._on_board_columns_preview)
    # 对话框遮挡会让挂件暂停「悬停恢复不透明」，否则看不到透明度效果
    if self._board is not None:
        self._board.set_previewing(True)
    result = dlg.exec()
    dlg.deleteLater()
    self._board_manager = None
    if self._board is not None:
        self._board.set_previewing(False)
    if result == QDialog.Accepted:
        from app.core import board_store
        board_store.save_devices(dlg.selected_dids())
        self._update_board_devices()
        Toast.info(self, "挂件设备已更新", 2000)

def _on_board_opacity_preview(self, value: float) -> None:
    # 落盘在对话框侧已完成，这里只负责让窗口实时可见
    if self._board is not None:
        self._board.set_opacity(value, persist=False)

def _on_board_columns_preview(self, value: int) -> None:
    if self._board is not None:
        self._board.set_columns(value)
```

#### 4.4.11 `_load_card_icons` —— 图标 URL 用低优先级，图片下载走副线程

**锚点**：`_load_card_icons` 与 `_on_icons_ready`

```python
self._jobs.submit(
    lambda m=models: self._service.icon_urls(m),
    on_success=self._on_icons_ready,
    priority=PRIORITY_IDLE,
)

def _on_icons_ready(self, urls: dict[str, str | None]) -> None:
    """图标 URL 就绪：缺图的下发后台下载，下载完成回填当前可见卡片。"""
    for model, url in urls.items():
        if not url:
            continue
        if icon_store.icon_path(model).is_file():
            continue
        # 图片下载走 _web_jobs：单张最慢可到 15 秒超时，绝不能占控制队列
        self._web_jobs.submit(
            lambda m=model, u=url: self._service.icon_file(m, u),
            on_success=lambda p, m=model: self._apply_card_icon(m, str(p))
            if p else None,
            priority=PRIORITY_IDLE,
        )
```

> `icon_urls / get_icon` 走的是上游共享 `requests.Session`，**必须留在 `_jobs`**；只有 `icon_file`（模块级 `requests.get`）能挪到 `_web_jobs`。

#### 4.4.12 `_maybe_localize_names` —— 产品页名查询挪线程 + 不重试

**锚点**：`_maybe_localize_names` 末尾「仍有未解析的型号」那段

```python
# 仍有未解析的型号 → 提交一次后台查询（防重入）
# 走 _web_jobs：产品页单次实测 2.4 秒、超时 30 秒，绝不能在
# 控制队列里跑；失败后也不再每 5 秒重试（见 _on_product_names_failed）
pending = sorted({d.model for d in ascii_devices
                  if not self._service.has_product_page_name(d.model)
                  and d.model not in self._localize_attempted})
if pending and not self._localize_busy:
    self._localize_busy = True
    self._localize_attempted.update(pending)
    self._web_jobs.submit(
        lambda: [self._service.product_page_name(m) for m in pending],
        on_success=lambda _: self._on_product_names_fetched(),
        on_error=lambda e: self._on_product_names_failed(e),
        priority=PRIORITY_IDLE,
    )
```

#### 4.4.13 `_apply_power_states` —— 轮询结果同步挂件

**锚点**：`_apply_power_states`

```python
def _apply_power_states(self, states: dict[str, bool | None]) -> None:
    self._poll_in_flight = False
    for did, state in states.items():
        self._apply_power_state(did, state)
    self._maybe_localize_names()
    self._update_tray_devices()
    # 挂件就地更新按钮状态（不整窗重建，避免每 5 秒一次重建抖动）
    if self._board is not None:
        try:
            self._board.apply_power_states(states)
        except Exception:
            logger.warning("同步挂件开关状态失败", exc_info=True)
```

#### 4.4.14 `_push_tray_metrics` —— 读数同步挂件

**锚点**：`_push_tray_metrics`

```python
def _push_tray_metrics(self) -> None:
    """把温湿度读数同步给托盘快捷窗口与桌面挂件（副标题展示）。"""
    if self._tray is not None:
        try:
            self._tray.set_metrics(self._metrics)
        except Exception:
            # 托盘行可能在重建间隙销毁，同步失败可安全忽略
            pass
    if self._board is not None:
        try:
            self._board.set_metrics(self._metrics)
        except Exception:
            pass
```

#### 4.4.15 `_on_theme_changed` —— 挂件随主题重设样式

**锚点**：`_on_theme_changed` 里托盘那一段之后

```python
    if self._tray is not None:
        self._tray.retheme()
        self._update_tray_devices()
        self._push_tray_metrics()
if self._board is not None:
    self._board.retheme()
# 打开中的挂件管理对话框（内联样式为构造期求值）同步刷新
bdlg = self._board_manager
if bdlg is not None and shiboken6.isValid(bdlg) and bdlg.isVisible():
    bdlg.retheme()
```

#### 4.4.16 快速开关三方法 —— 乐观 UI + 单次写（**响应速度的核心**）

**锚点**：`_on_power_clicked` / `_on_power_done` / `_on_power_failed` 三个方法（`# ---------- 快速开关 ----------` 段）

```python
def _on_power_clicked(self, did: str) -> None:
    card = self._cards.get(did)
    if card is None:
        return
    current = self._known_power.get(did)
    if current is None:
        # 状态未知：只能让服务端读一次再写，仍按用户操作优先级排队
        card.set_busy(True)
        self._jobs.submit(
            lambda: self._service.toggle_power(did),
            on_success=lambda new_state, c=card, d=did:
                self._on_power_done(c, d, new_state),
            on_error=lambda err, c=card: self._on_power_failed(c, err),
            priority=PRIORITY_CONTROL,
        )
        return
    target = not current
    # 乐观 UI：先翻转再发请求，失败回滚（见 _on_power_failed）
    self._known_power[did] = target
    card.set_power_state(target)
    card.set_busy(True)
    self._jobs.submit(
        lambda: (self._service.set_power_state(did, target), target)[1],
        on_success=lambda new_state, c=card, d=did:
            self._on_power_done(c, d, new_state),
        on_error=lambda err, c=card, d=did, prev=current:
            self._on_power_failed(c, err, d, prev),
        priority=PRIORITY_CONTROL,
    )

def _on_power_done(self, card: DeviceCard, did: str, new_state: bool) -> None:
    # 卡片可能在任务排队期间随网格重建被销毁，回调前先确认存活
    if not shiboken6.isValid(card):
        return
    card.set_busy(False)
    card.set_power_state(new_state)
    self._known_power[did] = new_state
    self._update_tray_devices()
    if self._board is not None:
        self._board.apply_power_states({did: new_state})
    Toast.info(
        self, f"已{'打开' if new_state else '关闭'}「{card.device.name}」", 2500
    )

def _on_power_failed(self, card: DeviceCard, error: Exception,
                     did: str | None = None,
                     revert_to: bool | None = None) -> None:
    # 乐观翻转失败要回滚，否则界面显示"已开"而设备实际没动
    if did is not None and revert_to is not None:
        self._known_power[did] = revert_to
        if shiboken6.isValid(card):
            card.set_power_state(revert_to)
        if self._board is not None:
            self._board.apply_power_states({did: revert_to})
    if not shiboken6.isValid(card):
        return
    card.set_busy(False)
    QMessageBox.warning(self, "操作失败", str(error))
```

#### 4.4.17 `closeEvent` —— 隐藏挂件 + 关掉第二条线程

**锚点**：`closeEvent` 里托盘清理之后

```python
# 桌面挂件：先把位置与透明度落盘再销毁，避免退出时丢摆放
if self._board is not None:
    try:
        self._board.hide()
    except Exception:
        pass
self._jobs.shutdown()
self._web_jobs.shutdown()
super().closeEvent(event)
```

---

### 4.5 `app/ui/tray/controller.py` —— 4 处

#### 4.5.1 菜单加两项（`__init__` 里 `menu` 构建处）

```python
act_manage = QAction("管理托盘设备", menu)
act_manage.triggered.connect(self._emit_manage)
menu.addAction(act_manage)
# 桌面挂件：显示/隐藏 + 管理 两条并列，入口放一起，
# 用户不必先找到主窗口
act_board = QAction("显示桌面挂件", menu)
act_board.triggered.connect(self._on_toggle_board)
menu.addAction(act_board)
self._board_action = act_board
act_board_manage = QAction("管理桌面挂件", menu)
act_board_manage.triggered.connect(self._on_manage_board)
menu.addAction(act_board_manage)
act_settings = QAction("设置", menu)
act_settings.triggered.connect(self._on_settings)
menu.addAction(act_settings)
menu.addSeparator()
act_quit = QAction("退出", menu)
act_quit.triggered.connect(self._quit)
menu.addAction(act_quit)
# 每次弹出前刷新挂件菜单文案：窗口可能被挂件自己的 × 按钮关掉，
# 菜单若一直写「显示」会与实际状态不符
menu.aboutToShow.connect(self._refresh_board_action)
self._tray.setContextMenu(menu)
```

#### 4.5.2 新增方法组（`_on_manage` 之后）

```python
# ---- 桌面挂件 ----

def _on_toggle_board(self) -> None:
    """托盘菜单「桌面挂件」：已显示则隐藏，未显示则显示。"""
    if self._board_visible():
        self._main.hide_board()
    else:
        self._main.show_board()
    self._refresh_board_action()

def _board_visible(self) -> bool:
    board = getattr(self._main, "_board", None)
    return board is not None and board.isVisible()

def _refresh_board_action(self) -> None:
    """菜单文案跟随挂件实际状态，避免"显示"点了却是隐藏。"""
    if self._board_action is not None:
        self._board_action.setText(
            "隐藏桌面挂件" if self._board_visible() else "显示桌面挂件")

def _on_manage_board(self) -> None:
    self._main.show_board_manager()
```

---

### 4.6 `app/ui/tray/quick_window.py` —— 2 处

#### 4.6.1 import

```python
from app.core.jobs import PRIORITY_CONTROL, JobExecutor
```

#### 4.6.2 `_make_row` 里的开关回调 + 新增失败回调

**锚点**：`_make_row` 内 `if known is not None:` 分支

```python
btn = None
if known is not None:
    btn = PowerButton(28, icon_size=22)
    btn.set_state(known)
    def _on_toggle(checked=False, d=dev.did, b=btn):
        # 已知状态直接写反值（省一次读往返）+ 立刻翻转按钮，
        # 失败在回调里回滚：点击不再有"点了没反应"的空窗
        target = not bool(self._known_power.get(d))
        self._known_power[d] = target
        b.set_state(target)
        b.set_busy(True)
        self._jobs.submit(
            lambda: (self._service.set_power_state(d, target), target)[1],
            on_success=lambda ns, dd=d, bb=b: self._on_toggle_done(dd, ns, bb),
            on_error=lambda e, dd=d, bb=b, prev=(not target):
                self._on_toggle_failed(dd, prev, bb),
            priority=PRIORITY_CONTROL,
        )
    btn.clicked.connect(_on_toggle)
    lay.addWidget(btn)
```

**锚点**：`_on_toggle_done` 之后新增

```python
def _on_toggle_done(self, did: str, ns: bool, btn) -> None:
    self._known_power[did] = ns
    btn.set_state(ns)
    btn.set_busy(False)

def _on_toggle_failed(self, did: str, revert_to: bool, btn) -> None:
    """写入失败：回滚乐观翻转的按钮，不留假状态。"""
    import shiboken6

    self._known_power[did] = revert_to
    if shiboken6.isValid(btn):
        btn.set_state(revert_to)
        btn.set_busy(False)
```

---

### 4.7 `app/ui/settings_dialog.py` —— 4 处

#### 4.7.1 import

```python
from app.core import board_store
from app.core import settings_store
```

#### 4.7.2 `_build_features_page` 新增开关项

**锚点**：「隐藏无可控制功能的设备」那一段之前

```python
# ── 桌面挂件：启动时自动显示 ──
self._board_item, self._board_label, self._board_desc, board_row = self._make_item(
    "启动时显示桌面挂件",
    "开启后，每次启动应用时桌面自动显示设备挂件面板；"
    "挂件的设备、列数与透明度在「管理桌面挂件」中调整")
self._board_toggle = themed_switch()
self._board_toggle.setChecked(board_store.get_autostart())
_sync_switch(self._board_toggle)
board_row.addWidget(self._board_toggle)
```

#### 4.7.3 `_build_features_page` 的 item 列表

```python
return self._build_scroll([
    self._autostart_item, self._speaker_item, self._tray_item,
    self._start_min_item, self._board_item, self._hide_item,
    self._update_item,
])
```

#### 4.7.4 `_save_and_accept` 落盘

```python
settings_store.set_hide_no_func_devices(self._hide_toggle.isChecked())
board_store.set_autostart(self._board_toggle.isChecked())
settings_store.set_check_update_enabled(self._update_toggle.isChecked())
```

#### 4.7.5 `_apply_styles` 的标签/描述颜色列表各加一项

```python
for label in (..., self._update_label, self._board_label):
for desc in (..., self._update_desc, self._board_desc):
```

---

### 4.8 `tests/theme_test.py` —— 退出路径

**为什么改**：新版 `JobExecutor` 是 daemon 线程，测试不必也不能在解释器关闭阶段 `join`（实测偶发访问越界）。旧版这里靠 `QThread` 析构报错，本就以非 0 退出码收尾，会掩盖真实回归。

**锚点**：文件末尾 `win.close()` 之前

```python
win._all_devices = []  # 关闭路径不保存假设备
# 后台线程是 daemon，进程退出时自然结束，测试不必显式 join
win.close()
jobs.shutdown()
```

> 相对上游原版，这里**删掉**了任何 `win._jobs.shutdown()` / `win._web_jobs.shutdown()` 调用。

---

### 4.9 `README.md` / `.gitignore`

- README「功能」加一条**桌面挂件**；「本地缓存」表加 `board.json` 行；「项目结构」树里补
  `board_store.py` / `widgets_board.py` / `board_manager.py` / `tests/board_test.py` / `tests/latency_test.py`；
  新增一节「开关响应速度（为什么点下去就变）」。
- `.gitignore` 在「用户个性化配置」段加 `board.json`；另加一条 `MiHome-Windows.lock`。

---

## 5. 新版兼容红线（三条，缺一条就废）

1. **`from app.core.jobs import PRIORITY_CONTROL` 必须能 import**。
   `widgets_board.py` / `quick_window.py` / `main_window.py` 都在用它。若新版 `jobs.py` 被上游重写，最起码把这三个常量与 `submit(priority=...)` 参数补回去。

2. **`WidgetsBoard(service, jobs, parent)` 的三个方法名不能变**：
   `set_devices(devices, known_power)`、`set_metrics(dict)`、`apply_power_states(dict)`、
   `set_columns(int)`、`set_opacity(float, persist=bool)`、`retheme()`、`toggle_collapse()`、`set_previewing(bool)`。
   主窗口的 11 处接线全依赖这些签名。

3. **`toggle_power(did, current=None)` 的第二个参数**：
   `quick_window.py` 没用它（改用 `set_power_state`），`widgets_board.py` 与 `main_window.py` 会用。
   若新版 `service.py` 被重写，保留这个可选参数即可，不传时行为与上游一致。

**上游 mijiaAPI 升级**：本次只用到了 `set_devices_prop` / `get_devices_prop` / 设备列表与 spec 读取，全部经 `app/core/service.py` 适配层。
升级后跑 `python -m tests.smoke_test`；它过了才说明适配层的上游接口还在。

---

## 6. 验证

### 6.1 自动测试（必须四项都 exit 0）

```powershell
cd MiHome-Windows-Main
.venv\Scripts\python.exe -X utf8 -m tests.smoke_test     # 上游接口兼容
.venv\Scripts\python.exe -X utf8 -m tests.board_test     # 挂件
.venv\Scripts\python.exe -X utf8 -m tests.latency_test   # 响应速度
.venv\Scripts\python.exe -X utf8 -m tests.theme_test     # 主题回归
```

期望末尾分别输出 `[OK] …`、`BOARD REGRESSION ALL PASS`、`LATENCY REGRESSION ALL PASS`、`THEME REGRESSION ALL PASS`。

> 依赖装在 `.venv`；装依赖要加国内源：
> `-i https://pypi.tuna.tsinghua.edu.cn/simple`（直连 PyPI 会卡在反向搜索）。

### 6.2 手工验收（8 条）

1. 托盘右键 →「显示桌面挂件」→ 桌面出现面板，标题 `米家 · 桌面挂件 · N`。
2. 「管理桌面挂件」→ 勾选 3 台 → 保存 → 面板变成 3 张卡片。
3. 拖动标题栏 / 面板空白处 → 窗口跟手移动；重启应用位置不丢。
4. 拖动「一行放多少个」到 5 → 面板立即变宽重排。
5. 拖动「透明度」滑块 → 面板实时变淡；鼠标移上去恢复不透明；关掉对话框后保持设定值。
6. 点右上角 ⌄（或托盘菜单）→ 收成细横条，显示 `⠿ 米家挂件 ●●○`；**单击**展开、**按住拖动**移动（不展开）。
7. 点卡片上的电源钮 → **立刻翻转**；网络正常时 ~0.3–1 秒内无回滚；断网时回滚并有提示。
8. 切换深/浅主题 → 挂件与折叠横条配色同步，无残留旧色。

---

## 7. 回退

只撤三处即可让应用回到上游行为（挂件是可选增强，缺了不会崩）：

1. 删 `app/core/board_store.py`、`app/ui/widgets_board.py`、`app/ui/board_manager.py`。
2. `main_window.py` 撤掉 4.4.1 的两个 import、4.4.3~4.4.6 的创建与菜单项、4.4.10 的方法组；其余 `if self._board is not None` 分支留着无害（`_board` 恒为 None）。
3. 托盘菜单撤 4.5.1 的两项。

`board.json` 留着无害，删了也只是丢挂件配置。

> `jobs.py` 的优先级队列**建议保留**：它修的是既有问题（点开关被慢请求堵住），与挂件无关。

---

## 8. 合并到 0.3.0 的记录（2026-10-18）

### 8.1 上游 0.3.0 相对 0.2.0 的变化（我合并时需要绕开的）

| 变化 | 位置 | 对本次合并的影响 |
|---|---|---|
| 升级到 **mijiaAPI 5.x**，规格改按 `spec_type`（云端 URN）获取，不再按 model 抓 home.miot-spec.com | `service.py`、`pyproject.toml` | 只改 `toggle_power` 一处，其余方法签名未变；环境需装 mijiaAPI 5.x |
| 新增**场景**功能：`SceneInfo`、`scene_card.py`、`scene_list`/`run_scene`、设置里的「场景入口」开关 | `models.py`、`service.py`、`scene_card.py`、`main_window.py`、`settings_dialog.py`、`tray/quick_window.py` | 我的锚点全部仍在；`settings_dialog._apply_styles` 的 label/desc/item 三个元组各多一项 `_scene_*`，合并时按其对齐追加 `_board_*` |
| 新增 `spec_zh.py`（规格中文化） | `app/core/spec_zh.py` | 无影响（挂件不碰 spec 解析） |
| 托盘菜单「管理托盘设备」改名为「管理托盘项目」 | `tray/controller.py` | 保留上游新文案，我只在其后追加挂件两项 |
| `_on_power_clicked/_done/_failed` 仍在原位置、签名未变 | `main_window.py` | 直接按 §4.4.16 替换 |
| 主窗口新增 `_rebuild_scene_grid` / `_finalize_grid` / `_load_scenes` 等 | `main_window.py` | 与挂件无交集；`_apply_devices` 末尾多了「预取场景」，我把 `_update_board_devices()` 插在它之前 |

### 8.2 合并结果（0.3.0 上的实际落点）

- **整份覆盖（上游未改动该文件）**：`app/core/jobs.py`、`app/ui/power_button.py`
- **整份新增**：`app/core/board_store.py`、`app/ui/widgets_board.py`、`app/ui/board_manager.py`、`tests/board_test.py`、`tests/latency_test.py`、`docs/CHANGES-桌面挂件.md`
- **按锚点重放**：`app/core/service.py`（1 处）、`app/ui/main_window.py`（11 处）、`app/ui/tray/controller.py`（2 处新增 + 菜单项）、`app/ui/tray/quick_window.py`（2 处）、`app/ui/settings_dialog.py`（4 处）、`tests/theme_test.py`（1 行注释）、`README.md`、`.gitignore`

### 8.3 环境

- 0.3.0 目录内的 `.venv` 由 0.2.0 的 venv 复制后升级：**mijiaAPI 4.4.0 → 5.0.0**，PySide6 6.12.0 不变。
- 依赖安装用国内源：`-i https://pypi.tuna.tsinghua.edu.cn/simple`（直连 PyPI 会卡在反向搜索）。

### 8.4 合并后的验证结论

四套测试在 0.3.0 上全部 **exit 0**：

```
tests.smoke_test    [OK] 适配层依赖的上游接口全部在位      （mijiaAPI 5.0.0）
tests.theme_test    THEME REGRESSION ALL PASS
tests.board_test    BOARD REGRESSION ALL PASS   （含 5b 折叠条拖动/单击、5c 标题栏拖动）
tests.latency_test  LATENCY REGRESSION ALL PASS
```

另做离屏渲染自检：挂件标题 `米家 · 桌面挂件 · 4`、折叠条 `⠿ 米家挂件 ●◐●○ ^`、
设置页「启动时显示桌面挂件」开关存在且读值正常，与 0.3.0 新增的场景页共存无冲突。

### 8.5 后续开发

新功能一律在 0.3.0 上开发；本文件继续作为「挂件与响应速度」这两块改动的唯一说明。
若再遇到上游发版，重复 §2 的顺序即可，§8.1 可作为"上游动过哪些地方"的参照。

### 8.6 合并后的增量改动

按时间顺序追加，只动 `app/ui/widgets_board.py` 与 `tests/board_test.py`（都在 §3 的
「整份复制」清单里，重放到别版时带着走即可）。

| 改动 | 位置 | 说明 |
|---|---|---|
| 挂件标题带产品名 + 折叠条加标题 | `_BOARD_TITLE`/`_BAR_TITLE`/`_bar_title` | 展开态 `米家 · 桌面挂件 · N`；折叠条 `⠿ 米家挂件 ●●○ ^`，改产品名只改 `_APP_LABEL` |
| 折叠横条可拖动 | `eventFilter` | 折叠态整条可拖，单击展开，靠 `_DRAG_THRESHOLD` 区分 |
| **展开态单击标题行收起** | `eventFilter` | 标题行、面板空白、折叠横条三块统一走"按下记原点 → 超阈值算拖拽 → 否则算单击"；单击动作按区域分派：标题行收起、空白无动作、折叠条展开。按下消费事件以保住后续 move/release；卡片与按钮是子控件，不受影响 |
| **设备定时开关**（独立功能，见 §9） | 新增 `schedule_store.py`/`scheduler.py`/`schedule_dialog.py`；`main_window.py` 接线 | 每台设备可配多条「每天 HH:MM 打开/关闭」规则 |

> 事件过滤统一后，字段从 `_drag_origin`/`_bar_drag_origin`/`_bar_moved` 收敛为
> `_press_origin`/`_press_moved`。回归用例：`board_test` 5b（折叠条）、5c（标题栏拖动）、
> 5d（单击标题行收起）。

---

## 9. 设备定时开关（0.3.0 新增功能）

### 9.1 需求与边界

给指定设备配「每天某时刻打开/关闭」，用来替代米家 APP 的定时能力
（家里没有光线传感器，需要按固定时间点灯）。**每台设备可配多条规则**
（7:00 开灯 + 23:00 关灯彼此独立）。

明确不做：

- **不做系统级调度**。定时只在应用运行时触发（窗口开着或最小化到托盘）；
  关掉进程不会执行。要做到"程序不开也开灯"需接 Windows 任务计划，不在此范围。
- **不补执行错过的时刻**。应用没开/电脑休眠导致错过的定时，醒来不补开灯
  （白天突然亮灯更烦人），只提示一次"今天已错过"。
- 不做星期几、不做节假日、不做一次性的"仅今天"。

### 9.2 文件

| 文件 | 类型 | 职责 |
|---|---|---|
| `app/core/schedule_store.py` | 新增 | `schedules.json` 读写与校验（时间零填充、非法丢弃、按时间排序） |
| `app/core/scheduler.py` | 新增 | 纯逻辑判定：给定 `now` 返回「该执行的规则」与「今天新错过的规则」，不依赖 Qt |
| `app/ui/schedule_dialog.py` | 新增 | 定时任务对话框：新增 / 启停 / 删除；设备下拉支持输入搜索（包含匹配、大小写不敏感、弹层独立上色） |
| `tests/schedule_test.py` | 新增 | 6 组回归：存取、判定、错过、停用、主窗口接线、对话框 |
| `app/ui/main_window.py` | 改 | 10 秒定时器、执行与回写、菜单入口、退出停表 |
| `app/ui/tray/controller.py` | 改 | 托盘菜单「定时任务」 |

### 9.3 数据结构（`schedules.json`）

```json
{
  "version": 1,
  "rules": [
    {"id": "a1b2c3d4e5f6", "did": "123456", "time": "07:00",
     "action": "on", "enabled": true, "last_fired": "2026-10-18"}
  ]
}
```

- `time`：24 小时制零填充 `HH:MM`，写入前统一 `normalize_time()`；
- `action`：`"on" | "off"`；
- `enabled`：停用保留规则不执行（与删除区分开）；
- `last_fired`：最后成功执行日期，**同一天只执行一次**，重启也不会重复；
- 设备被移除后规则保留，界面标「设备已移除」并可删除。

### 9.4 判定语义（`Scheduler.tick(now)`）

只认「当前 HH:MM 等于规则时刻」，不是「已过时刻」：

| 场景 | 结果 |
|---|---|
| 06:59 而规则 07:00 | 不执行 |
| 07:00 那一分钟内（10 秒一扫） | 执行 |
| 07:00 已执行过，同日再扫 | 跳过（`last_fired`） |
| 08:00 才发现 07:00 的规则 | 不补执行，报一次「错过」 |
| 规则停用 | 既不执行也不报错过 |
| 新建规则且今天该时刻已过 | `last_fired` 直接记为今天，从明天起生效 |

「错过」按 (规则 id, 日期) 去重，同一天只提醒一次，不会每 10 秒刷一条。

### 9.5 执行链路

```
_schedule_timer(10s) → _on_schedule_tick()
    → Scheduler.tick()  → due / missed
    → missed: Toast 汇总一次
    → due:    _run_schedule_rule(rule)
                 → jobs.submit(set_power_state, priority=PRIORITY_CONTROL)
                 → 成功: schedule_store.mark_fired + 同步三处界面 + Toast
                 → 失败: Toast 报错，pending 清理，不写 last_fired
```

- 走 `PRIORITY_CONTROL`：定时点和用户手点同等优先级，不排在轮询后面。
- `_schedule_pending` 集合：两次 tick 之间同一条规则不会重复提交（成功回调
  之前 `last_fired` 还没落盘，靠它挡住同一分钟内的重复触发）。
- 成功后同步三处：主界面卡片、托盘快捷窗口、桌面挂件。

### 9.6 时间录入口径（最终实现）

时间用**「时 + 分」两个下拉**（时 00–23、分每 5 分钟一档共 12 项），
点选即可、不用记格式也不会输错。要精确到分钟把 `range(0, 60, 5)`
改成 `range(60)` 即可。

曾试过三种方案，记录以免重复踩：

| 方案 | 结果 |
|---|---|
| `QTimeEdit` | **不可用**：一套自定义样式表，内部编辑框文字就不再绘制（整框空白）；去掉样式表字能显示但观感与其他控件不一致 |
| `QLineEdit` + 校验 | 可用，但要用户手打时间，容易写错格式 |
| **时/分 两个 `QComboBox`** | 采用：纯点选、主题一致、无格式错误 |

### 9.7 踩坑记录

**`QCompleter` 的弹层不继承下拉框样式**。它自己造一个 `QListView` 当候选
列表，`QComboBox QAbstractItemView` 那套 QSS 管不到——深色主题下会出现
浅底浅字、候选几乎看不清。必须对 `completer.popup()` 单独 setStyleSheet，
并在主题切换时重刷（`retheme` → `_style_completer_popup`）。

**可搜索下拉不能用 `currentIndex()` 取设备**。输入片段后用户可能没点选，
`currentIndex` 会停在旧值。`_resolve_device()` 的做法：先按完整标签精确
匹配，再退化为「唯一包含匹配」——命中多台就视为有歧义、不擅自选一台。

**程序化 `setEditText()` 不会触发补全过滤**，那只在真实键入时生效。
测试里要验证过滤，得手动 `completer.setCompletionPrefix(...)` 再读
`completionModel()`；`QTest.keyClicks` 也打不了中文（`qasciikey` assert）。

**"对话框打不开"可能只是看不见**。`OverlayDialog._fade_in` 先把窗口设成
全透明再动画到 1.0；若动画因窗口合成器原因没跑，窗口会永远停在全透明，
表现就是"点了没反应"。已在 `_fade_in` 里加 400ms 兜底复位
（`_ensure_visible_opacity`）。另外 `run.py` 运行期会隐藏控制台，
**槽函数里的异常不会弹出任何提示**，所以 `show_schedules` 加了 try/except
并弹 `QMessageBox`——出问题要看得见，而不是静默失败。

**父窗口最小化时 `isHidden()` 仍为 False**。以最小化的主窗口为父窗口，
对话框会跟着最小化，看起来像没打开。入口处改为
`None if (self.isHidden() or self.isMinimized()) else self`。

### 9.7 验证

```
tests.schedule_test  SCHEDULE REGRESSION ALL PASS
```

覆盖：时间规范化与非法丢弃、同设备多条规则、到点只触发一次、同日不重复、
停用静默、错过只报一次、新建"今天已过"不触发、主窗口真实调用
`set_power_state` 并回写界面、失败不写 `last_fired` 且清理 pending。

---

## 10. 多键面板虚拟子设备的开关（0.3.0 修复）

### 10.1 现象

实体三键开关在米家 APP 里被拆成 1 个主设备 + 3 个虚拟设备（`厨房三开` /
`卫生间` / `厨房1` / `厨房2`）。米家 APP 里四路都能单独开关，
本程序里**只有主设备能开关，虚拟设备点了没反应**（连开关按钮都不显示）。

### 10.2 根因

云端下发的多键面板规格里，三路属性是：

```
on-2  bool rw  siid=2 piid=1      ← 第 1 路
on-3  bool rw  siid=3 piid=1      ← 第 2 路
on-4  bool rw  siid=4 piid=1      ← 第 3 路
```

（名字里的 `-2/-3/-4` 是上游 `devices.py` 的 `_deduplicate_names` 补的 siid 后缀。）

虚拟设备的 did 形如 `1234567890.s2 / .s3 / .s4`——**后缀 N 就是这一路的 siid**。

而 `_find_on_method()` 只取规格里**第一条** `on-*`（即 siid 2），于是：

- 三个虚拟设备全部向 `siid=2` 发请求；
- 云端对其 did + 不匹配的 siid 返回非 0 → `power_states` 记为 `None` →
  界面判定"无开关能力" → 按钮根本不显示。

### 10.3 修复

1. `_gang_from_did(did)`：正则 `\.s(\d+)$` 取出通道号。
2. `_find_on_method(info, did)`：按 `method.siid == 通道号` 选属性；
   后缀与 siid 对不上时按顺序兜底（`s2`→第 1 条、`s3`→第 2 条）。
   父设备（无后缀）保持原行为取第一条——用户反馈主设备本来就能开关。
3. 读（`power_states`）与写（`set_power_state`）都传入 did。

### 10.4 兜底：云端只认父 did 的情况

不同固件/版本上，云端对"子 did + 多路 siid"的接受度不一致。为两种都能用：

- **写**：先用子 did 写；失败且该 did 带后缀 → 用 **父 did + 同一 siid** 重试一次。
- **读**：批量读失败（code≠0）的子设备，收集后用父 did + 同一 siid 补读一次，
  结果仍记在子 did 名下。

补读的键必须是 **(父did, siid)**：同一父设备的多路子设备共用父 did，
只按 did 建映射会把三路结果全算到最后一路头上（第一版实现踩过，测试已覆盖）。

### 10.5 验证

```
tests.subdevice_test  SUBDEVICE REGRESSION ALL PASS
```

覆盖：后缀解析（含 `blt.3.x` 这种带点的 mac 风格 did 不误判）、三路 siid 选取、
读写请求的 siid 正确且同批不重复、云端只认父 did 时的读写兜底、父 did 也失败时
保持 `None` 不抛异常。

### 10.6 真实云端实测结论（决定性证据）

用 `tools/diag_switch.py` 在真实账号上测出来的结果：

```
子 1234567891.s2 siid=2 -> code=-704083036      ← 子 did 一律被拒
子 1234567891.s2 siid=3 -> code=-704083036
父 1234567891    siid=2 -> code=0 value=False   ← 父 did 正常
父 1234567891    siid=3 -> code=0 value=False
父 1234567891    siid=4 -> code=-704040002      ← 2 键没有第 4 路（越界）
```

**结论：米家云端不接受虚拟子设备的 did 做属性读写，只认「父 did + 该路 siid」。**

错误码对照：
- `-704083036`：该 did 不接受这次读取（子 did 被拒）
- `-704040002`：siid 越界（2 键面板没有第 4 路，说明"路数 = siid−1"成立）

据此把实现从"先子 did、失败再退父 did"改成**学习式**：

1. `_switch_target(did)`：子设备默认先按自身 did 发；
2. 失败后父 did 兜底；父 did 成功即把该家族记为 `parent`；
3. 之后（含后续轮询）直接发父 did，不再每次白跑一次注定失败的请求。

首次启动每个家族多一次往返，之后恢复正常。偏好只记在内存，重启后重新学习一次。

端到端实测（真实云端，只读）：

```
第 1 次：五台子设备全部读到 False（其中父 did 兜底成功）
学到：{'1234567891': 'parent', '1234567890': 'parent'}
第 2 次：直接走父 did，全部读到 False
```

### 10.7 顺带补上的日志

排查时才发现 **0.3.0 根本没有日志落地**：`run.py` 运行期隐藏控制台，
`logger.warning` 全打到看不见的 stderr 上，`debug.log` 压根不存在。

已补：`app.setup_logging()`（`app/__init__.py`）在 `run.py` 启动时调用，
日志写到数据目录的 `debug.log`：

- 源码模式：`<项目根>/debug.log`
- 构建版：`%LOCALAPPDATA%\MiHome-Windows\debug.log`

另外 `power_states` 对"有开关能力却读不到状态"的设备逐台记一次
`开关状态读不到（按钮将隐藏）did=… model=… spec_type=… siid=… code=…`，
不随 5 秒轮询刷屏。

### 10.8 诊断脚本

`tools/diag_switch.py`（只读，不改设备状态）：

```powershell
cd MiHome-Windows-Main
.venv\Scripts\python.exe -X utf8 -m tools.diag_switch
```

打印：账号里的虚拟子设备分组、规格里 on 属性的 siid 分布、
用「子 did」「父 did」分别读 siid 2/3/4 的原始云端返回（code/message）。
以后再遇到"某类设备控制不了"，先跑它，一次就能看清云端认什么。

---

## 11. 配置"每次启动都丢"的根因（0.3.0 修复）

### 11.1 现象

用户配好桌面挂件的设备、定时任务规则后，关掉程序再 `start.bat`，
之前的设置全部回到默认（挂件空、定时规则空，托盘配置也空了）。

### 11.2 根因：**是测试把我的真实配置清空了**

排查时对比文件修改时间：`board.json` / `schedules.json` / `tray.json` /
`settings.json` 的 mtime 全部落在跑回归测试的那几分钟里。

原因：0.3.0 源码模式的数据目录就是**项目根**（`_json_store.data_dir()`），
而各测试模块为了"跑完不留脏数据"，末尾会显式清空：

```python
board_store.save_devices([])     # board_test
schedule_store.save([])          # schedule_test
tray_store.save([])              # theme_test
settings_store.set_theme_mode("system")
```

于是**每跑一次测试，用户的真实配置就被清空一次**。用户每次看到
"设置又没了"，实际是我的测试干的，不是程序没保存。

### 11.3 验证程序本身是好的

跨进程持久化实测（写 → 另起一个进程读回，模拟关掉再启动）：

```
进程 A（写入）: WROTE
进程 B（读回）: {"devices": ["1234567890.s3","1234567890.s4"], "columns": 4,
                "opacity": 0.55, "autostart": true,
                "rules": [["1234567890.s3","07:00","on"],["1234567890.s4","23:30","off"]]}
```

挂件设备、列数、透明度、启动显示、定时规则**全部正确读回**。
所以持久化链路（`board_store` / `schedule_store` / `_json_store`）本身没问题。

### 11.4 修复

1. `_json_store.data_dir()` 支持环境变量 **`MIHOME_DATA_DIR`** 覆盖，
   优先级高于构建版/源码目录。
2. 新增 `tests/_isolate.py`：`isolate()` 把数据目录指向临时目录，
   进程退出时清理。**所有测试模块在导入 app 之前调用它。**
3. `board_test` / `schedule_test` 各加一条防回归断言：
   `assert "mihome-test-" in data_dir()`——隔离失效时测试直接失败，
   而不是悄悄清空用户配置。
4. `write_json` 写失败不再静默：`logger.warning("写入配置失败 …（本次改动不会保存）")`。
   写在文件系统层面的失败（只读目录、杀软锁文件）现在能在 `debug.log` 里看到。

### 11.5 验证

连续跑六套测试前后对比真实配置文件 mtime：

```
跑测试前: board.json=16:11:00  schedules.json=16:11:03  tray.json=16:11:07  settings.json=16:11:07
跑测试后: board.json=16:11:00  schedules.json=16:11:03  tray.json=16:11:07  settings.json=16:11:07
✅ 真实配置未被改动
```

### 11.6 教训

「测试要清理自己产生的数据」和「测试不能碰真实数据目录」是两件事。
只做前者时，清理动作正好命中用户的真实文件。正确做法是**先隔离，
再谈清理**：隔离之后，测试里那些 `save([])` 只是冗余，无害。

---

## 12. 子设备"看不到开关"的第二层原因：状态记忆把失败当成了"无能力"

### 12.1 现象

修好 did/siid 映射后，`1234567891`、`1234567890` 两个面板的子设备正常了，
但 `1234567892`（餐厅厨房三开）的子设备**仍然没有开关按钮**。
诊断显示云端对这个父 did 的 siid 2/3/4 全部返回 `code=0`，请求本身没问题。

### 12.2 根因：`None` 被当成"确认无开关能力"，且永不重试

`_known_power[did]` 的三态语义是：`True/False` 上次已知状态、
`None` 确认没有开关能力。轮询逻辑是：

```python
if not force:   # 首次探测
    dids = [d for d in dids if d not in self._known_power]
else:           # 每 5 秒轮询
    dids = [d for d in dids if self._known_power.get(d) is not None]
```

而**读失败**（子 did 被云端拒绝）与**离线**都被写成了 `None`：

- `power_states` 读失败 → `result[did] = None`；
- `_apply_power_state` 发现设备离线 → `self._known_power[did] = None`。

于是这些设备在**修复之前**就被记成"无能力"，还被写进 `devices_cache.json`
持久化。启动时载入后：

- `force=False`：它在 `_known_power` 里 → 跳过；
- `force=True`：值是 `None` → 跳过。

**两条探测路径都跳过，永远不再请求** → 按钮永远不会出现。
`1234567892` 就是这么被"冻结"的；另外两个家族是在修复后第一次探测就成功了。

### 12.3 修复

1. **读失败不再写进结果**（`power_states` 里从 `result[owner] = None` 改成
   只记入待兜底清单，兜底仍失败就不返回该 did）。保持"未知"，下一轮还能重试。
2. **离线不再写 `None`**（`_apply_power_state` 直接 return，保留上次已知状态；
   卡片本来就按离线灰置）。避免设备重新上线后永久失联。
3. **`devices_cache.json` 版本 2 → 3**，一次性作废旧缓存，让所有设备重新探测。
4. 日志文案从"（按钮将隐藏）"改成"读取失败"，避免把临时失败说成定性结论。

### 12.4 验证

真实云端（只读）：

```
第 1 次：1234567892.s2/.s3/.s4 -> False / False / False（父 did 兜底成功）
学到：{'1234567892': 'parent'}
第 2 次：直接走父 did，三路依然全部读到
```

`tests/subdevice_test.py` 新增两条断言：

- 兜底后仍失败 → **结果里不含该 did**（而不是 None），保证下一轮可重试；
- 真无 `on` 属性 → 仍显式返回 `None`，这类设备不反复探测；
- 兜底成功一次后，第二轮**不再发子 did** 的注定失败请求。

### 12.5 教训

"读不到"和"没有"必须是两个状态。把临时失败降级成"确认无能力"，
再叠加"无能力就不再探测"的优化，就会变成**永久性误判**——
而且它会被持久化，连重启都救不回来。

---

## 13. 开关按钮"绿一下又变灰"——在途轮询覆盖本地操作

### 13.1 现象

点开关 → 按钮立刻变绿 → **不到 1 秒又变灰** → 过一会儿（约 5 秒）才真的变绿。
关闭同理。子设备上特别明显。

### 13.2 根因：轮询与用户操作的竞态

```
t0  轮询请求发出（此时设备是"关"）
t1  用户点开关 → 乐观翻转成绿 → 写入请求发出
t2  写入成功 → 绿、_known_power=True
t3  t0 那条轮询才返回，携带旧值 False → 覆盖成灰   ← 用户看到的回弹
t4  下一轮轮询（写完之后发出的）→ 读到 True → 变绿
```

子设备写入要两次请求（子 did 失败 → 父 did 重试），窗口期更长，所以更明显。

### 13.3 修复：状态版本号 + 快照比对

`main_window` 与 `widgets_board` 各自维护 `_power_gen: dict[did, int]`：

- 任何**本地操作**（点击、写入成功/失败回滚、挂件回传、定时执行）→ 该 did 的版本号 +1；
- 发出轮询时 `snapshot = dict(_power_gen)` 并随回调带回；
- 结果回来时逐台比对：`snapshot[did] != _power_gen[did]` 说明这台设备在请求
  发出后被动过，**丢弃它的旧值**（不写记忆、不动卡片、不推托盘与挂件）。

没有快照的调用（内部推送）保持原行为，不影响正常刷新。

### 13.4 验证

`tests/race_test.py` 直接复现这个时序：

```
1. 主窗口：过期轮询不覆盖本地操作 OK
2. 新的轮询结果仍能正常更新 OK
3. 无快照的推送不受影响 OK
4. 挂件：过期轮询不覆盖本地操作 OK
RACE REGRESSION ALL PASS
```

第 1 条就是用户看到的现象：点击后翻转成 True，再喂一条"点击之前发出的
轮询结果 False"，断言状态仍然是 True（修复前会被打回 False）。

### 13.5 教训

"乐观 UI"必须配一个**丢弃过期响应**的机制。只要存在后台轮询，
"先翻转再等网络"就一定会被旧结果打回；版本号快照是最小代价的做法，
比"等写入完成后 N 秒忽略轮询"更精确，也不会引入魔法时间窗。

---

## 14. 重启后挂件是否自动打开

### 14.1 原来的行为（会让人误解）

只有勾选设置里的「启动时显示桌面挂件」才会自动显示；不勾就**一律隐藏**，
不管上次退出时挂件是开着的。用户打开挂件、关程序、再 `start.bat`，
挂件不见了——像是"设置没保存"。

### 14.2 现在的规则

| 设置「启动时显示」 | 上次退出时挂件 | 启动后 |
|---|---|---|
| 勾选 | 开 / 关 | **显示**（强制） |
| 未勾 | 开 | **显示**（恢复上次状态） |
| 未勾 | 关 | 隐藏 |
| 未勾 | 首次运行（无记录） | 隐藏 |

实现：`board.json` 增加 `visible` 字段（`board_store.get_visible/set_visible`），
与 `autostart` 分工明确：

- `autostart=True`：每次启动都开，不看上次；
- `autostart=False`（默认）：跟随上次状态。

### 14.3 关键细节：只记"用户意图"

`visible` 只在**用户动作**时写入：

- `show_board()`（托盘菜单 / 更多菜单 / 启动恢复）→ `visible=True`
- 挂件右上角 ×（`hide_by_user()`）→ `visible=False`
- 托盘菜单「隐藏桌面挂件」（`hide_board()`）→ `visible=False`

**应用退出时的被动 `hide()`、启动时不满足条件而隐藏，都不写**。
否则一关程序就把"上次是开着的"抹成 False，重启永远回不来——
这是本功能最容易写错的地方，`board_test` 第 9 组专门断言了这一点。

### 14.4 验证

`tests/board_test.py` 第 9 组覆盖四种组合 + 被动隐藏不覆盖用户意图：

```
9. 重启恢复显示的四种组合 + 被动隐藏不覆盖 OK
```

---

## 15. 打包成 exe 与安装包

### 15.1 现状

项目自带 Nuitka 构建脚本（`build.ps1` / `build_msvc.bat`）。本次改动**不需要**
新增打包配置：新模块（`board_store`/`widgets_board`/`board_manager`/
`schedule_store`/`scheduler`/`schedule_dialog`）都走正常 import，Nuitka 会
自动跟随；无新增数据文件。

只在 `$NuitkaArgs` 里补了一条 `--nofollow-import-to=tools,tests`，
防止 `tools/diag_switch.py`、测试模块被间接带进产物。

### 15.2 修掉的构建脚本缺陷

原脚本把 MSVC 路径**写死**为：

```
C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat
```

装在别的版本（如 VS 18）、别的盘符、或只装了 Community 版时，会直接报
"VS Build Tools 2022 not found"——让人误以为必须重装成 2022。现在改为：

1. `$env:VSINSTALLDIR`（在 VS 开发者命令提示符里启动时）；
2. `vswhere -products * -latest -find VC\Auxiliary\Build\vcvars64.bat`（官方定位工具）；
3. 兜底递归扫描 `Program Files*` 下的任意 `vcvars64.bat`。

仍找不到时脚本直接报错，并列出必须装的组件名，而不是让人自己猜。

> 本项目的构建**只有 MSVC 一条路**（与原作者脚本一致）：必须装
> Visual Studio 生成工具 + Windows SDK，不做任何降级工具链。

### 15.3 安装包

`dist\` 是"exe + 依赖目录"的免安装形态，不能只拷 exe。要可安装/可卸载的包：

- `installer.iss`：Inno Setup 脚本（6 / 7 都可编），装到 `%LOCALAPPDATA%\Programs\MiHome-Windows`
  （**免管理员权限**），带开始菜单快捷方式、可选桌面图标、卸载程序；
  卸载时询问是否连 `%LOCALAPPDATA%\MiHome-Windows` 的配置一起删。
- `make_installer.ps1`：自动找 `ISCC.exe`、从 `app/__init__.py` 读版本号、
  产出 `dist\installer\MiHome-Windows-Setup-<版本>.exe`。

### 15.4 脚本编码坑

`build.ps1` / `make_installer.ps1` 含中文，必须存成 **UTF-8 with BOM**：
Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当 ANSI 读，中文变乱码后
连语法解析都可能失败（实测报"缺少右大括号"）。

**改完必须复核 BOM**——不少工具（含各类批量替换、自动格式化、脚本化写文件）
会静默把文件重写成无 BOM 的 UTF-8，肉眼看不出来，一跑就崩。检查前 3 字节应为
`EF BB BF`：

```powershell
$b = [System.IO.File]::ReadAllBytes("build.ps1"); "{0:X2} {1:X2} {2:X2}" -f $b[0],$b[1],$b[2]
```

`build_msvc.bat` 现在**优先用 `pwsh`**（PowerShell 7 对有无 BOM 都能正确解码），
没有 `pwsh` 才退回 `powershell` 5.1——万一 BOM 又丢了，双击构建也不会莫名报语法错。


### 15.5 实际构建验证记录（2026-10-18）

在本机完成过一次完整构建，环境与结果如下，可作为"能编出来"的对照基线：

| 项目 | 实际情况 |
|---|---|
| 源码路径 | `E:\AI-IDE\DeepSeekHarness\MiHome-Windows-Main`（**纯 ASCII**） |
| Python | 3.11.9 |
| 编译器 | VS Community 2026（MSVC 14.5），装在 `D:\Microsoft Visual Studio\18\Community` |
| Windows SDK | **10.0.22621.0**（`Windows Kits\10\Include`） |
| Inno Setup | **7**（`D:\Inno Setup 7\ISCC.exe`），含 ChineseSimplified.isl |
| Nuitka | 4.3.1 |
| 编译耗时 | **16 分 22 秒**（4 并发） |

产物：

```
dist\MiHome-Windows.exe                          35.1 MB  （含依赖共 193 MB / 749 文件）
dist\installer\MiHome-Windows-Setup-0.3.0.exe    37.9 MB
```

校验方式（没有真实启动，避免与用户已装版本抢单实例锁）：

- PE 头：机器类型 `0x8664`（x64）、子系统 `2`（Windows GUI，无控制台窗口）；
- 版本信息：ProductVersion/FileVersion = `0.3.0.0`；
- 二进制内可检出本次新增功能的标识：`board.json`、`schedules.json`、
  `widgets_board`、`schedule_dialog`、`scheduler`，以及中文界面串
  「桌面挂件」「定时任务」「定时开关」——说明新功能确实进了产物。

踩过的三道关（都在脚本里自动处理了，重装环境时按此排查）：

1. **路径必须纯 ASCII**：`depends.exe` 把中文写成 `0x04`，Nuitka 断言崩溃。
2. **Windows SDK 必须装**：只有 MSVC 时 vcvars 能"激活成功"但编译必失败，
   Nuitka 还会静默退回它自带的工具链、编出来的产物启动即崩。
   脚本现在会提前检查 SDK 并报出确切组件名。
3. **VS 装在中文路径时的 Nuitka bug**：查 MSVC 路径的 vswhere 少了 `-utf8`，
   中文路径解码崩溃。脚本每次构建自动检测并打补丁（幂等）。

---

## 16. 子设备状态显示延迟：真凶是"子 did 的拒绝要等 6 秒"

### 16.1 量化（`tools/diag_poll_timing.py`，真实云端）

用真实账号测轮询耗时，结果非常明确：

| 场景 | 耗时 | 请求明细 |
|---|---|---|
| 已学过后 · 仅 8 台子设备 | **52 ms** | 8 条 / 51 ms |
| 已学过后 · 全部 32 台在线 | 766 ms | 15 条/143 ms + 13 条/622 ms |
| **首次未学习** · 仅 8 台子设备 | **5986 ms** | 8 条/**5932 ms** + 8 条/54 ms |
| **首次未学习** · 全部 32 台在线 | **12227 ms** | 15 条/5992 ms + 13 条/6184 ms + 8 条/52 ms |

关键发现：**云端对子 did（`<父did>.sN`）的拒绝不是快速返回，而是拖到约
6 秒才给 `-704083036`**；而同样的读取换成「父 did + 该路 siid」只要 50 ms 左右。

### 16.2 为什么之前慢

上一版为了"兼容只认子 did 的云端"，策略是**先发子 did、失败再退回父 did**
（并在成功后记住偏好）。问题是每次**应用启动后的第一轮轮询**都处于"未学习"
状态，于是先吃一次 6 秒的拒绝；整批 32 台设备时，失败请求按批次拆开，
两次都撞上 6 秒，首轮总共要 12 秒。用户看到的就是"子设备状态半天不刷新"。

### 16.3 修复

1. **不再试探子 did**：`_switch_target()` 对 `.sN` 设备直接返回父 did。
   既然父 did 是唯一可用入口，就不该为兼容"别的厂商可能认子 did"而
   每次都白付 6 秒。真要换，只改这一行的返回值即可。
2. 删掉 `_retry_with_parent()` 与"学习偏好"逻辑（连同它们的调用点），
   写入路径同样直接发父 did。
3. **批量读取上限 15 → 20**：32 台在线设备的轮询从 3 次请求降到 2 次。
4. **轮询间隔 5 秒 → 3 秒**：稳态一次整批读 0.4~0.6 秒，加上间隔后
   外部状态变化最迟约 4 秒内跟上（原先最坏 6~12 秒）。

### 16.4 修复后实测

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 首次 · 仅子设备 | 5986 ms | **57 ms** |
| 首次 · 全部在线 | 12227 ms | **880 ms** |
| 稳态 · 全部在线 | 766 ms | **551 ms** |

即：首轮不再有 6 秒白等，稳态也少了一次往返；配合 3 秒轮询，
子设备状态刷新的观感从"要等十几秒"变成"基本跟手"。

### 16.5 回归

`tests/subdevice_test.py` 第 3/5 组改为锁死"**不允许出现任何子 did 请求**"：

- 三次写入 = 三次请求，全部发父 did，siid 分别为 2/3/4；
- 三路批量读只发 **1 次**请求，含 3 条（父 did + siid 2/3/4）；
- 6 台子设备（两组家族）合并进 **1 次**请求；
- 读取被拒时不落 `None`（否则会被永久跳过）、无 on 属性时仍落 `None`。

诊断脚本 `tools/diag_poll_timing.py` 保留，环境或网络变化后可随时复测。

---

## 17. 收尾：目录改名 / 构建方式收敛 / 隐私自检

### 17.1 目录改名

项目目录从"中文名 + 版本号"改为 **`MiHome-Windows-Main`**，两个原因：

1. Nuitka 依赖的 `depends.exe` 不认非 ASCII 路径（详见 §15.5 第 1 条），
   构建必须发生在英文目录下——把这条约束直接固化到目录名上，比每次提醒更可靠；
2. 版本号只有一个信源（`app/__init__.py`），目录名再带一份版本号迟早对不上。

改名后同步的位置：README 全部命令示例、本文档的命令示例、
`.venv\Scripts\activate*` 的 `VIRTUAL_ENV`、`.venv\pyvenv.cfg` 的 `command`。
旧目录已删除，仓库只剩这一个副本。

### 17.2 构建方式收敛为 MSVC 一条路

- 删除"备用编译器"的双击入口脚本，`build.ps1` 里对应的开关参数与分支一并移除，
  只保留 MSVC 一条路；
- 找不到 MSVC 时**报错并指出缺哪个组件**，不再提供"降级编译"的退路；
- 理由：降级工具链编出的产物在本项目启动即崩，且与原作者脚本的构建方式不一致，
  留着只会让人白踩一次坑。

### 17.3 构建期隐私自检

`build.ps1` 在编译完成后扫一遍 `dist\`：只要出现登录凭据或用户配置文件
（`auth.json`、`devices_cache.json`、`board.json`、`schedules.json`、
`settings.json`、`tray.json`、`workbench.json`、`device_icons.json`）
就 **exit 1**，构建直接失败。

### 17.4 "卸载重装不用扫码"的结论

**产物不含任何个人米家信息**；不用扫码是因为登录态本来就存在用户目录里，
卸载程序不会动它。链条如下：

| 环节 | 位置 | 卸载时 |
|---|---|---|
| 登录凭据 | `C:\Users\<用户>\.config\mijia-api\auth.json`（mijiaAPI 的凭证，与 CLI 共用） | 保留（卸载程序不管这里） |
| 设备缓存与配置 | `%LOCALAPPDATA%\MiHome-Windows\` | 弹窗询问，默认保留 |
| 程序本体 | `%LOCALAPPDATA%\Programs\MiHome-Windows\` | 删除 |

实测证据（本机构建产物）：

- `dist\` 748 个文件逐个做二进制搜索，`auth.json` 里的 `serviceToken`、
  `passToken`、`cUserId`、`ssecurity` 等 8 个长字符串 → **0 命中**；
- 再搜真实设备 did、设备名、账号昵称 → **0 命中**；
- exe/安装包内检出的是 `board.json`、`widgets_board` 这类**文件名与模块名**，
  属于代码字符串，不是用户数据。

要复现"全新安装"的扫码流程：把 `auth.json` 临时改名再启动即可
（`%LOCALAPPDATA%\MiHome-Windows` 也可一并改名，验证配置初始化）。

---

## 18. 扫码页一直转圈：投递定时器被模态窗堵死（0.3.1 修复）

### 18.1 现象

把 exe 装到**别人的电脑**（本机没有登录凭据）后，登录窗只显示
「正在获取登录二维码…」，二维码永远不出来，也没有任何报错；点「重新获取二维码」
同样无效。开发机上永远复现不了——因为开发机一直有 `auth.json`，扫码这条路
从没被走过一次。

### 18.2 复现方法

不需要卸载、也不碰自己的凭据，把「家目录」指到空目录即可模拟新机器：

```powershell
$env:USERPROFILE="E:\tmp\fakehome"        # 让 ~/.config/mijia-api/auth.json 不存在
$env:MIHOME_DATA_DIR="E:\tmp\data"        # 配置也写到临时目录
.venv\Scripts\python.exe run.py           # 打包版直接对 exe 设同样的环境变量
```

### 18.3 根因（`faulthandler` 线程栈）

卡住的瞬间三个栈是决定性的：

```
Thread（worker）：queue.get() 空闲 —— 任务早就执行完了
Thread（worker2）：queue.get() 空闲
Main：login_dialog.exec_and_wait → main_window._after_login_check
      → jobs._deliver_results → 回调
```

worker 4 秒就把二维码数据取回来了，但**结果要靠 JobExecutor 的投递定时器送到界面**，
而那个定时器正被自己的回调堵在栈上：回调里直接开了模态登录窗（`QDialog.exec()`），
在定时器的栈上又起了一层事件循环。Qt 不会在回调返回前再次触发同一个定时器，
于是结果一直躺在队列里，界面永远停在「正在获取登录二维码…」。

一句话：**在"送结果"的回调里开模态窗，等于把"送结果"的通道自己堵住。**

### 18.4 修复（四层，前两条治本）

1. **投递层**（治本）：`jobs._deliver_results` 不再直接调用回调，而是
   `QTimer.singleShot(0, ...)` 再排一拍。回调不在定时器栈上，里面开多久的
   模态窗都不影响投递；顺序仍按入队顺序。
2. **主窗口**：`_after_login_check` 改成 `QTimer.singleShot(0, ...)` 后才开
   登录窗（`_open_login_dialog`），不再从回调里直接 `exec()`。
3. **登录窗**：补全过程日志（开始 / 取到 / 已显示 / 失败，含耗时）、
   二维码渲染失败显性报错并可重试、40 秒看门狗（超时提示检查网络/代理）。
   以后再卡，日志里一定有话说，不会再无声转圈。
4. **网络层**：新增 `app/core/net_guard.py`，给上游那些没写 `timeout` 的
   `requests.get` 注入默认超时（连接 10s / 读 30s）。扫码长轮询自己传的
   120s 不受影响。断网、代理失效、IPv6 不通时会在 10~30 秒内报错而不是无限等。

### 18.5 回归

新增 `tests/dispatch_test.py`，把事故复现成用例：回调里跑嵌套事件循环时，
新提交的任务结果必须**在嵌套循环内部**送达。

实测双向验证：把 `_deliver_results` 临时改回直接调用，第 1 项断言失败
（`结果被拖到嵌套循环结束后才送达`）；恢复后全绿。另外锁住结果顺序与
「某个回调抛异常不拖垮后续投递」。

### 18.6 顺带修掉的构建坑

`build.ps1` 现在把 Nuitka 缓存固定到项目内 `.nuitka-cache`：默认的
`%LOCALAPPDATA%\Nuitka` 在受限账户/受管控机器上不可写，会以
`failed to create cache directory` 直接构建失败（本次就撞了一次）。
外部若显式设置了 `NUITKA_CACHE_DIR`，脚本尊重外部设置。

### 18.7 0.3.1 产物与验证

| 项目 | 实际情况 |
|---|---|
| 版本 | `0.3.1`（`app/__init__.py` / `pyproject.toml` / `installer.iss` 三处同步） |
| 编译耗时 | **8 分 45 秒**（479 个 C 文件，clcache 471 命中） |
| 隐私自检 | 通过 |
| `dist\MiHome-Windows.exe` | 35.1 MB，ProductVersion `0.3.1.0` |
| `dist\installer\MiHome-Windows-Setup-0.3.1.exe` | 38.1 MB |

打包版实机验证（把 `USERPROFILE` 指到空目录模拟新机器）：

```
INFO 未登录：打开扫码登录窗口
INFO 登录: 开始获取二维码 (第 1 轮)
INFO 登录: 第 1 轮取到登录数据（3.9s）
INFO 登录: 二维码已显示（4.0s）
```

截图确认二维码正常渲染、状态为「请使用米家 APP 扫描二维码」——
即 0.3.0 上必现的卡死，在打包版 0.3.1 上已消失。

四套原有回归 + 新增 `tests/dispatch_test.py` 全部 exit 0。

---

## 19. 挂件背景色 + 置顶开关（0.3.2）

### 19.1 背景色：从「只有主题灰」到「可选底色」

| 项目 | 原（0.3.1 及以前） | 现（0.3.2） |
|---|---|---|
| 底色来源 | 写死跟随主题（深色 `#1e1f24` / 浅色 `#f2f3f5`） | 默认仍是「跟随主题」，另可选纯黑 / 深夜灰 / 石墨 / 墨蓝 / 白 / 米白 / 自定义取色 |
| 卡片与文字 | 全部取主题色 | 按底色明暗自动派生：暗底把卡片提亮、文字转白；亮底压暗、文字转黑 |
| 折叠横条 | 同面板底色 | 跟随同一个底色（收起后不会突然变回主题灰） |
| 持久化 | 无 | `board.json` 的 `bg`，`null` 表示跟随主题 |
| 生效方式 | 重启才成立（改动需重编） | 点色块即刻生效，所见即所得 |

派生规则集中在 `widgets_board._BoardPalette`：底色 `lightnessF() < 0.5` 判暗，
暗底把卡片混白 10%、文字用 `#f2f3f5`；亮底把卡片混黑 5%、文字用 `#1a1b1f`；
主/次/离线文字用不同透明度的 `rgba()` 区分层级。强调色（电源开、选中态）始终是主题绿，
两套底色下都够亮。

### 19.2 置顶：从「永远置顶」到「可关，默认关」

| 项目 | 原 | 现 |
|---|---|---|
| 窗口标志 | 恒带 `WindowStaysOnTopHint` | 由 `topmost` 决定；默认**不带** |
| 用户感受 | 一直压在其他窗口上面，挡住内容 | 与普通窗口一致，会被别的窗口盖住；需要常驻时勾上「窗口置顶」 |
| 持久化 | 无 | `board.json` 的 `topmost`（默认 `false`） |

实现要点：Qt 在 Windows 上调用 `setWindowFlags` 会**销毁并重建原生窗口**
（隐藏 + 位置尺寸复位），因此 `_apply_window_flags()` 改完标志后必须重新 `show()`
并还原几何，否则挂件会跳到屏幕左上角——测试 11 专门锁了这一条。

### 19.3 交互位置

都在「管理桌面挂件」对话框里（托盘右键 → 管理桌面挂件）：

- **背景色**：第一行 6 个预设色块，第二行「跟随主题」「自定义颜色…」（打开系统取色器）；
- **窗口置顶**：勾选框 + 一行说明。

与透明度、列数同一套约定：**点了就生效并落盘**，点「取消」也不会回滚这几项
（「保存」只决定设备清单与顺序）。

### 19.4 回归

`tests/board_test.py` 新增三组：

- **10**：纯黑立刻生效 + 卡片文字转亮色 + 离线文字转半透明；米白底文字转深色；
  折叠横条跟随底色；回到「跟随主题」后与主题色一致；
- **11**：默认无 `WindowStaysOnTopHint`；预览路径只改窗口不落盘（落盘归对话框）；
  开/关置顶后标志正确、窗口仍可见、几何未丢；
- **12**：对话框色块点击 → 落盘 + 预览信号；自定义色号 → 不选中任何预设 + 提示显示色号；
  「跟随主题」→ 落盘 `None`；置顶勾选 → 落盘 + 预览信号。

存储层同时锁住：只接受 `#rrggbb`，非法值（如手改成「黑色」）不覆盖已有值。

### 19.5 视觉自检

用真实 Windows 平台离屏渲染了 6 种底色 + 折叠横条 + 管理对话框（脚本
`_ui_check/render_board.py`，仅本机调试用，不入库）。确认：黑底白字、
米白底深字、离线设备在半透明下仍可辨识、折叠横条同步、对话框换行不挤字。

---

## 20. 挂件管理：搜索设备 / 离线设备不进候选 / 连点 + 只开一个窗（0.3.3）

### 20.1 选设备可以搜索（新增）

| 项目 | 原 | 现 |
|---|---|---|
| 找设备 | 只能在长列表里滚动 | 列表上方有搜索框，按**设备名或房间名**实时过滤，大小写不敏感，带清空按钮 |

只隐藏行、不动勾选状态，所以「搜到客厅设备勾上，再搜书房勾另一台」不会互相干扰；
拖拽命中会跳过被过滤掉的行（`_hit_test` 只认可见行），避免拖到看不见的设备上。

### 20.2 离线设备不进候选（新增）

| 项目 | 原 | 现 |
|---|---|---|
| 未选中的离线设备 | 混在候选列表里，占位置还要滚 | **不显示、也搜不到** |
| 已选中的离线设备 | 显示 | **仍显示**（文字变暗），可就地取消勾选 |

这是刻意的例外：`selected_dids()` 只收集列表里存在的行，若把"已选中的离线设备"
一并藏掉，用户一保存就会把上不了线的设备**静默丢掉**。要的是"不打扰"，
不是"删数据"。

### 20.3 连点「+」叠出多个管理窗（修复）

**根因**：`show_board_manager()` 没有单实例守卫。连点「+」时点击事件排队，
第一个模态窗 `exec()` 返回后，排队的第二次点击又建一个窗口——屏幕上叠出多个
「管理桌面挂件」，且它们各自订阅同一批预览信号。

**修复**：入口先查 `self._board_manager`，已存在就 `raise_()` + `activateWindow()`
把已有窗口提到前面并直接返回。托盘菜单、主界面「更多」菜单走的是同一个入口，
一并受保护。

### 20.4 回归

`tests/board_test.py` 新增两组：

- **13**：搜索按设备名命中唯一设备；按房间命中该房间的**在线**设备（离线的同房间
  设备不出现）；清空搜索恢复全部行；搜不到时命中检测返回 None；
  未选中离线设备不在候选、已选中离线设备保留且保持勾选；
- **14**：把对话框 `exec` 换成"打开时再调一次 `show_board_manager`"的替身，
  断言只创建了 1 个窗口，且关闭后 `_board_manager` 被清空。







---

## 21. 管理窗布局重排：设置项拆到「外观」页（0.3.4）

### 21.1 问题

用户反馈：设备列表一次只能看两行，太挤。实测 660px 高的面板里，
列数/透明度/背景色/置顶四个分区连标题、间距、说明文字一共吃掉约 430px，
留给列表的只有 ~90px（两行 48px 的卡片）。

### 21.2 改法：分为「设备」「外观」两页

| 项目 | 原 | 现 |
|---|---|---|
| 版式 | 单页：四个设置分区在上，设备列表被压到最下面 | 顶部两个页签：**设备**（搜索 + 列表，独占整页高度）/ **外观**（四个设置分区） |
| 设备列表可视行数 | **2 行**（660px 面板） | **6 行以上**（620px 面板，列表区约 470px） |
| 窗口置顶说明 | 勾选框下两行灰字解释 | **去掉解释**，说明挪进勾选框 tooltip |
| 背景色 | 两行控件：色块一行 + 「跟随主题 / 自定义」一行 | 「背景色」标题行内放「跟随主题 / 自定义…」+ 色号，下面一行色块 |
| 「保存」语义 | 容易误解为外观也要保存 | 外观页底部加一句：**以上改动点击即生效，无需点「保存」；「保存」只保存设备清单** |

切页只切 `QStackedWidget`，不改任何状态；页签样式与列数按钮同款（选中绿底）。

### 21.3 回归

`tests/board_test.py` 原有 14 组全部通过（对话框控件引用、预览信号、搜索过滤
均未变）。页签是纯 UI 拆分，行为契约没有变化，所以没有新增断言；视觉改用
`_ui_check/render_board.py` 渲染深浅两套 × 两页共 4 张图人工核对。