# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""读屏软件（NVDA / 争渡 / 讲述人 / JAWS）无障碍桥接层。

为什么要这一层
--------------
本项目 UI 大量使用 SiliconUI 的自绘控件：设备卡片、场景卡片、托盘行、
桌面挂件卡片都是纯 ``QWidget`` + ``QPainter``，没有文字、没有焦点策略，
在 Windows UIA 树里既没有名称也进不了 Tab 链；图标按钮只用 ``setToolTip``
承载文案，而 tooltip 又被自绘浮窗接管；开关的三态只靠颜色表达。结果就是
读屏软件看得到窗口，却读不出「这是什么、现在什么状态」。

Qt6 的 Windows UIA 桥接（``QWindowsUia``）本身是可用的（静态编在
``qwindows.dll`` 里），缺的只是**语义**：名称、焦点、键盘入口和
状态反馈。本模块就是把这些语义补齐。

设计约束（改动前请务必读完）
----------------------------
1. **纯加法**。只调用 ``setAccessibleName`` / ``setFocusPolicy`` /
   ``setWindowTitle`` / ``setProperty``，以及新增键盘事件分支和
   ``QShortcut``。绝不改动任何 ``paintEvent``、``mouseReleaseEvent``、
   ``eventFilter``、QSS、布局、几何或既有信号连接。键盘激活一律转发到
   **已经存在**的信号或方法，保证与鼠标路径产生完全相同的副作用。
2. **零业务侵入**。全模块不 import 任何 ``app.ui.*`` 业务模块，按类名和
   鸭子属性识别控件，避免循环导入、启动变慢和 Nuitka 打包牵连。
   业务代码只需要在 ``run.py`` 增加两行挂载。
3. **对普通用户无感**。无障碍属性不改变一个像素；自绘卡片获得焦点后没有
   可见焦点框（本来就没画），鼠标用户看不出差别。焦点恢复只针对
   Tab/Backtab 产生的键盘焦点，鼠标点击的焦点永远不参与。把卡片内冗余
   动作按钮设为 ``NoFocus`` 只影响 Tab 分配，不影响鼠标点击与 clicked 信号。
4. **永不拖垮主程序**。所有回调都包在 try/except 里；Qt 版本差异导致的
   API 缺失一律静默降级。桥接失效最多是没有无障碍，不能让应用崩溃。
5. **文案只补缺失的文字**。可访问名称里只写控件本身代表的内容（设备名、
   房间、属性名、按钮用途，以及读屏拿不到的自绘开关状态）。读屏自己会
   朗读的控件类型（按钮 / 复选框 / 选项卡 / 分组）与选中态、以及「按空格」
   「按回车」这类操作提示一律不写；``accessibleDescription`` 会映射到
   UIA 的 HelpText 并被读屏追加朗读，因此恒留空，也不写自己编的帮助文案。
6. **去掉多余的「回车」提示**。QDialog 里 ``autoDefault=True`` 的
   ``QPushButton`` 一旦获得焦点，Qt 的 UIA 桥接就会把
   ``LegacyIAccessible.KeyboardShortcut`` 报成 ``'Enter'``，NVDA 于是在
   每个按钮名称后多念一声「回车」。关闭 ``autoDefault`` / ``default`` 后
   该快捷键消失；「回车激活焦点按钮」由事件过滤器等价转发到 ``click()``，
   鼠标、空格、回车三条路径的副作用与改造前完全一致。
7. **符号按钮补文字**。添加功能抽屉里每项只有一个 ``+`` / ``✓`` 按钮，
   工作台卡片上是 ``↑`` / ``↓`` / ``✕``，托盘与桌面挂件管理里是
   ``+`` / ``✓`` 勾选钮。这些字形会被 Qt 原样报成「+ 按钮」，读屏既分不清
   是哪一项，也不知道按下去是添加还是移除。按控件语义补名称：抽屉项取
   它所属 ModuleCard 的功能标题并带上当前动作，勾选钮复用同行的设备名，
   图标按钮直接取 tooltip（有 tooltip 时）。显示文字本身一个字都不改。
8. **数值滑块必须真的能用键盘改**。设备详情里的设定温度、亮度、色温等
   是自绘 ``SiSlider``：Qt 默认 ``NoFocus``，Tab 根本到不了；即便把按键
   送进去，``valueChanged`` 只更新预览文字，真正下发到设备的是鼠标
   ``sliderReleased`` → ``SliderSection._commit()``。桥接层只做三件事：
   给属性滑块补 ``StrongFocus``、把方向键 / Page / Home / End 等价转发到
   既有的 ``setValue()``、停止按键后按既有「松手才下发」语义调用同一个
   ``_commit()``（去抖，连续按键只下发一次）。不改绘制、QSS、布局、
   鼠标事件与任何既有信号连接。Qt 的 UIA 不为这个自绘滑块提供
   RangeValue，所以当前数值要补进可访问名称，读屏才念得出来。
9. **Tab 链里不留空的「分组」**。项目里所有页面都套着 ``QScrollArea``
   （设备详情工作台、添加功能抽屉、主界面设备网格、调整标签顺序，以及
   设置 / 定时任务 / 托盘管理等）：Qt 默认给它 ``StrongFocus``，而它在 UIA
   里是无名的 ``GroupControl``，读屏只会念「分组」，本身既没有可操作语义
   也不显示任何内容。把这些无名滚动容器统一设为 ``NoFocus`` 后，Tab 直接
   进入真正的控件；滚轮、滚动条、鼠标拖拽与内部控件的焦点全都照旧，
   代价只是「聚焦滚动容器后按方向键翻页」这一入口不再有。规则只命中
   ``accessibleName`` 为空的滚动容器；将来某个容器若有可读名称则不受影响。
10. **同名按钮补上所属对象**。「调整标签顺序」每行都有一个文字完全相同的
    「设为默认」按钮，房间名只写在同行的 QLabel 上，读屏逐行 Tab 时听到
    的全是「设为默认 按钮」，分不出是哪一个标签。名称里补房间名
    （``客厅，设为默认``）；按钮被设为默认后文字变成禁用的「默认」，
    名称同步更新。只补缺失的对象名，不写控件类型、状态词或操作提示。
11. **tooltip 不变成读屏的拖尾**。Qt 的 UIA 在 ``accessibleDescription``
    为空时会**回退用 ``toolTip`` 填充 ``UIA_FullDescriptionProperty``**，
    读屏于是把悬停说明追加在名称后面念一遍。定时任务里这类文案尤其冗余：
    搜索框带「回车从候选里选」这种操作提示，开关带「停用后保留规则但不执行」
    这种帮助文案，删除按钮的 tooltip 又和名称一模一样，会连念两遍
    「删除这条规则」。本层只在该窗口内给带 tooltip 的控件设一个空白描述
    占位挡住回退：``toolTip()`` 一个字不改，明眼用户的悬停提示照旧，
    UIA HelpText 仍为空。同时给四个无名下拉框补上缺失的用途名
    （设备 / 小时 / 分钟 / 动作），避免去掉拖尾后反而读不出这是什么。
    其它窗口的 tooltip 保持原样——用户明确要求过保留某些按钮的说明文字。
12. **下拉框不再多念「下方向」**。Qt 把 ``QComboBox`` 无障碍接口的
    ``Accelerator`` 固定报成 ``Down``，UIA 于是把它填进
    ``LegacyIAccessible.KeyboardShortcut``，NVDA 在每个下拉框后面追加一声
    「下方向」。它和按钮的「回车」同属操作提示：读屏本来就会报控件类型
    与展开状态，用户也知道用方向键。``QAccessible.installFactory`` 为所有
    QComboBox 返回一个代理接口，除 ``text(Accelerator)`` 返回空之外，
    名称、描述、当前值、子节点、角色、状态全部委托给 Qt 原生接口；
    视觉、鼠标、键盘（方向键改值、Alt+Down 展开、展开列表的可访问项）
    与各个 UIA Pattern 一律不变。工厂拿不到原生接口时静默降级回原生行为。
13. **托盘快捷窗口的电源钮要带上设备名与房间**。托盘行是纯 ``QFrame``，
    只存了 ``_did``，设备名 / 房间名画在同行的两个 ``QLabel`` 上（还会按
    宽度省略，全文放在 tooltip）。``_context()`` 沿父链找不到 ``device``
    对象，于是每个电源钮都只念「电源开关，已关闭」，逐行 Tab 时分不清是
    哪台设备。这里按窗口里的设备列表用 ``did`` 反查，给名称补上
    「设备名，房间」；场景行的执行钮同样补「场景名，家庭」。数据未就绪时
    退回同行 QLabel 的完整文本；只命中托盘快捷窗口，其它窗口的电源钮
    名称保持原样。状态词仍留在名称里——PowerButton 不是 checkable，
    开 / 关是纯颜色表达，读屏拿不到。
14. **托盘快捷窗口的 Tab 焦点要稳定**。这个面板每 3 秒随主窗口的设备轮询
    调一次 ``set_devices()``，只要窗口可见就 ``_rebuild()`` 整窗重建：上一
    秒持有键盘焦点的电源钮被 ``deleteLater()`` 销毁，焦点无声丢失又回到链
    首，读屏于是反复念同一个控件；重建瞬间旧按钮还挂在焦点链里，形成同名
    重复停靠点。开关按下后业务层还会 ``set_busy(True)`` 禁用按钮（乐观
    翻转期间防连点），Qt 会顺势把焦点挪到下一颗按钮，用户再按空格切的是
    另一台设备。本层做四件事，仍不改业务源码、绘制、布局、鼠标事件与信号：
    (a) 给窗口的 ``_rebuild`` 套一层内容指纹守卫，设备 / 开关 / 顺序 / 场景
    / 列数 / 宽度都没变时跳过这次重建（与桌面挂件「就地更新，不整窗重建」
    的既有做法一致），窗口隐藏或内容真变了照常重建；(b) 已从网格摘除、
    等待延迟删除的旧行整行退出 Tab 链；(c) 记住 Tab 到达的那一台设备，重建
    后把焦点放回同 ``did`` / ``scene_id`` 的新按钮；(d) **忙碌时根本不让焦点
    漂移**：实测 Qt 在 ``setEnabled(False)`` 里是「先同步把焦点推给下一颗按钮、
    再发 EnabledChange」，读屏因此先念下一台设备，等忙碌结束再把焦点抢回来
    只会多出一次播报（听起来就是「念了下一项、读不全、又跳回原来」）。所以
    在托盘行按钮实例上包一层 ``setEnabled``，忙碌且正持有焦点时保持启用，
    忙碌配色由 ``_busy`` / 样式表决定，与 enabled 无关；防连点改由桥接器在
    这期间吞掉鼠标点击与激活键。鼠标焦点、普通用户的视觉与点击行为一律不变。
15. **托盘小爱音频栏也能用键盘操作**。顶部的「上一首 / 播放暂停 / 下一首」
    与音量条在业务里都被显式设成 ``NoFocus``，Tab 根本到不了。本层给它们
    补 ``StrongFocus``，图标按钮的名称取 tooltip（播放 / 暂停随状态变），
    音量条沿用「方向键改值 + 停止按键后下发一次」的既有语义，转发到业务
    已有的 ``_on_slider_commit()``。音量条是**原生 QSlider**，Qt 的 UIA 会
    提供 RangeValuePattern，读屏自己会念当前音量，因此名称只写「音量」，
    不把数值再抄一遍（与自绘 SiSlider 不同，那里没有 RangeValue）。三个
    按钮的 tooltip 与名称完全相同，Qt 会把它回退进 FullDescription 再念
    一次，所以在这三个按钮上挡住回退。最后，业务在切歌 / 静音 / 提交音量
    时都会 ``setEnabled(False)`` 防连点，若控件正持有键盘焦点就会重演第 14
    条的焦点漂移；这里同样只在「这次操作由键盘发起」时保持启用并吞掉防连点
    按键，鼠标点击则完全走业务原路径，明眼用户看到的忙碌变暗一丝不变。
16. **托盘设备行与主界面卡片一样：空格切电源、回车打开详情**。主界面
    ``DeviceCard`` 本来就有这两个键（见 ``_activate``），但托盘行没有可聚焦
    的父卡片，焦点落在行内唯一的 ``PowerButton`` 上，回车被第 6 条的
    ``_a11y_enter_proxy`` 等价转成 ``click()``，于是又切了一次电源，读屏
    用户没有任何键盘入口能打开设备详情。本层只在事件过滤器里把**设备行**
    电源钮的回车转发到窗口既有的 ``open_device_requested(did)`` 信号——
    鼠标左键点击行本体走的正是它，副作用完全一致（收起面板、打开详情
    对话框）。空格仍由 QPushButton 原生的 ``clicked`` 切电源；场景行的
    回车仍是「执行场景」，音频栏、标题栏、主界面卡片与其它窗口一概不受
    影响。长按回车的自动重复事件会被过滤，不会连弹出多个详情对话框。
    名称、描述、显示文字、焦点策略、忙碌守卫与鼠标路径一个字都不改。

覆盖范围（首轮：核心操作链路）
------------------------------
扫码登录窗口 → 家庭/房间切换 → 设备卡片（开关、打开详情）→ 场景卡片 →
设备详情工作台（电源行、滑块、枚举、只读值）→ 设置页开关 → 顶栏与状态胶囊
→ 托盘项目管理对话框（勾选框、搜索框、房间筛选）→ 托盘快捷窗口的
设备 / 场景行按钮名称与键盘操作（设备行空格切电源、回车打开详情），以及
小爱音频栏的切歌 / 播放 / 音量 → Toast / 登录状态的播报。
桌面挂件的完整键盘导航留待后续迭代。

验证方式
--------
``.venv\\Scripts\\python.exe -m tests.a11y_test``（离屏，无需米家账号）；
真实读屏验证需在 Windows 桌面会话中运行源码或构建产物。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QWidget,
)

# Qt 的无障碍事件 API 在不同 PySide6 小版本里暴露程度不同；缺失时降级为
# 「只更新名称，不主动发 UIA 事件」，焦点经过时读屏仍会朗读最新状态。
try:  # pragma: no cover - 取决于具体 PySide6 版本
    from PySide6.QtGui import QAccessible, QAccessibleEvent
except ImportError:  # pragma: no cover
    QAccessible = None  # type: ignore[assignment]
    QAccessibleEvent = None  # type: ignore[assignment]

# QAccessibleWidget 只在需要给 QComboBox 换无障碍接口时用到；单独 try 以免
# 某个 PySide6 小版本缺它时连上面的播报事件一起降级。
try:  # pragma: no cover - 取决于具体 PySide6 版本
    from PySide6.QtWidgets import QAccessibleWidget
except ImportError:  # pragma: no cover
    QAccessibleWidget = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 名称素材
# ---------------------------------------------------------------------------

# 内嵌标题栏的三个按钮用 PUA 私用区字形当图标字体，读屏会读成乱码。
_TITLE_BAR_GLYPHS = {
    "\ue921": "最小化窗口",
    "\ue922": "最大化窗口",
    "\ue923": "还原窗口",
    "\ue8bb": "关闭窗口",
}

# OverlayDialog 子类没有 setWindowTitle，UIA 里连窗口名都是空的。
# 无边框窗口设置标题不产生任何视觉变化。
_WINDOW_TITLES = {
    "LoginDialog": "登录米家账号",
    "SettingsDialog": "设置",
    "DeviceDetailDialog": "设备详情",
    "AboutDialog": "关于",
    "ScheduleDialog": "定时任务",
    "RoomOrderDialog": "调整房间标签顺序",
    "RenameDeviceDialog": "修改设备名称",
    "MetricsPickDialog": "选择卡片显示的值",
    "BoardManagerDialog": "管理桌面挂件",
    "TrayManagerDialog": "管理托盘快捷控制",
    "TrayQuickWindow": "托盘快捷控制",
    "StatusPanelDialog": "环境状态明细",
    "AddDrawer": "添加设备功能",
    "UpdateDialog": "发现新版本",
}

# 这些自绘容器需要：可聚焦 + 键盘激活。它们是纯 QWidget，Qt 默认 NoFocus。
_ACTIVATABLE = {
    "DeviceCard",
    "SceneCard",
    "PowerRowSection",
}

# 环境状态胶囊：只有图标 + tooltip，数值写在 text() 里，两者都要读出来。
_STATUS_PILL_TIPS = (
    "当前范围的温度",
    "当前范围的湿度",
    "当前范围内开启的设备",
    "耗材不足的设备",
)

# 承载属性区块标题的卡片，给它们在 UIA 树里一个可读的分组名。
_SECTION_CLASSES = {
    "SliderSection",
    "EnumGridSection",
    "ReadOnlySection",
    "StatusPillSection",
    "TextRowSection",
    "GroupSection",
}

# 名称里带「只有颜色表达的状态」的控件，状态翻转时需要主动播报。
# checkable 按钮（设置开关、房间选项卡、托盘勾选框）的选中态由读屏自己
# 朗读，名称里不再带状态词，因此不在此列。
_STATEFUL = {
    "PowerButton",
    "DeviceCard",
    "PowerRowSection",
    "StatusPillSection",
}

_PUA_START = 0xE000
_PUA_END = 0xF8FF

# Qt 的 UIA 在 accessibleDescription 为空时会回退用 toolTip() 填充
# UIA_FullDescriptionProperty，读屏便把悬停说明追加在名称后朗读。设一个
# 空白占位即可挡住回退：UIA 读到的是空白，不会发声，而 toolTip() 本身
# 保持不变，明眼用户的悬停提示照旧。不能设成 ""（会继续回退），也不能
# 写帮助文案（会变成新的冗余拖尾）。
_UIA_BLANK_DESCRIPTION = " "

# 只用字形表意的按钮文字。它们不是「可读文字」：Qt 会把 "+" 原样报给
# UIA，NVDA 于是念「+ 按钮」；真正的语义在同行的 QLabel 或 tooltip 里。
# 注意 X 只用于单独成文的关闭按钮（下面有专门分支），不影响 "Xiaomi"。
_DECORATIVE_GLYPHS = set("+＋-−–—✓✔√✕×X↑↓←→⠿·•●○◆◇…※")

# 键盘调节数值滑块：沿用鼠标「拖动只预览、松手才下发」的既有语义，
# 停止按键后统一提交一次，避免连按方向键向设备连发几十条指令。
_SLIDER_COMMIT_MS = 400


def _readable(text: str) -> bool:
    """文字里是否含有可朗读内容（空白 / PUA 图标 / 装饰符号不算）。"""
    return any(
        not ch.isspace() and not (_PUA_START <= ord(ch) <= _PUA_END)
        and ch not in _DECORATIVE_GLYPHS
        for ch in text
    )


def _slider_owner(w: QWidget) -> QWidget | None:
    """找「这个滑块属于哪个属性区块」，只认带 ``_slider`` / ``_commit`` 的容器。

    用鸭子属性而非类名：``SliderSection`` 及组合卡片里同形的子区块都能覆盖；
    ``QScrollBar`` / ``SiScrollBar``、坐标选择器内部的滑块没有 ``_commit``，
    一律排除，避免给滚动条或无关控件凭空造出 Tab 停靠点。
    """
    if not (isinstance(w, QAbstractSlider) and not isinstance(w, QScrollBar)):
        return None
    node = w.parentWidget()
    for _ in range(5):
        if node is None:
            return None
        if (getattr(node, "_slider", None) == w
                and callable(getattr(node, "_commit", None))):
            return node
        node = node.parentWidget()
    return None


# 「没有名字的滚动容器」。Qt 默认给 QAbstractScrollArea StrongFocus，Tab
# 会停在它上面；Qt UIA 把普通容器报成 GroupControl，名称为空时读屏只能念
# 控件类型「分组」。用户先后在设备详情、主界面、调整标签顺序里遇到同一个
# 问题，因此不再按祖先类名限定范围：所有无名 QScrollArea 一律移出 Tab 链。
# 有可读名称的容器说明它承载语义，保留原焦点策略。
def _is_plain_scroll_area(w: QWidget) -> bool:
    """是否是需要移出 Tab 链的无名滚动容器。"""
    return isinstance(w, QScrollArea) and not w.accessibleName()


def _context(widget: QWidget) -> tuple[str, Any]:
    """沿父链向上找承载业务数据的祖先，返回 (标题, 数据对象)。

    电源钮、卡片、属性区块共用这一套：业务对象上已经存着设备名 / 场景名 /
    属性描述，直接复用，不需要无障碍层自己维护一份会过期的映射。
    """
    node: QWidget | None = widget
    for _ in range(6):
        if node is None:
            break
        device = getattr(node, "device", None)
        if device is not None and getattr(device, "name", None):
            return str(device.name), device
        scene = getattr(node, "scene", None)
        if scene is not None and getattr(scene, "name", None):
            return str(scene.name), scene
        prop = getattr(node, "prop", None)
        if prop is not None and getattr(prop, "desc", None):
            return str(prop.desc), prop
        node = node.parentWidget()
    return "", None


def _row_title(widget: QWidget) -> str:
    """设置项开关：取同一行里第一个非空 QLabel 文字作为描述。"""
    parent = widget.parentWidget()
    if parent is None:
        return ""
    try:
        for label in parent.findChildren(
                QLabel, "", Qt.FindChildOption.FindDirectChildrenOnly):
            text = label.text().strip()
            if text:
                return text.split("\n")[0]
    except Exception:
        pass
    return ""


# 托盘项目 / 桌面挂件列表里勾选按钮的文字只有这些符号，真正的设备名 /
# 场景名写在同行的 QLabel 里。Qt 的 UIA 会把按钮报成「+ 复选框 未选中」。
_TRAY_CHECK_GLYPHS = {"+", "✓", "✔", "√", "−", "-", ""}


def _tray_dialog(w: QWidget) -> QWidget | None:
    """沿父链找 TrayManagerDialog 实例；不在该对话框里返回 None。

    用祖先类名限定范围，避免误伤别处同样用符号做文字的按钮或输入框。
    """
    node = w.parentWidget()
    for _ in range(8):
        if node is None:
            return None
        if type(node).__name__ == "TrayManagerDialog":
            return node
        node = node.parentWidget()
    return None


# 托盘快捷窗口（左键点托盘图标弹出的面板）里，每行是一个纯 QFrame：只存
# _did / _scene_id，设备名与房间名画在同行的两个 QLabel 上，还会按宽度省略
# （完整文本放在 tooltip）。所以 _context() 沿父链找不到 device / scene，
# 每个电源钮都只念「电源开关，已关闭」，逐行 Tab 分不清是哪台设备。按 did
# 反查窗口里的设备列表，把「设备名，房间」补进名称；场景行同理补场景名。
def _tray_quick_window(w: QWidget) -> QWidget | None:
    """沿父链找 TrayQuickWindow；不在托盘快捷面板里返回 None。"""
    node = w.parentWidget()
    for _ in range(8):
        if node is None:
            return None
        if type(node).__name__ == "TrayQuickWindow":
            return node
        node = node.parentWidget()
    return None


def _tray_quick_row(w: QWidget, win: QWidget) -> QWidget | None:
    """从按钮往上找带 _did / _scene_id 的托盘行（只认 trayRow 卡片）。

    顶部音频栏 ``_TrayAudioBar`` 也存了 ``_did``（当前控制的音箱），
    只按属性找会把「上一首 / 播放 / 下一首」误判成某台设备的开关，
    进而装错忙碌守卫、抢走 Tab 锚点。设备行与场景行都带
    ``objectName == "trayRow"``，据此精确限定。
    """
    node = w.parentWidget()
    for _ in range(4):
        if node is None or node is win:
            return None
        if (node.objectName() == "trayRow"
                and (getattr(node, "_did", None)
                     or getattr(node, "_scene_id", None))):
            return node
        node = node.parentWidget()
    return None


def _tray_quick_title(w: QWidget, win: QWidget) -> str:
    """托盘快捷行按钮所属对象的「名字，房间 / 家庭」。"""
    row = _tray_quick_row(w, win)
    if row is None:
        return ""
    did = getattr(row, "_did", None)
    if did:
        for dev in getattr(win, "_devices", ()) or ():
            if getattr(dev, "did", None) == did:
                name = str(getattr(dev, "name", "") or "").strip()
                room = str(getattr(dev, "room_name", "") or "").strip()
                return "，".join(x for x in (name, room) if x)
    sid = getattr(row, "_scene_id", None)
    if sid:
        for sc in getattr(win, "_tray_scenes", ()) or ():
            if isinstance(sc, dict) and sc.get("scene_id") == sid:
                name = str(sc.get("name") or "").strip()
                home = str(sc.get("home_name") or "").strip()
                return "，".join(x for x in (name, home) if x)
    # 兜底：设备列表尚未就绪时，从同一行 QLabel 的完整文本取。副标题形如
    # 「客厅 | 26℃」「客厅 · 离线」，只截出房间名，不把读数带进名称。
    try:
        labels = row.findChildren(
            QLabel, "", Qt.FindChildOption.FindDirectChildrenOnly)
    except Exception:
        labels = []
    parts: list[str] = []
    for label in labels[:2]:
        raw = (label.toolTip() or label.text() or "").strip()
        raw = raw.split("|")[0].split("·")[0].strip()
        if raw and raw not in parts:
            parts.append(raw)
    return "，".join(parts)


# ---------------------------------------------------------------------------
# 托盘快捷窗口：稳定 Tab 焦点（见模块说明第 14 条）
# ---------------------------------------------------------------------------

def _tray_quick_grid_rows(win: QWidget) -> list[QWidget]:
    """当前网格里仍在使用的行（按行序）；重建过程中 _grid 可能为 None。"""
    grid = getattr(win, "_grid", None)
    if grid is None:
        return []
    rows: list[QWidget] = []
    try:
        for i in range(grid.count()):
            item = grid.itemAt(i)
            row = item.widget() if item is not None else None
            if row is not None:
                rows.append(row)
    except Exception:
        pass
    return rows


def _tray_quick_live_buttons(win: QWidget) -> list[QWidget]:
    """当前有效行里的按钮（按行序），用来排除等待延迟删除的旧行。"""
    buttons: list[QWidget] = []
    for row in _tray_quick_grid_rows(win):
        try:
            buttons.extend(row.findChildren(QAbstractButton))
        except Exception:
            pass
    return buttons


def _tray_quick_orphan_row(w: QWidget, win: QWidget) -> bool:
    """是否是被 _destroy_grid() 从布局摘掉、只等 deleteLater() 的旧行。

    这些行在删除前仍挂在 _host 下、仍 visible 且可聚焦，Tab 链里于是出现
    与当前行同名的一整套按钮。只把它们移出焦点链，不改绘制与鼠标行为。
    """
    if type(w).__name__ != "QFrame" or w.objectName() != "trayRow":
        return False
    if getattr(win, "_grid", None) is None:
        return False
    return not any(row is w for row in _tray_quick_grid_rows(win))


def _tray_quick_button_key(w: QWidget,
                           win: QWidget | None = None) -> tuple[str, str] | None:
    """托盘行按钮的语义键：同一台设备 / 场景重建前后保持不变。"""
    if not isinstance(w, QAbstractButton):
        return None
    if win is None:
        win = _tray_quick_window(w)
    if win is None:
        return None
    row = _tray_quick_row(w, win)
    if row is None:
        return None
    did = getattr(row, "_did", None)
    if did:
        return ("tray-device", str(did))
    sid = getattr(row, "_scene_id", None)
    if sid:
        return ("tray-scene", str(sid))
    return None


def _tray_device_open_detail(w: QWidget) -> bool:
    """托盘快捷窗口「设备行」电源钮的回车：打开该设备的详情。

    主界面设备卡片本来就是「空格切电源、回车打开详情」（见 ``_activate``）。
    托盘行没有可聚焦的卡片，焦点落在行内唯一的电源钮上，而回车此前被
    ``_a11y_enter_proxy`` 等价转成 ``click()``——于是又切了一次电源，读屏
    用户没有任何键盘入口能打开设备详情。这里把回车转发到窗口**既有**的
    ``open_device_requested(did)`` 信号：鼠标左键点击行本体走的正是它，
    副作用完全一致（收起面板、打开详情对话框），不改业务源码与鼠标路径。

    只命中设备行（语义键为 ``tray-device``）的 ``PowerButton``：场景行的
    回车仍是「执行场景」，音频栏三个切歌钮、标题栏按钮、主界面卡片与其它
    窗口的按钮一概不受影响。空格不在此处处理，仍由 QPushButton 原生的
    ``clicked`` 切电源。成功返回 True 表示按键已消费。
    """
    if type(w).__name__ != "PowerButton":
        return False
    key = _tray_quick_button_key(w)
    if key is None or key[0] != "tray-device":
        return False
    win = _tray_quick_window(w)
    if win is None:
        return False
    try:
        win.open_device_requested.emit(key[1])
    except Exception:
        return False
    return True


def _tray_content_signature(win: QWidget):
    """托盘列表内容指纹：只要有一项会影响生成行，就必须允许重建。

    刻意不含温湿度读数——``set_metrics()`` 本来就地刷新副标题标签，
    不必为此整窗重建。取值全部转成可哈希的标量，异常时返回 None
    （守卫随即放行原 _rebuild，绝不因指纹算不出而卡住业务刷新）。
    """
    try:
        dids = tuple(str(d) for d in (getattr(win, "_tray_dids", ()) or ()))
        order = tuple(
            (str(o.get("type", "")), str(o.get("id", "")))
            for o in (getattr(win, "_tray_order", ()) or ())
            if isinstance(o, dict))
        scenes = tuple(
            (str(s.get("scene_id", "")), str(s.get("name", "")),
             str(s.get("home_name", "")))
            for s in (getattr(win, "_tray_scenes", ()) or ())
            if isinstance(s, dict))
        lookup = {getattr(d, "did", None): d
                  for d in (getattr(win, "_devices", ()) or ())}
        power = getattr(win, "_known_power", {}) or {}
        devices = tuple(
            (did,
             str(getattr(lookup.get(did), "name", "") or ""),
             str(getattr(lookup.get(did), "room_name", "") or ""),
             bool(getattr(lookup.get(did), "online", True)),
             power.get(did))
            for did in dids)
        return (order, scenes, devices,
                getattr(win, "_columns", 1), win.width())
    except Exception:
        return None


def _guard_tray_rebuild(win: QWidget) -> None:
    """给托盘快捷窗口套一层 _rebuild 守卫：内容没变就别整窗重建。

    主窗口每 3 秒轮询一次设备并同步给托盘；窗口可见时原实现无条件
    ``_rebuild()``，把所有行连同持有键盘焦点的电源钮一起销毁，焦点无声
    丢失又回到链首。这里只在**窗口可见且当前网格仍在**时跳过重复重建；
    隐藏态（呼出前的准备）、列数切换、设备/状态/顺序变化都照常重建。
    不替换业务源码，只在实例上包一层 Python 方法，视觉与鼠标无感。
    """
    if getattr(win, "_a11y_rebuild_original", None) is not None:
        return
    original = getattr(win, "_rebuild", None)
    if not callable(original):
        return
    win._a11y_rebuild_original = original
    win._a11y_tray_signature = _tray_content_signature(win)

    def rebuild_guarded():
        signature = _tray_content_signature(win)
        if (signature is not None
                and signature == getattr(win, "_a11y_tray_signature", None)
                and getattr(win, "_grid", None) is not None
                and win.isVisible()):
            return
        win._a11y_tray_signature = signature
        return original()

    win._rebuild = rebuild_guarded


def _tray_button_is_busy(btn: QAbstractButton) -> bool:
    """托盘行按钮被禁用是否来自「执行中」忙碌态，而非离线等其它原因。"""
    if getattr(btn, "_busy", None) is True:          # PowerButton
        return True
    # 场景执行钮唯一的禁用点就是 _apply_scene_btn(busy=True)
    return btn.objectName() == "sceneRunBtn"


def _guard_tray_button_enabled(btn: QAbstractButton) -> None:
    """忙碌时保持托盘行按钮启用，让键盘焦点一步都不漂移。

    事后把焦点抢回来无法消除读屏已经播报出去的「下一项」，必须让这次
    ``setEnabled(False)`` 不发生。这里只在按钮实例上包一层 ``setEnabled``：
    当且仅当它正持有键盘焦点、且禁用来自忙碌态时保持启用。忙碌态的配色 /
    图标由 ``_busy`` 与样式表决定，完全不读 enabled，视觉零变化；防连点由
    桥接器在 ``_a11y_busy_hold`` 期间吞掉鼠标点击与激活键，等价于原来的
    disabled。其它窗口的 PowerButton、离线禁用、忙碌结束后的重新启用都不
    受影响；按钮随列表重建被销毁后，新按钮会在下一轮扫描重新装好。
    """
    if _tray_quick_button_key(btn) is None:
        return
    if getattr(btn, "_a11y_set_enabled_original", None) is not None:
        return
    original = btn.setEnabled
    if not callable(original):
        return
    btn._a11y_set_enabled_original = original

    def set_enabled_guarded(enabled: bool, _b=btn, _o=original):
        if (not enabled
                and QApplication.focusWidget() is _b
                and _tray_button_is_busy(_b)):
            _b._a11y_busy_hold = True
            return _o(True)
        _b._a11y_busy_hold = False
        return _o(enabled)

    btn.setEnabled = set_enabled_guarded


# ---------------------------------------------------------------------------
# 托盘小爱音频栏：切歌 / 播放 / 音量也能用键盘（见模块说明第 15 条）
# ---------------------------------------------------------------------------

def _audio_bar(w: QWidget) -> QWidget | None:
    """沿父链找托盘音频栏；按鸭子属性识别，不 import 任何业务模块。"""
    node = w.parentWidget()
    for _ in range(5):
        if node is None:
            return None
        if (getattr(node, "_slider", None) is not None
                and getattr(node, "_btn_prev", None) is not None
                and callable(getattr(node, "_on_slider_commit", None))):
            return node
        node = node.parentWidget()
    return None


def _audio_role(w: QWidget, bar: QWidget) -> str | None:
    """音频栏里需要接管键盘的控件：三个切歌钮与音量条。"""
    if isinstance(w, QAbstractButton):
        if w is getattr(bar, "_btn_prev", None):
            return "prev"
        if w is getattr(bar, "_btn_play", None):
            return "play"
        if w is getattr(bar, "_btn_next", None):
            return "next"
    if (isinstance(w, QAbstractSlider)
            and not isinstance(w, QScrollBar)
            and w is getattr(bar, "_slider", None)):
        return "volume"
    return None


def _guard_audio_enabled(w: QWidget) -> None:
    """键盘操作触发的忙碌禁用不抢焦点；鼠标操作完全走业务原路径。

    音频栏的切歌 / 静音 / 提交音量都会先 ``setEnabled(False)`` 防连点，
    Qt 会同步把键盘焦点推给下一个控件，读屏于是先念「下一项」。这里只在
    「本次操作由键盘发起」且控件正持有焦点时保持启用，并置
    ``_a11y_busy_hold`` 让桥接器吞掉防连点按键；鼠标点击前会清掉标记，
    明眼用户看到的忙碌变暗与改造前完全一样。
    """
    if getattr(w, "_a11y_audio_set_enabled_original", None) is not None:
        return
    original = w.setEnabled
    if not callable(original):
        return
    w._a11y_audio_set_enabled_original = original

    def set_enabled_guarded(enabled: bool, _w=w, _o=original):
        if (not enabled
                and QApplication.focusWidget() is _w
                and _w.property("_a11y_kb_disable")):
            _w.setProperty("_a11y_kb_disable", False)  # 一次性，只挡这一回
            _w._a11y_busy_hold = True
            return _o(True)
        # 业务重新启用（忙碌结束）或鼠标路径的常态禁用：一并清掉标记，
        # 免得残留到下一次与本操作无关的禁用上。
        _w._a11y_busy_hold = False
        _w.setProperty("_a11y_kb_disable", False)
        return _o(enabled)

    w.setEnabled = set_enabled_guarded


# 定时任务「新增规则」区的四个下拉框在 UIA 里名称全为空：读屏只能念出
# 当前值（「20」「打开」），不知道这一项是什么。按对话框里存好的控件引用
# 精确补用途名，不误伤补全弹层等其它 QComboBox。
_SCHEDULE_COMBO_NAMES = (
    ("_device_combo", "设备"),
    ("_hour_combo", "小时"),
    ("_minute_combo", "分钟"),
    ("_action_combo", "动作"),
)


def _schedule_dialog(w: QWidget) -> QWidget | None:
    """沿父链找 ScheduleDialog；不在定时任务窗口里返回 None。

    限定祖先类名是为了不影响别处同样用 tooltip 承载说明的控件。
    """
    node = w.parentWidget()
    for _ in range(8):
        if node is None:
            return None
        if type(node).__name__ == "ScheduleDialog":
            return node
        node = node.parentWidget()
    return None


def _describe_schedule_combo(w: QWidget, dialog: QWidget) -> tuple[str, str] | None:
    """四个无名下拉框只补缺失的用途名；其余控件交回通用规则。"""
    if not isinstance(w, QComboBox):
        return None
    for attr, title in _SCHEDULE_COMBO_NAMES:
        if getattr(dialog, attr, None) == w:
            return title, ""
    return None


def _symbol_check(w: QWidget) -> bool:
    """是否是需要补同行文字的「+ / ✓」勾选钮。

    覆盖两个对话框：管理托盘项目、管理桌面挂件。后者还有列数、背景色块
    等别的 checkable 按钮，因此用 ``_checks`` 字典精确限定，不误伤。
    """
    if not (isinstance(w, QAbstractButton) and w.isCheckable()
            and w.text().strip() in _TRAY_CHECK_GLYPHS):
        return False
    node = w.parentWidget()
    for _ in range(8):
        if node is None:
            return False
        cls = type(node).__name__
        if cls == "TrayManagerDialog":
            return True
        if cls == "BoardManagerDialog":
            checks = getattr(node, "_checks", {}) or {}
            return w in checks.values()
        node = node.parentWidget()
    return False


def _module_button_card(w: QWidget) -> QWidget | None:
    """沿父链找「添加功能」抽屉里的 ModuleCard，且 w 必须是它的按钮。

    抽屉每项只有这一个按钮能进 Tab 链；限定 ``card._btn is w`` 是为了
    不把这个名称套到卡片里的标题 / 描述 QLabel 上。
    """
    if not isinstance(w, QAbstractButton):
        return None
    node = w.parentWidget()
    for _ in range(4):
        if node is None:
            return None
        if type(node).__name__ == "ModuleCard":
            # 用 Qt 的相等比较（比对底层 C++ 对象），不依赖 Python wrapper
            return node if getattr(node, "_btn", None) == w else None
        node = node.parentWidget()
    return None


def _room_default_row(w: QWidget) -> QWidget | None:
    """「调整标签顺序」里「设为默认 / 默认」按钮所属的那一行。

    每行按钮文字完全相同，房间名只写在同行的 QLabel 上（也存于
    ``row.name``）。逐行 Tab 时读屏全念「设为默认 按钮」，分不出对象。
    用 ``_default_btn`` 精确限定，避免把同行的其它控件也套上这个名称。
    """
    if not isinstance(w, QAbstractButton):
        return None
    node = w.parentWidget()
    for _ in range(3):
        if node is None:
            return None
        if type(node).__name__ == "_RoomRow":
            return node if getattr(node, "_default_btn", None) == w else None
        node = node.parentWidget()
    return None


def _describe_room_default(w: QWidget, row: QWidget) -> tuple[str, str]:
    """同名按钮补上所属房间名；按钮文字变成「默认」时名称同步。"""
    name = getattr(row, "name", None)
    title = " ".join(str(name).split()) if name else ""
    if not title:
        title = " ".join(_row_title(w).split())
    text = " ".join(w.text().split()) or "设为默认"
    return (f"{title}，{text}" if title else text), ""


def _describe_module_button(w: QWidget, card: QWidget) -> tuple[str, str]:
    """抽屉按钮：补上功能标题，以及符号本身表达的「添加 / 移除」。

    ``+`` / ``✓`` 不是 checkable 状态，读屏拿不到；按钮名称又不带标题的
    话，整页 Tab 下去每一项都只念「+ 按钮」。名称只放这两项缺失的文字，
    控件类型、按键提示一律不写，HelpText 恒为空。
    """
    label = getattr(card, "_title", None)
    title = " ".join(label.text().split()) if isinstance(label, QLabel) else ""
    action = "移除" if getattr(card, "_added", False) else "添加"
    return f"{action} {title or '未命名功能'}", ""


def _describe_symbol_check(w: QWidget) -> tuple[str, str]:
    """把同一行的「设备名 · 房间」补进勾选框的可访问名称。

    这些按钮是 checkable QPushButton，读屏会自己朗读「复选框 已选中 /
    未选中」；名称里再写一遍状态和「按空格」只会变成噪音。
    """
    title = " ".join(_row_title(w).split())
    return (title or "未命名项目", "")


def _describe_tray_search(w: QLineEdit) -> tuple[str, str]:
    return "搜索设备或场景", ""


def _describe_tray_room(w: QComboBox) -> tuple[str, str]:
    return "按房间筛选", ""


def _status_token(text: str) -> str:
    """从可访问名称里抽出状态词，用来区分「状态翻转」和「名称首次补全」。

    启动时电源钮是逐个探测出状态的，若一有名称变化就播报，读屏会被
    几十条「XX 已关闭」刷屏。只有旧名称和新名称都带状态词、且两者不同
    时才播报，也就是真正的开/关翻转。
    """
    for token in ("已开启", "已关闭", "离线", "正在操作", "状态未知"):
        if token in text:
            return token
    return ""


def _power_state(button: QWidget) -> str:
    """把 PowerButton 的三态 + 离线 / 忙碌翻译成中文。"""
    if not getattr(button, "_online", True):
        return "离线"
    if getattr(button, "_busy", False):
        return "正在操作"
    state = getattr(button, "_state", None)
    if state is None:
        return "状态未知"
    return "已开启" if state else "已关闭"


def _keyboard_owner(w: QWidget) -> str | None:
    """沿父链找「已由桥接层接管键盘激活」的容器类名。

    这类容器本身就是一个 Tab 停靠点，并已实现空格 / 回车的等价操作；
    它内部的动作按钮若还留在 Tab 链里，同一台设备就会出现两个功能
    完全相同的焦点（用户实测反馈）。返回容器类名，没有则返回 None。
    """
    node = w.parentWidget()
    for _ in range(5):
        if node is None:
            break
        name = type(node).__name__
        if name in _ACTIVATABLE:
            return name
        node = node.parentWidget()
    return None


def _is_redundant_action_button(w: QWidget) -> bool:
    """是否是「已可聚焦卡片」内部、键盘功能与卡片重复的动作按钮。

    只认两类：设备卡片 / 电源属性行里的 PowerButton，以及场景卡片里的
    运行按钮（objectName=sceneRunBtn）。托盘快捷行、桌面挂件里的电源钮
    没有可聚焦的父卡片，是当地唯一的键盘入口，必须原样保留。
    """
    if _keyboard_owner(w) is None:
        return False
    if type(w).__name__ == "PowerButton":
        return True
    return (isinstance(w, QAbstractButton)
            and w.objectName() == "sceneRunBtn")


# ---------------------------------------------------------------------------
# 设备详情「运行模式」枚举按钮：把只画在背景色里的选中态交给 UIA
# ---------------------------------------------------------------------------

# 业务 EnumGridSection 用 QButtonGroup(exclusive=False) 加一组普通
# QPushButton，选中项只靠 setStyleSheet 换成主题色背景；按钮本身既不是
# checkable，也没有保存当前值。Qt 的 UIA 于是把「风扇 / 制冷 / 制热 / 除湿」
# 报成四个一模一样的普通按钮，读屏 Tab 过去分不出当前是哪一种模式。
# 名称里写「已选中 / 未选中」是被明令禁止的冗余文案（回归测试也拦着），
# 正解是让按钮真的具备 checkable / checked：Qt 原生把它映射成 UIA 的
# TogglePattern 与 LegacyIAccessible.State，读屏自己念选中态。这里只改
# Qt 状态位，不动样式表、文字、布局、鼠标事件与既有信号；业务 _highlight()
# 的高亮仍由它自己画，包装只在后面顺手把 checked 同步过去。另外按钮原本
# 挂在业务构造的 QButtonGroup(exclusive=False) 里，Qt 一旦发现「组里的按钮
# 变成 checkable」就按单选组处理，把未选中项踢出 Tab 链（详见
# _guard_enum_grid），所以设 checkable 之前必须先把按钮从组里摘出来。

_ENUM_NO_VALUE = object()


def _enum_grid_parts(section: QWidget):
    """取 (按钮列表, 取值列表)；不是枚举模式网格时返回 None。"""
    buttons = getattr(section, "_buttons", None)
    values = getattr(section, "_values", None)
    if (not isinstance(buttons, (list, tuple))
            or not isinstance(values, (list, tuple))
            or not buttons or len(buttons) != len(values)):
        return None
    if not all(isinstance(b, QAbstractButton) for b in buttons):
        return None
    return list(buttons), list(values)


def _sync_enum_checked(section: QWidget, value) -> None:
    """把业务高亮的那一项同步成 checked；未变化的按钮不触发多余事件。"""
    parts = _enum_grid_parts(section)
    if parts is None:
        return
    buttons, values = parts
    for button, item_value in zip(buttons, values):
        active = (value is not None and value is not _ENUM_NO_VALUE
                  and item_value == value)
        if button.isChecked() != active:
            button.setChecked(active)


def _enum_grid_highlighted_value(section: QWidget):
    """扫描晚于业务首次高亮时，从现有 QSS 反推一次当前项。

    选中分支的 QSS 只有 QPushButton 规则，未选中分支还带一条
    QPushButton:hover；不依赖具体主题色，也不改任何样式。推断不出来就
    返回 _ENUM_NO_VALUE（全部保持未选中，比错报一个「已选中」安全）。
    """
    parts = _enum_grid_parts(section)
    if parts is None:
        return _ENUM_NO_VALUE
    buttons, values = parts
    marked = [v for b, v in zip(buttons, values)
              if "QPushButton:hover" not in (b.styleSheet() or "")]
    return marked[0] if len(marked) == 1 else _ENUM_NO_VALUE


def _guard_enum_grid(section: QWidget) -> None:
    """在实例上包一层 _highlight，让选中态随业务高亮/回读同步。

    业务构造时先调 _highlight(None)，随后设备回读走 refresh_value() →
    self._highlight(value)；_pick() 点击也走同一个方法。包住这一个入口
    就覆盖了全部路径，不用改业务源码，也不用猜设备状态。
    """
    parts = _enum_grid_parts(section)
    if parts is None:
        return
    if getattr(section, "_a11y_enum_highlight_original", None) is not None:
        return
    original = getattr(section, "_highlight", None)
    if not callable(original):
        return
    # 业务的 QButtonGroup(exclusive=False) 只是构造时把按钮收拢一下：没接
    # 任何 group 信号，点击全走按钮自己的 clicked。但 Qt 只要看到
    # 「checkable 按钮属于 QButtonGroup」，就按单选组处理——实测四个模式
    # 只剩一个能 Tab 到：focusNextPrevChild() 会跳过未选中项，setChecked()
    # 还会把非焦点按钮的 TabFocus 位清掉，切一次模式就又退化。摘组之后
    # 四个按钮重新都在 Tab 链里，checked 状态与 UIA 的 CheckBox /
    # TogglePattern 仍由 checkable 提供；组对象本身留着，业务信号、外观、
    # 焦点策略一字不动。
    for button in parts[0]:
        group = button.group()
        if group is not None:
            group.removeButton(button)
        if not button.isCheckable():
            button.setCheckable(True)
    # 有自定义 QSS 且没有 :checked 规则时，checked 不改变任何像素；鼠标
    # 按下、松开、clicked 与写入设备的顺序也照旧。
    section._a11y_enum_highlight_original = original

    def highlight_guarded(value, _s=section, _o=original):
        result = _o(value)
        _sync_enum_checked(_s, value)
        return result

    section._highlight = highlight_guarded
    # 详情回读可能在扫描之前就完成了：补一次初始同步，避免这段窗口里
    # 读屏把已经选中的模式也报成「未选中」。
    _sync_enum_checked(section, _enum_grid_highlighted_value(section))


# ---------------------------------------------------------------------------
# 各类控件的可访问名称推导
# ---------------------------------------------------------------------------

def _describe_power_button(w: QWidget) -> tuple[str, str]:
    title, _ = _context(w)
    prefix = f"{title}，" if title else ""
    # PowerButton 不是 checkable，读屏只会报「按钮」；开 / 关 / 离线 / 忙碌
    # 是纯颜色表达的信息，必须留在名称里。
    return f"{prefix}电源开关，{_power_state(w)}", ""


def _describe_device_card(w: QWidget) -> tuple[str, str]:
    device = getattr(w, "device", None)
    if device is None:
        return "未命名设备", ""
    bits = [str(getattr(device, "name", "") or "未命名设备")]
    room = str(getattr(device, "room_name", "") or "")
    if room:
        bits.append(room)
    bits.append("在线" if getattr(w, "_online", getattr(device, "online", True))
                else "离线")
    power = getattr(w, "_power_btn", None)
    can_switch = power is not None and power.isVisible() and power.isEnabled()
    if can_switch:
        bits.append(_power_state(power))
    return "，".join(bits), ""


def _describe_scene_card(w: QWidget) -> tuple[str, str]:
    scene = getattr(w, "scene", None)
    name = str(getattr(scene, "name", "") or "未命名场景")
    home = str(getattr(scene, "home_name", "") or "")
    bits = [name]
    if home:
        bits.append(home)
    return "，".join(bits), ""


def _describe_power_row(w: QWidget) -> tuple[str, str]:
    label = getattr(w, "_state", None)
    text = label.text().strip() if isinstance(label, QLabel) else ""
    if not text:
        text = "已开启" if getattr(w, "_current", False) else "已关闭"
    prop = getattr(w, "prop", None)
    title = str(getattr(prop, "desc", "") or "电源")
    return f"{title}，{text}", ""


def _describe_section(w: QWidget) -> tuple[str, str]:
    prop = getattr(w, "prop", None)
    title = str(getattr(prop, "desc", "") or "设备属性")
    value_label = getattr(w, "_value_label", None)
    value = value_label.text().strip() if isinstance(value_label, QLabel) else ""
    return (f"{title}，{value}" if value else title), ""


def _describe_switch(w: QWidget) -> tuple[str, str]:
    title = _row_title(w)
    if not title:
        title, _ = _context(w)
    if not title:
        title = "选项"
    # checkable QPushButton：选中态由读屏朗读，名称里只放设置项标题
    return title, ""


def _describe_tab(w: QWidget) -> tuple[str, str]:
    text = w.text().strip()
    # 用户实测：原本朗读「场景 复选框 未选中」就已经够用，缺的只是文字。
    # 类型与选中态由读屏负责，不要再往名称里加东西。
    return text, ""


def _describe_slider(w: QWidget, owner: QWidget | None = None) -> tuple[str, str]:
    title, _ = _context(w)
    if not title:
        title = _row_title(w)
    if not title:
        title = "数值调节"
    if owner is None:
        return title, ""
    # 实测 Qt 的 UIA 不为自绘 SiSlider 提供 RangeValuePattern，读屏 Tab 到
    # 滑块时读不到当前数值（数值只画在旁边的 QLabel 里），必须补进名称。
    value_label = getattr(owner, "_value_label", None)
    value = value_label.text().strip() if isinstance(value_label, QLabel) else ""
    return (f"{title}，{value}" if value else title), ""


def _describe_audio_control(w: QWidget, bar: QWidget) -> tuple[str, str] | None:
    """托盘音频栏控件的名称；None 表示本规则不接管，交回通用推导。

    - 三个图标按钮：tooltip 就是用途，播放 / 暂停还会随静音状态更新；
    - 音量条：只写「音量」。它是**原生 QSlider**，Qt 的 UIA 会提供
      RangeValuePattern（CurrentValue / Minimum / Maximum），读屏自己会
      朗读当前音量；名称里再抄一遍数值会变成「音量，42 … 42」。
      自绘 SiSlider 没有 RangeValue，才必须把值写进名称（见上）。
    """
    role = _audio_role(w, bar)
    if role is None:
        return None
    if role == "volume":
        return "音量", ""
    tip = w.toolTip().strip()
    if tip:
        return tip, ""
    return {"prev": "上一首", "next": "下一首", "play": "播放"}[role], ""


def _describe(w: QWidget, *,
              tray_dialog: QWidget | None = None,
              tray_quick: QWidget | None = None,
              schedule_dialog: QWidget | None = None,
              symbol_check: bool = False,
              module_card: QWidget | None = None,
              room_row: QWidget | None = None,
              slider_owner: QWidget | None = None,
              audio_bar: QWidget | None = None) -> tuple[str | None, str | None]:
    """返回 (可访问名称, 可访问描述)；名称 None 表示保持原样。

    描述恒为空：读屏会朗读 HelpText，写帮助文案只会产生拖尾。
    """
    cls = type(w).__name__
    is_button = isinstance(w, QAbstractButton)
    text = w.text().strip() if is_button else ""

    # --- 业务专有控件 -----------------------------------------------------
    # 「添加功能」抽屉：每项唯一的符号按钮必须带功能标题与当前动作
    if module_card is not None:
        return _describe_module_button(w, module_card)
    if room_row is not None:
        return _describe_room_default(w, room_row)
    if schedule_dialog is not None:
        combo = _describe_schedule_combo(w, schedule_dialog)
        if combo is not None:
            return combo
    if audio_bar is not None:
        named = _describe_audio_control(w, audio_bar)
        if named is not None:
            return named
    if symbol_check:
        return _describe_symbol_check(w)
    if tray_dialog is not None:
        # 同一对话框里还有两个原生控件在 UIA 里是空的：搜索框与房间下拉
        if isinstance(w, QLineEdit) and getattr(tray_dialog, "_search", None) is w:
            return _describe_tray_search(w)
        if isinstance(w, QComboBox) and getattr(tray_dialog, "_room_combo", None) is w:
            return _describe_tray_room(w)
    # 托盘快捷窗口：电源钮 / 场景执行钮要带上所属对象，否则整页同名。
    # 只命中该窗口；其它页面的电源钮仍走 _describe_power_button 原路径。
    if tray_quick is not None:
        title = _tray_quick_title(w, tray_quick)
        if title:
            if cls == "PowerButton":
                return f"{title}，电源开关，{_power_state(w)}", ""
            if is_button and w.objectName() == "sceneRunBtn":
                return f"{title}，执行场景", ""
    if cls == "PowerButton":
        return _describe_power_button(w)
    if cls == "DeviceCard":
        return _describe_device_card(w)
    if cls == "SceneCard":
        return _describe_scene_card(w)
    if cls == "PowerRowSection":
        return _describe_power_row(w)
    # 场景卡片的运行钮无文字、也不进 Tab 链，但浏览模式仍要能读出它是什么
    if is_button and w.objectName() == "sceneRunBtn":
        title, _ = _context(w)
        prefix = f"{title}，" if title else ""
        return f"{prefix}执行场景", ""
    if cls in _SECTION_CLASSES:
        return _describe_section(w)
    if cls == "SiSwitchRefactor":
        return _describe_switch(w)
    if cls == "SiToggleButtonRefactor":
        return _describe_tab(w)

    # --- 图标按钮 / 字形按钮 ---------------------------------------------
    if is_button and text in _TITLE_BAR_GLYPHS:
        return _TITLE_BAR_GLYPHS[text], ""
    if is_button and text in ("✕", "×", "X"):
        # 同一个 ✕ 既用于遮罩对话框的关闭按钮，也用于工作台卡片的移除
        # 按钮；后者自带 tooltip「移除」，有 tooltip 时以它为准。
        return w.toolTip().strip() or "关闭对话框", ""
    if cls == "_IconButton":
        return w.toolTip().strip() or "按钮", ""

    tip = w.toolTip().strip() if hasattr(w, "toolTip") else ""
    if is_button:
        if tip in _STATUS_PILL_TIPS:
            # 胶囊的数值会变，每次扫描都重新拼，读屏才知道当前读数
            return (f"{tip}，{text}" if _readable(text) else tip), ""
        if not _readable(text) and tip:
            # ↑ / ↓ / ✕ / 图标字形：tooltip 已经写明了按钮用途
            return tip, ""
        # 文字里混了装饰符号（如「+ 添加功能」），名称只留可读部分
        if text and text[0] in _DECORATIVE_GLYPHS:
            cleaned = " ".join(text[1:].split())
            if _readable(cleaned):
                return cleaned, ""
        # 枚举按钮挂在 FlowLayout 的 grid_host 里，不一定直接以
        # EnumGridSection 为父；它自带文字，名称保持原文即可。选中态由
        # checkable 交给读屏朗读，名称里绝不追加「已选中 / 未选中」。
        node = w.parentWidget()
        for _ in range(3):
            if node is None:
                break
            if type(node).__name__ == "EnumGridSection":
                return text, ""
            node = node.parentWidget()
        return None, ""

    # --- 滑块 -------------------------------------------------------------
    # QScrollBar 同样继承 QAbstractSlider，但它有 Qt 自带的滚动条语义与
    # 名称，不能套用「数值调节」；否则 UIA 树里会出现假的滑块节点。
    if isinstance(w, QAbstractSlider) and not isinstance(w, QScrollBar):
        return _describe_slider(w, slider_owner)

    return None, ""


def _semantic_key(w: QWidget) -> tuple[str, str] | None:
    """键盘焦点的语义标识，用于控件重建后把焦点放回同一个对象。"""
    cls = type(w).__name__
    if cls == "DeviceCard":
        device = getattr(w, "device", None)
        return ("card", str(getattr(device, "did", ""))) if device else None
    if cls == "SceneCard":
        scene = getattr(w, "scene", None)
        return ("scene", str(getattr(scene, "scene_id", ""))) if scene else None
    if cls == "SiToggleButtonRefactor":
        return ("tab", w.text().strip())
    # 托盘快捷行没有可聚焦的父卡片，按钮本身就是当地唯一的键盘入口；
    # 整窗重建后靠 did / scene_id 把焦点放回同一台设备的新按钮。
    tray_key = _tray_quick_button_key(w)
    if tray_key is not None:
        return tray_key
    # 卡片内的 PowerButton 已退出 Tab 链，不再作为键盘焦点恢复的锚点；
    # 一台设备在 Tab 链里只对应一个语义键，就是卡片本身的 ("card", did)。
    return None


# ---------------------------------------------------------------------------
# 桥接器
# ---------------------------------------------------------------------------

class AccessibilityBridge(QObject):
    """挂在 QApplication 上的全局无障碍桥接器。

    事件驱动（Show / ChildAdded / Polish）负责及时装饰新建控件，低频定时器
    负责同步颜色和状态变化产生的名称差异。所有入口都容错，且事件过滤器
    除键盘激活外一律 ``return False``，绝不吞掉正常事件。
    """

    _SCAN_INTERVAL_MS = 800

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._scanning = False
        self._pending = False
        self._kb_focus_key: tuple[str, str] | None = None
        # (托盘快捷窗口, 该窗口内 Tab 到达的设备 / 场景语义键)
        self._tray_anchor: tuple[QWidget, tuple[str, str]] | None = None
        # 用户确实按过 Tab / Shift+Tab：用来区分「主动换焦点」与 Qt 在按钮
        # 被禁用、控件被销毁时自己把焦点挪走（后者同样报 TabFocusReason）
        self._tab_pending = False
        self._live: QLabel | None = None
        self._last_announced = ""
        # 按来源去重：Toast 停留期间每轮扫描都会看到它，不能反复播报
        self._announced_sources: dict[str, str] = {}
        # 每轮扫描重建，只保留本轮仍存活的控件，避免持有已销毁的 QWidget
        self._names: dict[QWidget, str] = {}
        self._shortcuts_done = False
        # 每个滑块一个单发去抖计时器：连续按方向键只在最后一次停下后提交
        self._slider_timers: dict[QWidget, QTimer] = {}

        # 必须在任何 QComboBox 被 UIA 查询之前装好工厂
        _install_quiet_combo_factory()
        app.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(self._SCAN_INTERVAL_MS)
        self._timer.timeout.connect(self.scan)
        self._timer.start()
        QTimer.singleShot(0, self.scan)

    # -- 事件过滤 ---------------------------------------------------------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            etype = event.type()
            if (etype in (QEvent.Type.MouseButtonPress,
                          QEvent.Type.MouseButtonDblClick)
                    and isinstance(obj, QWidget)
                    and obj.property("_a11y_audio_control")):
                # 鼠标发起的操作不该被「键盘忙碌」守卫挡住：清掉标记，
                # 业务原本的 setEnabled(False) 变暗反馈照常发生。
                obj.setProperty("_a11y_kb_disable", False)
            if (etype in (QEvent.Type.MouseButtonPress,
                          QEvent.Type.MouseButtonRelease,
                          QEvent.Type.MouseButtonDblClick)
                    and isinstance(obj, QWidget)
                    and getattr(obj, "_a11y_busy_hold", False)):
                # 忙碌期间按钮保持启用（为了不抢焦点），在这里等价补回
                # 「防连点」：按下与抬起一并吞掉，不会发出 clicked。
                return True
            if etype in (
                    QEvent.Type.Show, QEvent.Type.ShowToParent,
                    QEvent.Type.ChildAdded, QEvent.Type.Polish,
                    QEvent.Type.EnabledChange):
                self._schedule_scan()
            elif etype == QEvent.Type.FocusIn and isinstance(obj, QWidget):
                # Tab 到达按钮的瞬间就装好守卫，不必等下一轮 800ms 扫描；
                # 用户随后的空格 / 回车一定晚于这次 FocusIn。
                if (isinstance(obj, QWidget)
                        and obj.property("_a11y_audio_control")):
                    try:
                        _guard_audio_enabled(obj)
                    except Exception:
                        pass
                if isinstance(obj, QAbstractButton):
                    try:
                        _guard_tray_button_enabled(obj)
                    except Exception:
                        pass
                reason = event.reason()  # type: ignore[attr-defined]
                if reason in (Qt.FocusReason.TabFocusReason,
                              Qt.FocusReason.BacktabFocusReason):
                    # 只记键盘焦点：鼠标用户的焦点流完全不参与焦点恢复
                    self._kb_focus_key = _semantic_key(obj)
                    tray_key = _tray_quick_button_key(obj)
                    if self._tab_pending:
                        # 真的按了 Tab / Shift+Tab：落点就是用户想去的地方
                        self._tray_anchor = (
                            (_tray_quick_window(obj), tray_key)
                            if tray_key is not None else None)
                        self._tab_pending = False
                    elif self._tray_anchor is None and tray_key is not None:
                        self._tray_anchor = (_tray_quick_window(obj), tray_key)
                    # 其余情况是 Qt 的被动焦点转移（忙碌禁用 / 控件销毁），
                    # 保留原锚点，等 _stabilize_tray_focus 把焦点放回去。
                elif reason in (Qt.FocusReason.MouseFocusReason,
                                Qt.FocusReason.PopupFocusReason):
                    # 鼠标 / 弹出菜单改了焦点就撤销托管，别把焦点抢回去
                    if isinstance(obj, (QAbstractButton, QLineEdit,
                                        QComboBox, QAbstractSlider)):
                        self._tray_anchor = None
                        if obj.property("_a11y_audio_control"):
                            obj.setProperty("_a11y_kb_disable", False)
            elif etype == QEvent.Type.KeyPress and isinstance(obj, QWidget):
                key = event.key()  # type: ignore[attr-defined]
                if (getattr(obj, "_a11y_busy_hold", False)
                        and key in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                    Qt.Key.Key_Enter)):
                    # 忙碌期间不再重复激活，等价于原 disabled 行为
                    return True
                if obj.property("_a11y_audio_control"):
                    modifiers = event.modifiers()  # type: ignore[attr-defined]
                    adjust = (obj.property("_a11y_slider")
                              and isinstance(obj, QAbstractSlider)
                              and self._slider_new_value(
                                  obj, key, modifiers) is not None)
                    if (key in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                Qt.Key.Key_Enter) or adjust):
                        # 这次操作由键盘发起，随后业务层的忙碌禁用不能抢走
                        # 焦点。空格激活的 clicked 要到 KeyRelease 才发出，
                        # 所以标记不在这里定时清：由守卫命中、业务重新启用、
                        # 鼠标按下或 _commit_slider 的 finally 负责清除。
                        # 音量条是 400ms 后去抖提交，届时另行设标记。
                        obj.setProperty("_a11y_kb_disable", True)
                if (key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
                        and self._tray_anchor is not None):
                    # FocusIn 由 Qt 在 keyPressEvent 内同步派发，随后这一次
                    # 事件循环就把标记清掉，不会沾染之后的被动焦点转移
                    self._tab_pending = True
                    QTimer.singleShot(0, self._clear_tab_pending)
                if (self._tray_anchor is not None
                        and key in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                                    Qt.Key.Key_Enter)
                        and self._block_busy_tray_key(obj)):
                    return True
                # 托盘快捷窗口的设备行：与主界面设备卡片保持一致——空格
                # 切电源（QPushButton 原生 clicked），回车打开设备详情。
                # 必须排在 _a11y_enter_proxy 之前，否则回车会先被等价转成
                # click()，等于又切了一次电源；转发到窗口既有的
                # open_device_requested(did) 信号，与鼠标点击行本体同一条
                # 路径。函数内部只认设备行 PowerButton，场景行、音频栏、
                # 标题栏、主界面卡片与其它窗口的按钮一概不受影响。
                if (key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                        and not event.isAutoRepeat()  # type: ignore[attr-defined]
                        and _tray_device_open_detail(obj)):
                    return True
                # 关掉 autoDefault 后按钮不再自己处理回车，在这里等价补回
                # click() 并吞掉事件，避免 QDialog 把它转交给别的按钮。
                # 这与 Qt 原来的「回车激活焦点按钮」副作用完全一致。
                if (isinstance(obj, QPushButton)
                        and obj.property("_a11y_enter_proxy")
                        and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
                    try:
                        obj.click()
                        return True
                    except Exception:
                        pass
                if (obj.property("_a11y_slider")
                        and isinstance(obj, QAbstractSlider)):
                    modifiers = event.modifiers()  # type: ignore[attr-defined]
                    if (getattr(obj, "_a11y_busy_hold", False)
                            and self._slider_new_value(
                                obj, key, modifiers) is not None):
                        # 提交期间等价于原 disabled：方向键也一并吞掉
                        return True
                    if self._adjust_slider(obj, key, modifiers):
                        return True
                if obj.property("_a11y_activatable") and not isinstance(
                        obj, QAbstractButton):
                    if key in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                               Qt.Key.Key_Enter):
                        if self._activate(obj, key):
                            return True
        except Exception:
            # 无障碍层的任何异常都不能影响主程序
            pass
        return False

    def _schedule_scan(self) -> None:
        if self._pending or self._scanning:
            return
        self._pending = True
        QTimer.singleShot(0, self._run_pending_scan)

    def _run_pending_scan(self) -> None:
        self._pending = False
        self.scan()

    def _clear_tab_pending(self) -> None:
        self._tab_pending = False

    # -- 托盘快捷窗口：忙碌期间不误切设备、重建后焦点回到原设备 ----------

    def _block_busy_tray_key(self, obj: QWidget) -> bool:
        """兜底：万一焦点仍漂到下一颗开关，吞掉忙碌期间的激活键。

        正常路径下 ``_guard_tray_button_enabled`` 已让焦点原地不动；这里
        覆盖按钮未及时装上守卫、或业务以别的方式禁用按钮的情况，避免
        用户紧接着按空格切到另一台设备。
        """
        anchor = self._tray_anchor
        if anchor is None:
            return False
        win, key = anchor
        try:
            import shiboken6

            if not shiboken6.isValid(win) or not win.isVisible():
                return False
        except Exception:
            return False
        if _tray_quick_window(obj) is not win or _semantic_key(obj) == key:
            return False
        # 只管漂移到同窗口另一颗托盘行按钮的情况；标题栏按钮、输入框等
        # 由用户正常 Tab 过去的操作一概不拦。
        if _tray_quick_button_key(obj, win) is None:
            return False
        for btn in _tray_quick_live_buttons(win):
            if _semantic_key(btn) == key:
                return bool(getattr(btn, "_a11y_busy_hold", False)
                            or not btn.isEnabled())
        return False

    @staticmethod
    def _guard_tray_windows(widgets: list[QWidget]) -> None:
        """给托盘快捷窗口与它的行按钮装守卫（幂等）。"""
        for w in widgets:
            if type(w).__name__ == "TrayQuickWindow":
                try:
                    _guard_tray_rebuild(w)
                    for btn in _tray_quick_live_buttons(w):
                        _guard_tray_button_enabled(btn)
                except Exception:
                    pass

    def _stabilize_tray_focus(self) -> None:
        """重建或忙碌结束后，把键盘焦点放回同一台设备 / 场景的新按钮。

        只在「Tab 到达过某台托盘设备」且焦点已不在它身上时动作；用户按
        Tab 前进会刷新锚点，鼠标点击会清掉锚点，都不会被拉回来。
        """
        anchor = self._tray_anchor
        if anchor is None:
            return
        win, key = anchor
        try:
            import shiboken6

            if not shiboken6.isValid(win) or not win.isVisible():
                self._tray_anchor = None
                return
        except Exception:
            self._tray_anchor = None
            return
        try:
            live = _tray_quick_live_buttons(win)
            current = QApplication.focusWidget()
            if current is not None and _semantic_key(current) == key:
                if current.isVisible() and any(b is current for b in live):
                    return
            for btn in live:
                if (_semantic_key(btn) == key and btn.isVisible()
                        and btn.isEnabled()):
                    # 用 OtherFocusReason：不冒充用户按了 Tab，锚点保持不变
                    btn.setFocus(Qt.FocusReason.OtherFocusReason)
                    return
        except Exception:
            pass

    # -- 键盘激活：只转发到已有信号 / 方法 --------------------------------

    @staticmethod
    def _activate(w: QWidget, key: int) -> bool:
        cls = type(w).__name__
        try:
            if cls == "DeviceCard":
                if key == Qt.Key.Key_Space:
                    power = getattr(w, "_power_btn", None)
                    if power is None or not power.isVisible() or not power.isEnabled():
                        announce_static("该设备不支持在此切换电源")
                        return True
                    w.power_clicked.emit(w.device.did)
                else:
                    w.open_requested.emit(w.device.did)
                return True
            if cls == "SceneCard":
                # 走按钮的 click()，与鼠标点击右侧运行钮完全同一条路径
                w._run_btn.click()
                return True
            if cls == "PowerRowSection":
                w._toggle()
                return True
        except Exception:
            pass
        return False

    # -- 数值滑块：键盘等价于鼠标「拖动 + 松手」 ---------------------------

    @staticmethod
    def _slider_new_value(slider: QAbstractSlider, key: int,
                          modifiers) -> int | None:
        """把按键换算成目标值；不认识的键返回 None（交回 Qt 处理）。

        方向键走 ``singleStep``；Shift+方向键 / Page 键走大步长。大步长取
        ``pageStep``、量程的 1/10、10 倍单步三者中的最大值：温度 16-30
        仍是 10，色温 2700-6500 才会自动放大到 380，避免按几千次方向键。
        """
        small = max(1, slider.singleStep())
        span = slider.maximum() - slider.minimum()
        big = max(slider.pageStep() or small * 10,
                  max(1, (span + 9) // 10), small * 10)
        fast = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        delta: int | None = None
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            delta = -big if fast else -small
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            delta = big if fast else small
        elif key == Qt.Key.Key_PageUp:
            delta = big
        elif key == Qt.Key.Key_PageDown:
            delta = -big
        if delta is None:
            if key == Qt.Key.Key_Home:
                return slider.minimum()
            if key == Qt.Key.Key_End:
                return slider.maximum()
            return None
        return max(slider.minimum(),
                   min(slider.maximum(), slider.value() + delta))

    def _adjust_slider(self, slider: QAbstractSlider, key: int,
                       modifiers) -> bool:
        """改值 + 安排提交；只对识别出的按键生效，其余交回 Qt。"""
        try:
            new = self._slider_new_value(slider, key, modifiers)
        except Exception:
            return False
        if new is None:
            return False
        try:
            if new != slider.value():
                # 走 Python 覆写的 setValue：预览文字、轨道动画、tooltip
                # 与鼠标拖动完全同一条路径；valueChanged 仍由原连接处理
                slider.setValue(new)
                self._schedule_slider_commit(slider)
            # 已到边界也吞掉按键，避免 QDialog 把它拿去导航
            return True
        except Exception:
            return False

    def _schedule_slider_commit(self, slider: QAbstractSlider) -> None:
        timer = self._slider_timers.get(slider)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=slider: self._commit_slider(s))
            self._slider_timers[slider] = timer
        timer.start(_SLIDER_COMMIT_MS)

    def _commit_slider(self, slider: QAbstractSlider) -> None:
        """停止按键后调用与鼠标松手完全相同的业务提交方法下发到设备。

        设备详情的自绘 SiSlider 走 ``SliderSection._commit()``；托盘小爱
        音频栏的原生 QSlider 走 ``_TrayAudioBar._on_slider_commit()``。
        两个方法都由业务源码提供，桥接层只在键盘路径上等价触发一次。
        """
        try:
            import shiboken6
            if not shiboken6.isValid(slider) or not slider.isEnabled():
                return
            owner = _slider_owner(slider)
            if owner is not None:
                owner._commit()
            else:
                bar = _audio_bar(slider)
                if bar is None or _audio_role(slider, bar) != "volume":
                    return
                # _on_slider_commit() 内部会 setEnabled(False) 防连点；
                # 键盘焦点在滑块上时由守卫保持启用，避免读屏念到下一项。
                slider.setProperty("_a11y_kb_disable", True)
                try:
                    bar._on_slider_commit()
                finally:
                    slider.setProperty("_a11y_kb_disable", False)
            # 立刻把最终值写进名称，并让读屏重读焦点控件
            self._decorate(slider, self._names)
            self._raise_event(slider)
        except Exception:
            pass

    # -- 扫描与装饰 -------------------------------------------------------

    def scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        try:
            try:
                widgets = list(QApplication.allWidgets())
            except Exception:
                return
            _prune_combo_interfaces()
            previous = self._names
            self._names = {}
            # 详情页反复打开会重建滑块：清掉已销毁控件的去抖计时器
            try:
                import shiboken6
                for s in list(self._slider_timers):
                    if not shiboken6.isValid(s):
                        timer = self._slider_timers.pop(s, None)
                        if timer is not None:
                            timer.deleteLater()
            except Exception:
                pass
            for w in widgets:
                try:
                    self._decorate(w, previous)
                except Exception:
                    continue
            try:
                self._guard_tray_windows(widgets)
            except Exception:
                pass
            try:
                self._install_shortcuts()
                self._restore_keyboard_focus(widgets)
                self._stabilize_tray_focus()
                self._collect_announcements(widgets)
            except Exception:
                pass
        finally:
            self._scanning = False

    def _decorate(self, w: QWidget, previous: dict[QWidget, str]) -> None:
        cls = type(w).__name__
        tray_quick = _tray_quick_window(w)
        audio_bar = _audio_bar(w)

        # 0) 已从网格摘除、只等 deleteLater() 的旧行，删除前仍 visible 且
        #    可聚焦，Tab 链里会多出一整套同名按钮。整行（含子控件）退出
        #    Tab 链；不改显示、鼠标事件与任何信号，稍后 Qt 会自己删掉它。
        if tray_quick is not None and _tray_quick_orphan_row(w, tray_quick):
            try:
                if w.focusPolicy() != Qt.FocusPolicy.NoFocus:
                    w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                for child in w.findChildren(QWidget):
                    if child.focusPolicy() != Qt.FocusPolicy.NoFocus:
                        child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            except Exception:
                pass

        # 1) 名称与描述
        tray_dialog = _tray_dialog(w)
        schedule_dialog = _schedule_dialog(w)
        slider_owner = _slider_owner(w)
        name, _ = _describe(
            w,
            tray_dialog=tray_dialog,
            tray_quick=tray_quick,
            schedule_dialog=schedule_dialog,
            symbol_check=_symbol_check(w),
            module_card=_module_button_card(w),
            room_row=_room_default_row(w),
            slider_owner=slider_owner,
            audio_bar=audio_bar,
        )
        if name:
            if w.accessibleName() != name:
                old = previous.get(w)
                w.setAccessibleName(name)
                old_token = _status_token(old) if old else ""
                new_token = _status_token(name)
                if (cls in _STATEFUL and w.isVisible()
                        and old_token and new_token and old_token != new_token):
                    self.announce(name)
            self._names[w] = name

        # 2) 不设置 accessibleDescription：它会变成 UIA 的 HelpText，被读屏
        #    追加到名称后面朗读，形成「……保存后生效。 回车」这类拖尾。
        #    tooltip 同样不写入 HelpText；名称已经承载了全部缺失的文字。
        #
        #    唯一例外是定时任务窗口：Qt 的 UIA 在描述为空时会回退用
        #    toolTip() 填 UIA_FullDescriptionProperty，读屏照样把悬停说明
        #    追加念一遍（含「回车从候选里选」这类操作提示、开关的帮助文案、
        #    与删除按钮名称完全重复的 tooltip）。设一个空白占位挡住回退：
        #    toolTip() 本身不动，视觉提示照旧，HelpText 仍为空。
        if schedule_dialog is not None:
            try:
                if (w.toolTip().strip()
                        and w.accessibleDescription() != _UIA_BLANK_DESCRIPTION):
                    w.setAccessibleDescription(_UIA_BLANK_DESCRIPTION)
            except Exception:
                pass

        # 3) 自绘卡片补焦点与键盘激活（只做一次，避免动态属性事件回环）
        if cls in _ACTIVATABLE:
            if not w.property("_a11y_activatable"):
                w.setProperty("_a11y_activatable", True)
            if w.focusPolicy() == Qt.FocusPolicy.NoFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 3a) 消除重复的 Tab 停靠点：卡片自身已可聚焦并承担空格 / 回车，
        #     内嵌的动作按钮不再参与 Tab 链。只改焦点策略，鼠标点击、
        #     clicked 信号、按钮外观与可用性都不变；父卡片未接管键盘的
        #     托盘行 / 挂件卡片不在范围内（见 _is_redundant_action_button）。
        if _is_redundant_action_button(w):
            if w.focusPolicy() != Qt.FocusPolicy.NoFocus:
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 3b) 枚举模式按钮：业务只把选中态画在背景色里，UIA 完全拿不到，
        #     四个模式读起来一模一样。让按钮具备原生 checkable 状态并随
        #     _highlight 同步（见 _guard_enum_grid），读屏自己念选中态；
        #     包装里同时把按钮摘出 QButtonGroup，四个模式才都还在 Tab 链。
        #     点击后原有的那一次语音反馈保持不变。
        if cls == "EnumGridSection":
            try:
                _guard_enum_grid(w)
                for button in w.findChildren(QAbstractButton):
                    if button.property("_a11y_pick_wired"):
                        continue
                    button.setProperty("_a11y_pick_wired", True)
                    label = button.text().strip()
                    button.clicked.connect(
                        lambda _checked=False, t=label: self.announce(
                            f"已选择：{t}"))
            except Exception:
                pass

        # 3c) 去掉 Qt 给「焦点按钮」附加的 Enter 快捷键。
        #     QDialog 里 autoDefault=True 的 QPushButton 一旦获得焦点就会被
        #     Qt 当成临时默认按钮，UIA 的 LegacyKeyboardShortcut 随之变成
        #     'Enter'，NVDA 于是在每个按钮名称后多念一声「回车」。关闭该
        #     属性后快捷键消失；「回车激活焦点按钮」的原有行为由
        #     eventFilter 的 _a11y_enter_proxy 分支等价补回，鼠标、空格、
        #     外观与既有信号连接都不变。项目在托盘快捷窗口 / 桌面挂件里
        #     本来就有 setAutoDefault(False) 的先例，这里再设一次是幂等的。
        if isinstance(w, QPushButton):
            if w.autoDefault():
                w.setAutoDefault(False)
            if w.isDefault():
                w.setDefault(False)
            if not w.property("_a11y_enter_proxy"):
                w.setProperty("_a11y_enter_proxy", True)

        # 3d) 数值滑块补 Tab 焦点。SiSlider 默认 NoFocus，键盘根本到不了；
        #     只改焦点策略，绘制、QSS、布局、鼠标事件与信号全都不动，自绘
        #     控件本来也不画焦点框，鼠标用户看不出任何差别。
        if slider_owner is not None:
            if w.focusPolicy() == Qt.FocusPolicy.NoFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            if not w.property("_a11y_slider"):
                w.setProperty("_a11y_slider", True)

        # 3f) 托盘小爱音频栏：三颗切歌钮与音量条在业务里被显式设了
        #     NoFocus，Tab 根本到不了。只补焦点策略与键盘入口；鼠标拖动、
        #     绘制、QSS、布局与既有信号连接一个字都不改。音量条同时挂上
        #     通用的 ``_a11y_slider``，方向键 / Page / Home / End 就会走
        #     「改值 + 停止按键后下发一次」的既有逻辑。
        if audio_bar is not None:
            role = _audio_role(w, audio_bar)
            if role is not None:
                if w.focusPolicy() == Qt.FocusPolicy.NoFocus:
                    w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                if not w.property("_a11y_audio_control"):
                    w.setProperty("_a11y_audio_control", True)
                if role == "volume" and not w.property("_a11y_slider"):
                    w.setProperty("_a11y_slider", True)
                _guard_audio_enabled(w)
                # 三个图标按钮的 tooltip 与名称一模一样，Qt 会把 tooltip
                # 回退进 UIA FullDescription，读屏于是把「上一首」念两遍。
                # 只在这三个按钮上设空白占位：toolTip() 本身不动，明眼用户
                # 的悬停提示照旧，HelpText 仍为空。下拉框的 tooltip 反而
                # 是它唯一用途说明（Name 为空），不能挡。
                if (role != "volume" and w.toolTip().strip()
                        and w.accessibleDescription() != _UIA_BLANK_DESCRIPTION):
                    w.setAccessibleDescription(_UIA_BLANK_DESCRIPTION)

        # 3e) 去掉无名滚动容器那个只读「分组」的空 Tab 停靠点。它没有任何
        #     可操作语义，也不是改造引入的（Qt 给 QScrollArea 默认
        #     StrongFocus）。只改焦点策略：滚轮、滚动条、鼠标拖拽、内部控件
        #     的焦点策略与 Tab 顺序全都照旧；有名字的容器不受影响。
        if _is_plain_scroll_area(w):
            if w.focusPolicy() != Qt.FocusPolicy.NoFocus:
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 4) 无边框遮罩对话框补窗口标题（不改变任何像素）
        if isinstance(w, QDialog) and not w.windowTitle():
            title = _WINDOW_TITLES.get(cls)
            if title:
                w.setWindowTitle(title)

        # 5) 扫码登录：二维码是图片，只补一个名称；不写操作指引。
        if cls == "LoginDialog":
            qr = getattr(w, "_qr_label", None)
            if isinstance(qr, QLabel) and not qr.accessibleName():
                qr.setAccessibleName("登录二维码")

    # -- 状态与结果播报 ---------------------------------------------------

    def _collect_announcements(self, widgets: list[QWidget]) -> None:
        seen_toast = False
        seen_login = False
        for w in widgets:
            cls = type(w).__name__
            if cls == "Toast" and w.isVisible():
                seen_toast = True
                for label in w.findChildren(QLabel):
                    text = label.text().strip()
                    if text:
                        self._announce_once("toast", f"通知：{text}")
            elif cls == "LoginDialog" and w.isVisible():
                seen_login = True
                label = getattr(w, "_status_label", None)
                if isinstance(label, QLabel):
                    text = label.text().strip()
                    if text:
                        self._announce_once("login", f"登录状态：{text}")
        # 浮层消失后清掉去重记录，下一条相同文案的提示仍能播报
        if not seen_toast:
            self._announced_sources.pop("toast", None)
        if not seen_login:
            self._announced_sources.pop("login", None)

    def _announce_once(self, source: str, text: str) -> None:
        if self._announced_sources.get(source) == text:
            return
        self._announced_sources[source] = text
        self.announce(text)

    def announce(self, text: str) -> None:
        """把一段文本送进隐藏的 live region，并尽力触发 UIA 变更事件。"""
        if not text or text == self._last_announced:
            return
        self._last_announced = text
        live = self._live_widget()
        if live is None:
            return
        try:
            live.setText(text)
            live.setAccessibleName(text)
            self._raise_event(live)
        except Exception:
            pass

    @classmethod
    def announce_static(cls, text: str) -> None:
        """供 _activate 静态方法使用：找到当前桥接器再播报。"""
        app = QApplication.instance()
        bridge = getattr(app, "_a11y_bridge", None) if app else None
        if bridge is not None:
            bridge.announce(text)

    def _live_widget(self) -> QLabel | None:
        if self._live is not None:
            return self._live
        tops = list(QApplication.topLevelWidgets())
        host = None
        for top in tops:
            if type(top).__name__ == "MainWindow":
                host = top
                break
        if host is None and tops:
            # 托盘静默启动 / 登录窗口先于主窗口出现时仍能播报
            host = tops[0]
        if host is None:
            host = QWidget()
            host.setWindowFlags(Qt.WindowType.Tool)
            self._live_host = host  # 保活
            host.show()
        label = QLabel(host)
        label.setObjectName("a11yLiveRegion")
        label.setFixedSize(1, 1)
        label.move(-10, -10)  # 客户区外，不进任何布局、不占可见区域
        label.setStyleSheet("background: transparent; color: transparent;")
        label.show()
        self._live = label
        return label

    @staticmethod
    def _raise_event(widget: QWidget) -> None:
        if QAccessible is None or QAccessibleEvent is None:
            return
        event_type = None
        event_enum = getattr(QAccessible, "Event", None)
        if event_enum is not None:
            event_type = getattr(event_enum, "NameChanged", None)
        if event_type is None:
            event_type = getattr(QAccessible, "NameChanged", None)
        if event_type is None:
            return
        try:
            QAccessible.updateAccessibility(QAccessibleEvent(widget, event_type))
        except Exception:
            pass

    # -- 焦点恢复（仅键盘焦点） -------------------------------------------

    def _restore_keyboard_focus(self, widgets: list[QWidget]) -> None:
        key = self._kb_focus_key
        # 托盘快捷行由 _stabilize_tray_focus 统一托管，避免两处抢焦点
        if key is None or key[0] in ("tray-device", "tray-scene"):
            return
        current = QApplication.focusWidget()
        if current is not None:
            if _semantic_key(current) == key:
                return
            if isinstance(current, (QAbstractButton, QLineEdit, QComboBox,
                                    QAbstractSlider)):
                # 用户主动把焦点移到别的交互控件，放弃恢复
                self._kb_focus_key = None
                return
        self._kb_focus_key = None
        if QApplication.activeModalWidget() is not None:
            return
        for w in widgets:
            if w.isVisible() and _semantic_key(w) == key:
                QTimer.singleShot(
                    0, lambda target=w: _safe_focus(target))
                break

    # -- 全局快捷键（纯新增，不与既有交互冲突） ---------------------------

    def _install_shortcuts(self) -> None:
        if self._shortcuts_done:
            return
        window = None
        for top in QApplication.topLevelWidgets():
            if type(top).__name__ == "MainWindow":
                window = top
                break
        if window is None:
            return
        self._shortcuts_done = True

        def bind(seq: str, handler) -> None:
            try:
                shortcut = QShortcut(QKeySequence(seq), window)
                shortcut.activated.connect(handler)
                window._a11y_shortcuts.append(shortcut)  # 保活
            except Exception:
                pass

        window._a11y_shortcuts = []

        # F5 / Alt+S：直达刷新与设置，读屏用户不必在无名图标里摸索
        if hasattr(window, "load_devices"):
            bind("F5", window.load_devices)
        if hasattr(window, "show_settings"):
            bind("Alt+S", window.show_settings)

        # Ctrl+L：焦点回到设备列表第一张卡片（网格重建后尤其有用）
        def focus_first_card() -> None:
            cards = getattr(window, "_cards", {}) or {}
            for card in cards.values():
                if card.isVisible():
                    card.setFocus(Qt.FocusReason.TabFocusReason)
                    return
            announce_static("当前没有可用设备")

        bind("Ctrl+L", focus_first_card)

        # Alt+1..9：直接切到第 N 个房间 / 场景选项卡
        for index in range(1, 10):
            def switch_tab(n=index) -> None:
                buttons = list(getattr(window, "_tab_buttons", {}).values())
                if 0 < n <= len(buttons):
                    buttons[n - 1].click()
                else:
                    announce_static(f"没有第 {n} 个房间")

            bind(f"Alt+{index}", switch_tab)


# ---------------------------------------------------------------------------
# 下拉框：把 Qt 内置的「Down」快捷键提示报成空
# ---------------------------------------------------------------------------

if QAccessibleWidget is not None:

    class _QuietComboBoxAccessible(QAccessibleWidget):
        """QComboBox 无障碍接口的透明代理，只隐藏 ``Accelerator``。

        Qt 把 ``QAccessible::Accelerator`` 映射成 UIA 的
        ``LegacyIAccessible.KeyboardShortcut``；原生接口固定返回 ``Down``，
        读屏于是在每个下拉框后面追加一声「下方向」。除这一项外，所有调用
        都转发给原生接口，保证名称、描述、当前值、子节点、角色、状态与
        UIA Pattern 和原版完全一致。
        """

        def __init__(self, widget: QComboBox, native: Any):
            super().__init__(widget, QAccessible.Role.ComboBox)
            self._native = native

        def text(self, t):  # noqa: N802 - Qt 接口命名
            if t == QAccessible.Text.Accelerator:
                return ""
            return self._native.text(t)

        def role(self):  # noqa: N802
            return self._native.role()

        def state(self):  # noqa: N802
            return self._native.state()

        def rect(self):  # noqa: N802
            return self._native.rect()

        def childCount(self):  # noqa: N802
            return self._native.childCount()

        def child(self, index):  # noqa: N802
            return self._native.child(index)

        def indexOfChild(self, child):  # noqa: N802
            return self._native.indexOfChild(child)

        def parent(self):  # noqa: N802
            return self._native.parent()

        def childAt(self, x, y):  # noqa: N802
            return self._native.childAt(x, y)

        def setText(self, t, text):  # noqa: N802
            return self._native.setText(t, text)

        def isValid(self):  # noqa: N802
            return self._native.isValid()

        def object(self):  # noqa: N802
            return self._native.object()

else:  # pragma: no cover - PySide6 版本缺 QAccessibleWidget 时静默降级
    _QuietComboBoxAccessible = None  # type: ignore[assignment]


# 保活代理与它委托的原生接口，避免 Qt 持有的 C++ 指针变成悬空 Python wrapper。
_combo_interfaces: dict[int, Any] = {}
_combo_factory_busy = False
_combo_factory_installed = False


def _quiet_combo_factory(classname: str, obj: QObject):
    """QAccessible 工厂：只对 QComboBox 返回代理，其余一律交回 Qt。"""
    global _combo_factory_busy
    if (_combo_factory_busy or _QuietComboBoxAccessible is None
            or QAccessible is None or classname != "QComboBox"
            or not isinstance(obj, QComboBox)):
        return None

    key = id(obj)
    cached = _combo_interfaces.get(key)
    if cached is not None:
        widget, wrapper = cached
        try:
            import shiboken6
            if shiboken6.isValid(widget):
                return wrapper
        except Exception:
            pass
        _combo_interfaces.pop(key, None)

    # 取原生接口时临时关掉自己，避免 queryAccessibleInterface 递归回工厂。
    _combo_factory_busy = True
    try:
        native = QAccessible.queryAccessibleInterface(obj)
    except Exception:
        native = None
    finally:
        _combo_factory_busy = False
    if native is None:
        return None
    try:
        wrapper = _QuietComboBoxAccessible(obj, native)
        _combo_interfaces[key] = (obj, wrapper)
        # destroyed 带一个 QObject 参数，用 *_ 吸收，别覆盖 key 默认值
        obj.destroyed.connect(
            lambda *_, _key=key: _combo_interfaces.pop(_key, None))
    except Exception:
        return None
    return wrapper


def _prune_combo_interfaces() -> None:
    """清掉已销毁控件的代理接口。

    ``destroyed`` 信号在个别 Qt 小版本 / 离屏环境下不一定派发，靠每轮扫描
    兜底，避免反复开关对话框后缓存里堆积失效的 wrapper（也避免 C++ 对象
    地址被复用时误命中旧代理）。
    """
    try:
        import shiboken6
        for key, (widget, _wrapper) in list(_combo_interfaces.items()):
            if not shiboken6.isValid(widget):
                _combo_interfaces.pop(key, None)
    except Exception:
        pass


def _install_quiet_combo_factory() -> None:
    """安装一次；Qt 版本缺 API 时静默降级为原生的「下方向」提示。"""
    global _combo_factory_installed
    if (_combo_factory_installed or QAccessible is None
            or _QuietComboBoxAccessible is None):
        return
    try:
        QAccessible.installFactory(_quiet_combo_factory)
        _combo_factory_installed = True
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 模块级小工具
# ---------------------------------------------------------------------------

def _safe_focus(widget: QWidget) -> None:
    try:
        import shiboken6

        if shiboken6.isValid(widget) and widget.isVisible():
            widget.setFocus(Qt.FocusReason.TabFocusReason)
    except Exception:
        pass


def announce_static(text: str) -> None:
    AccessibilityBridge.announce_static(text)


def install_accessibility(app: QApplication) -> AccessibilityBridge:
    """在 ``QApplication`` 创建后、任何控件创建前调用一次。

    用法（run.py）::

        app = QApplication(sys.argv)
        app._a11y_bridge = install_accessibility(app)

    返回值必须挂到 app 上保活，否则事件过滤器会被 GC 回收。
    """
    return AccessibilityBridge(app)
