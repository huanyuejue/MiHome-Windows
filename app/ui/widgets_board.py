# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""桌面挂件 Widgets Board：常驻桌面的设备控制面板。

与托盘快捷窗口（TrayQuickWindow）是**并列的两个入口**，不是替代关系：
托盘是临时呼出、用完即走，Board 是常驻抬眼即用。两者共用 PowerButton
与 SiColors 主题色，但设备清单各自独立（board.json / tray.json）。

窗口行为层（Board 独有，托盘窗口没有的部分）：
    常驻置顶   无边框 + WindowStaysOnTopHint + Tool，不占任务栏
    自由摆放   顶部拖拽区拖动窗口，位置与尺寸落盘恢复
    透明度     0.30–1.00 无级调节，鼠标悬停时自动恢复不透明便于读取
    一键收起   折叠为一条 32px 细横条，每台设备一个状态点，点一下展开
    列数       一行放 1–6 个设备，改动即时重排

卡片只承载「电源开关」这一条最快路径（与托盘快捷行同款）：名称 +
房间/温湿度副标题 + 圆形电源钮；点击卡片本体打开完整详情面板。
"""

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta

from app.core import board_store
from app.core.jobs import PRIORITY_CONTROL, JobExecutor
from app.core.models import DeviceInfo
from app.core.service import MijiaService
from app.ui.power_button import PowerButton
from app.ui.si_theme import SiColors

# 卡片与窗口的尺寸基准
_CARD_MIN_WIDTH = 168
_CARD_HEIGHT = 56
_COLLAPSE_HEIGHT = 32
_MARGIN = 10          # 根面板四周留白
_GRID_SPACING = 6
_DRAG_HANDLE_HEIGHT = 22
# 判定"拖动"而非"点击"的位移阈值（像素）：太小会误判抖动，太大拖不动
_DRAG_THRESHOLD = 5

# 挂件自称：桌面上只有这一块面板，标题要让人一眼知道它是谁家的
# 换产品名只改这两行即可
_APP_LABEL = "米家"
_BOARD_TITLE = f"{_APP_LABEL} · 桌面挂件"
_BAR_TITLE = f"{_APP_LABEL}挂件"


def _board_btn_qss(palette: "_BoardPalette | None" = None) -> str:
    """Board 顶部工具按钮统一观感（22×22 圆角小按钮）。"""
    surface = palette.surface if palette is not None else SiColors.SURFACE
    hover = palette.btn_hover if palette is not None else SiColors.BTN_HOVER
    return (
        f"QPushButton {{ background: {surface}; border: none;"
        f" border-radius: 11px; }}"
        f"QPushButton:hover {{ background: {hover}; }}")


def _rgba(color: QColor, alpha: float) -> str:
    """QColor → Qt 样式表可用的 rgba() 字符串（alpha 用 0–255）。"""
    return (f"rgba({color.red()}, {color.green()}, {color.blue()},"
            f" {int(round(alpha * 255))})")


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """按比例混合两色（t=0 取 a，t=1 取 b）。"""
    t = min(max(t, 0.0), 1.0)
    return QColor(
        int(round(a.red() + (b.red() - a.red()) * t)),
        int(round(a.green() + (b.green() - a.green()) * t)),
        int(round(a.blue() + (b.blue() - a.blue()) * t)),
    )


class _BoardPalette:
    """挂件配色：跟随主题时直接取 SiColors，自定义底色时按明暗派生。

    派生规则只说一句话：**底色暗就把卡片/文字往白里提，底色亮就往黑里压**。
    这样用户选纯黑、墨蓝、米白都能正常读，不会出现黑底黑字。
    强调色（电源开态、选中态）始终沿用主题绿，两套底色下都够亮。
    """

    def __init__(self, bg: str | None):
        if bg is None:
            self.window_bg = SiColors.WINDOW_BG
            self.line = SiColors.LINE
            self.card = SiColors.CARD
            self.card_hover = SiColors.CARD_HOVER
            self.card_border_hover = SiColors.CARD_BORDER_HOVER
            self.surface = SiColors.SURFACE
            self.btn_hover = SiColors.BTN_HOVER
            self.text_primary = SiColors.TEXT_PRIMARY
            self.text_secondary = SiColors.TEXT_SECONDARY
            self.text_muted = SiColors.TEXT_MUTED
            self.scrollbar = SiColors.SCROLLBAR
            self.scrollbar_hover = SiColors.SCROLLBAR_HOVER
            self.offline_text = SiColors.OFFLINE_TEXT
            self.offline_sub = SiColors.OFFLINE_SUB
            self.state_unknown_border = SiColors.STATE_UNKNOWN_BORDER
            self.is_custom = False
            return

        base = QColor(bg)
        # lightnessF 比 HSV 的 V 更稳：纯黑 V=0 时 lighter() 提不亮，必须手动混色
        dark = base.lightnessF() < 0.5
        self.is_custom = True
        self.window_bg = base.name()
        if dark:
            white = QColor(255, 255, 255)
            self.card = _mix(base, white, 0.10).name()
            self.card_hover = _mix(base, white, 0.16).name()
            self.card_border_hover = _mix(base, white, 0.30).name()
            self.surface = _mix(base, white, 0.14).name()
            self.btn_hover = _mix(base, white, 0.24).name()
            self.line = _rgba(white, 0.14)
            self.scrollbar = _rgba(white, 0.22)
            self.scrollbar_hover = _rgba(white, 0.38)
            self.text_primary = "#f2f3f5"
            self.text_secondary = _rgba(white, 0.66)
            self.text_muted = _rgba(white, 0.45)
            self.offline_text = _rgba(white, 0.42)
            self.offline_sub = _rgba(white, 0.28)
            self.state_unknown_border = _rgba(white, 0.35)
        else:
            black = QColor(0, 0, 0)
            self.card = _mix(base, black, 0.05).name()
            self.card_hover = _mix(base, black, 0.09).name()
            self.card_border_hover = _mix(base, black, 0.20).name()
            self.surface = _mix(base, black, 0.07).name()
            self.btn_hover = _mix(base, black, 0.13).name()
            self.line = _rgba(black, 0.12)
            self.scrollbar = _rgba(black, 0.22)
            self.scrollbar_hover = _rgba(black, 0.36)
            self.text_primary = "#1a1b1f"
            self.text_secondary = _rgba(black, 0.66)
            self.text_muted = _rgba(black, 0.48)
            self.offline_text = _rgba(black, 0.38)
            self.offline_sub = _rgba(black, 0.26)
            self.state_unknown_border = _rgba(black, 0.30)


class WidgetsBoard(QWidget):
    """常驻桌面的设备挂件。"""

    manage_requested = Signal()
    open_device_requested = Signal(str)  # did
    # 挂件内直接开关成功后回传主窗口：让主界面卡片与 _known_power 保持一致，
    # 否则轮询会拿旧记忆覆盖刚点的结果
    power_changed = Signal(str, object)  # (did, bool)

    def __init__(self, service: MijiaService, jobs: JobExecutor, parent=None):
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        self._devices: list[DeviceInfo] = []
        self._known_power: dict[str, bool | None] = {}
        self._metrics: dict[str, str | None] = {}
        # did -> 本地操作版本号：丢弃"操作前发出、操作后回来"的旧轮询结果
        self._power_gen: dict[str, int] = {}
        self._dids: list[str] = board_store.load_devices()
        self._columns: int = board_store.get_columns()
        self._opacity: float = board_store.get_opacity()
        # 背景色：None = 跟随主题；否则是用户挑的固定色
        self._bg: str | None = board_store.get_bg()
        # 置顶：默认关。常驻置顶会挡住别的窗口（用户明确反馈过）
        self._topmost: bool = board_store.get_topmost()
        self._collapsed: bool = False
        # 悬停恢复不透明：拖动滑块时鼠标在窗口内，若强制 1.0 会看不到预览
        self._force_opaque = False
        # 管理对话框打开期间为 True：暂停悬停恢复，让透明度预览可见
        self.setProperty("previewing", False)

        self.setWindowFlags(self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("米家桌面挂件")

        # 展开态几何：折叠时横条贴原位，收起高度后仍能回到原位置尺寸
        self._expanded_geom: tuple[int, int, int, int] | None = None
        # 三块可拖区域的统一按下状态：窗口原点与"是否已越过拖拽阈值"
        self._press_origin: QPoint | None = None
        self._press_moved = False
        # 位置/尺寸落盘防抖：拖动过程中每帧都写会拖慢窗口跟随
        # （_rebuild 也会用，必须先于首次 _rebuild 建好）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_geometry)

        # 配色表：跟随主题或用户挑的固定色（见 _BoardPalette）
        self._palette = _BoardPalette(self._bg)
        self._build_ui()
        self._restore_geometry()
        self._apply_opacity()
        self._rebuild()

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        self._root = QFrame(self)
        self._root.setObjectName("boardPanel")
        self._root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._root.setStyleSheet(
            f"QFrame#boardPanel {{ background: {self._palette.window_bg};"
            f" border: 1px solid {self._palette.line}; border-radius: 14px; }}")
        root_lay = QVBoxLayout(self._root)
        root_lay.setContentsMargins(_MARGIN, 8, _MARGIN, _MARGIN)
        root_lay.setSpacing(6)

        # 顶部：拖拽把手 + 设备数 + 三个小按钮
        handle = QFrame(self._root)
        handle.setObjectName("boardHandle")
        handle.setFixedHeight(_DRAG_HANDLE_HEIGHT)
        handle.setStyleSheet("QFrame#boardHandle { background: transparent; }")
        handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self._handle = handle
        handle.installEventFilter(self)
        # 面板空白处也可拖：只有 22px 把手太细，用户容易"抓不住"
        self._root.installEventFilter(self)
        hl = QHBoxLayout(handle)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(4)

        self._title = QLabel(_BOARD_TITLE)
        self._title.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        self._title.setStyleSheet(
            f"color: {self._palette.text_secondary}; background: transparent;")
        self._title.setToolTip(f"{_BOARD_TITLE}（单击收起为细横条，拖动可移动）")
        hl.addWidget(self._title)
        hl.addStretch(1)

        self._tool_buttons: list[QPushButton] = []
        # 收起：向下箭头（折叠成横条）
        self._collapse_btn = self._make_tool_button(
            "mdi.chevron-down", "收起为细横条", self._collapse)
        hl.addWidget(self._collapse_btn)
        # 管理：加号
        add_btn = self._make_tool_button(
            "mdi.plus", "管理挂件设备", self.manage_requested.emit)
        hl.addWidget(add_btn)
        # 隐藏：叉号（收起窗口但保留状态，托盘菜单可再显示）
        hide_btn = self._make_tool_button("mdi.close", "隐藏挂件", self.hide_by_user)
        hl.addWidget(hide_btn)
        root_lay.addWidget(handle)

        # 卡片滚动区：设备多于一屏时纵向滚动
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0 2px 0 0; }"
            f"QScrollBar::handle:vertical {{ background: {self._palette.scrollbar};"
            " border-radius: 3px; min-height: 30px; }"
            f"QScrollBar::handle:vertical:hover {{ background: {self._palette.scrollbar_hover}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }")
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(self._host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(0)
        self._scroll.setWidget(self._host)
        root_lay.addWidget(self._scroll, stretch=1)

        self._empty = QLabel("还没有添加设备\n点右上角 + 选择")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {self._palette.text_secondary}; background: transparent; font-size: 9pt;")
        self._empty.hide()
        root_lay.addWidget(self._empty)

        # 折叠态内容：一条横条，每台设备一个状态点
        self._bar = QFrame(self)
        self._bar.setObjectName("boardBar")
        self._bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._bar.setStyleSheet(
            f"QFrame#boardBar {{ background: {self._palette.window_bg};"
            f" border: 1px solid {self._palette.line}; border-radius: 14px; }}")
        self._bar.setFixedHeight(_COLLAPSE_HEIGHT)
        # 折叠态整条可拖拽移动；OpenHand 提示"能抓起来"
        self._bar.setCursor(Qt.CursorShape.OpenHandCursor)
        # 折叠横条同时承担两件事：拖动移动窗口、单击展开。
        # 靠位移阈值区分（见 eventFilter），否则一按就展开、永远拖不动
        self._bar.installEventFilter(self)
        bar_lay = QHBoxLayout(self._bar)
        bar_lay.setContentsMargins(10, 0, 10, 0)
        bar_lay.setSpacing(6)
        # 左侧抓握纹，明确告诉用户这条东西可以拖
        self._bar_grip = QLabel("⠿")
        self._bar_grip.setFixedWidth(10)
        self._bar_grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar_grip.setStyleSheet(
            f"color: {self._palette.text_muted}; background: transparent; font-size: 9pt;")
        self._bar_grip.setToolTip("拖动可移动位置，单击展开")
        bar_lay.addWidget(self._bar_grip)
        # 折叠态标题：只剩一条细横条时，光有状态点看不出这是谁家的挂件
        self._bar_title = QLabel(_BAR_TITLE)
        self._bar_title.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
        self._bar_title.setStyleSheet(
            f"color: {self._palette.text_secondary}; background: transparent; font-size: 8pt;")
        self._bar_title.setToolTip(f"{_BOARD_TITLE}（拖动可移动，单击展开）")
        bar_lay.addWidget(self._bar_title)
        self._dot_host = QWidget()
        self._dot_host.setStyleSheet("background: transparent;")
        self._dot_lay = QHBoxLayout(self._dot_host)
        self._dot_lay.setContentsMargins(0, 0, 0, 0)
        self._dot_lay.setSpacing(5)
        self._dot_lay.addStretch(1)
        bar_lay.addWidget(self._dot_host)
        # 展开按钮：向上箭头
        expand = self._make_tool_button('mdi.chevron-up', "展开挂件", self._expand)
        bar_lay.addWidget(expand)
        self._bar.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._root)
        outer.addWidget(self._bar)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # 上面各控件的初始样式用主题色兜底，这里统一按当前配色表覆盖一遍：
        # 用户若设过自定义背景色，构造完就是正确颜色，不会闪一下主题色
        self._apply_styles()

    def _make_tool_button(self, icon: str, tip: str, on_click) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tip)
        btn.setIcon(qta.icon(icon, color=self._palette.text_primary))
        btn.setIconSize(QSize(16, 16))
        btn.setStyleSheet(_board_btn_qss())
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.clicked.connect(on_click)
        # 图标色随主题变化，记住图标名供 retheme 重画
        btn._icon_name = icon
        self._tool_buttons.append(btn)
        return btn

    # ---------- 几何与透明度 ----------

    def _restore_geometry(self) -> None:
        geom = board_store.get_geometry()
        if geom is None:
            self._expanded_geom = self._default_geometry()
        else:
            self._expanded_geom = geom
        self._apply_expanded_geometry()
        if board_store.get_collapsed():
            self._collapsed = True
            self._swap_to_collapsed()

    def _default_geometry(self) -> tuple[int, int, int, int]:
        """首次启动：贴在主屏右上角（避开任务栏与桌面图标栏）。"""
        screen = QGuiApplication.primaryScreen()
        width = self._board_width()
        height = self._board_height()
        if screen is None:
            return 80, 80, width, height
        geo = screen.availableGeometry()
        return max(geo.left(), geo.right() - width - 24), \
            max(geo.top(), geo.top() + 24), width, height

    def _apply_expanded_geometry(self) -> None:
        if self._expanded_geom is not None:
            self.setGeometry(*self._expanded_geom)
        self.resize(self._board_width(), self._board_height())

    def _board_width(self) -> int:
        """按列数算卡片区总宽：卡片下限宽 + 间距 + 边距。"""
        inner = _CARD_MIN_WIDTH * self._columns + _GRID_SPACING * (self._columns - 1)
        return inner + _MARGIN * 2

    def _board_height(self) -> int:
        """按行数算窗口高度（与真实布局边距一致，否则会出现滚动条）。

        根面板：上边距 8 + 拖拽把手 22 + 把手与卡片区间距 6 + 下边距 10
        = 46；每行卡片 56，行间距 6（末行后无间距）。额外留 4px 余量：
        滚动区视口与内容恰好等高的 1px 舍入误差也会让滚动条冒出来。
        设备多到超出屏幕可用高度时封顶，由滚动条接管。
        """
        rows = max(1, -(-len(self._dids) // max(1, self._columns)))
        body = rows * _CARD_HEIGHT + (rows - 1) * _GRID_SPACING
        height = 46 + body + 4
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            cap = screen.availableGeometry().height() - 80
            height = min(height, max(160, cap))
        return height

    def _save_geometry(self) -> None:
        if self._expanded_geom is not None:
            board_store.set_geometry(*self._expanded_geom)

    # ---------- 背景色 / 置顶 ----------

    @property
    def palette(self) -> "_BoardPalette":
        return self._palette

    def set_bg(self, color: str | None, *, persist: bool = True) -> None:
        """设置背景色：None = 跟随主题，'#rrggbb' = 固定色。

        persist=False 给管理对话框做实时预览用（对话框自己负责落盘）。
        """
        if color is not None and not isinstance(color, str):
            return
        self._bg = color
        if persist:
            board_store.set_bg(color)
        self._palette = _BoardPalette(self._bg)
        self._apply_styles()

    def bg(self) -> str | None:
        return self._bg

    def _window_flags(self):
        """窗口标志：无边框 + Tool（不占任务栏），置顶按开关决定。"""
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def set_topmost(self, topmost: bool, *, persist: bool = True) -> None:
        """切换置顶。默认关：与普通窗口一样会被别的窗口盖住。

        注意 Qt 在 Windows 上改窗口标志会**销毁并重建原生窗口**：窗口被隐藏、
        位置尺寸回到默认值。因此改完必须重新 show 并还原几何，否则挂件会
        跳到屏幕左上角。
        """
        topmost = bool(topmost)
        if topmost == self._topmost:
            if persist:
                board_store.set_topmost(topmost)
            return
        self._topmost = topmost
        if persist:
            board_store.set_topmost(topmost)
        self._apply_window_flags()

    def _apply_window_flags(self) -> None:
        visible = self.isVisible()
        geo = self.geometry()
        self.setWindowFlags(self._window_flags())
        # setWindowFlags 后窗口不可见；即使原来可见也要重新 show
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if visible:
            self.show()
            self.setGeometry(geo)
            # 折叠态高度只有 32px，show 之后重新贴回原尺寸
            if not self._collapsed:
                self._apply_expanded_geometry()
            self._apply_opacity()

    def is_topmost(self) -> bool:
        return self._topmost

    def _apply_styles(self) -> None:
        """按当前配色表重设所有内联样式（主题切换与改背景色共用）。"""
        p = self._palette
        self._root.setStyleSheet(
            f"QFrame#boardPanel {{ background: {p.window_bg};"
            f" border: 1px solid {p.line}; border-radius: 14px; }}")
        self._bar.setStyleSheet(
            f"QFrame#boardBar {{ background: {p.window_bg};"
            f" border: 1px solid {p.line}; border-radius: 14px; }}")
        self._title.setStyleSheet(
            f"color: {p.text_secondary}; background: transparent;")
        self._empty.setStyleSheet(
            f"color: {p.text_secondary}; background: transparent; font-size: 9pt;")
        grip = getattr(self, "_bar_grip", None)
        if grip is not None:
            grip.setStyleSheet(
                f"color: {p.text_muted}; background: transparent; font-size: 9pt;")
        bar_title = getattr(self, "_bar_title", None)
        if bar_title is not None:
            bar_title.setStyleSheet(
                f"color: {p.text_secondary}; background: transparent;"
                " font-size: 8pt;")
        scroll = getattr(self, "_scroll", None)
        if scroll is not None:
            scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
                "QScrollBar:vertical { background: transparent; width: 6px;"
                " margin: 0 2px 0 0; }"
                f"QScrollBar::handle:vertical {{ background: {p.scrollbar};"
                " border-radius: 3px; min-height: 30px; }"
                f"QScrollBar::handle:vertical:hover {{ background: {p.scrollbar_hover}; }}"
                "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
                "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
                " { background: none; }")
        for btn in self._tool_buttons:
            btn.setStyleSheet(_board_btn_qss(p))
            btn.setIcon(qta.icon(btn._icon_name, color=p.text_primary))
        # 卡片与状态点
        grid = getattr(self, "_grid", None)
        if grid is not None:
            for i in range(grid.count()):
                card = grid.itemAt(i).widget()
                if card is not None:
                    card.retheme()
        if self._collapsed:
            self._sync_dots()

    def _apply_opacity(self) -> None:
        # 管理对话框打开时预览透明度：暂停「悬停恢复不透明」，
        # 否则鼠标移到对话框上经过挂件会把预览值顶回 1.0
        previewing = self.property("previewing")
        opaque = self._force_opaque and not previewing
        self.setWindowOpacity(1.0 if opaque else self._opacity)

    def set_opacity(self, value: float, *, persist: bool = True) -> None:
        """设定透明度；persist=True 时同步落盘（滑块拖动过程每步都落盘）。"""
        self._opacity = _clamp_opacity(value)
        if persist:
            board_store.set_opacity(self._opacity)
        self._apply_opacity()

    def opacity(self) -> float:
        return self._opacity

    def set_previewing(self, previewing: bool) -> None:
        """管理对话框打开/关闭时调用：预览期间暂停「悬停恢复不透明」。"""
        self.setProperty("previewing", bool(previewing))
        self._apply_opacity()

    def enterEvent(self, event) -> None:  # noqa: N802
        # 鼠标进入即恢复不透明：半透明时文字难读、看不清开关状态
        self._force_opaque = True
        self._apply_opacity()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._force_opaque = False
        self._apply_opacity()
        super().leaveEvent(event)

    # ---------- 折叠 / 展开 ----------

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def _collapse(self) -> None:
        if self._collapsed:
            return
        self._swap_to_collapsed()
        board_store.set_collapsed(True)

    def _expand(self) -> None:
        if not self._collapsed:
            return
        self._swap_to_expanded()
        board_store.set_collapsed(False)

    def toggle_collapse(self) -> None:
        """托盘菜单「收起挂件」用：折叠时展开，已折叠直接切换。"""
        if self._collapsed:
            self._expand()
        else:
            self._collapse()

    def _swap_to_collapsed(self) -> None:
        geo = self.geometry()
        self._expanded_geom = (geo.x(), geo.y(), self._board_width(), self._board_height())
        self._root.hide()
        self._bar.show()
        self._collapsed = True
        # 横条宽 = 左右边距 20 + 抓握纹 10 + 标题 + 间距 + 每台设备点 14 + 展开按钮 22
        dots = len(self._dids) * 14 + max(0, len(self._dids) - 1) * 5
        title_w = self._bar_title.sizeHint().width()
        self.resize(max(150, 20 + 10 + title_w + 6 + dots + 6 + 22), _COLLAPSE_HEIGHT)
        self._sync_dots()
        # 折叠/展开不淡变透明度：windowOpacity 是用户设定值，动画会
        # 在中途覆盖它并在结束时跳回，观感是一次闪烁
        self._apply_opacity()

    def _swap_to_expanded(self) -> None:
        self._bar.hide()
        self._root.show()
        self._collapsed = False
        self._apply_expanded_geometry()
        self._apply_opacity()

    def _sync_dots(self) -> None:
        """重建折叠横条上的状态点（绿=开，灰=关，描边=未知，暗=离线）。"""
        import shiboken6

        while self._dot_lay.count():
            item = self._dot_lay.takeAt(0)
            w = item.widget()
            if w is not None and shiboken6.isValid(w):
                w.deleteLater()
        lookup = {d.did: d for d in self._devices}
        for did in self._dids:
            dev = lookup.get(did)
            if dev is None:
                continue
            dot = QLabel()
            dot.setFixedSize(9, 9)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if not dev.online:
                color, border = "transparent", self._palette.offline_sub
            else:
                state = self._known_power.get(did)
                if state is True:
                    color, border = SiColors.THEME, SiColors.THEME
                elif state is False:
                    color, border = SiColors.STATE_OFF, SiColors.STATE_OFF
                else:
                    color, border = "transparent", self._palette.state_unknown_border
            dot.setStyleSheet(
                f"QLabel {{ background: {color}; border: 1px solid {border};"
                " border-radius: 4px; }")
            dot.setToolTip(f"{dev.name}（{dev.room_name}）")
            self._dot_lay.addWidget(dot)
        self._dot_lay.addStretch(1)

    # ---------- 数据 ----------

    def set_devices(self, devices: list[DeviceInfo],
                    known_power: dict[str, bool | None]) -> None:
        """由主窗口在设备列表刷新 / 开关状态变化后推送。"""
        self._devices = list(devices)
        self._known_power = dict(known_power)
        self._dids = board_store.load_devices()
        if self._collapsed:
            self._sync_dots()
        else:
            self._rebuild()

    def set_metrics(self, metrics: dict[str, str | None]) -> None:
        """主窗口温湿度读数同步：就地刷新可见卡片副标题。

        读数始终记进内存（隐藏时也要记），下次显示由 showEvent 补回，
        否则挂件隐藏期间更新的读数会一直显示旧值。
        """
        import shiboken6

        if not shiboken6.isValid(self):
            return
        self._metrics = dict(metrics)
        if self._collapsed or not self.isVisible():
            return
        self._refresh_subtitles()

    def _refresh_subtitles(self) -> None:
        """按当前设备/读数重算所有卡片副标题（房间 | 温湿度）。"""
        lookup = {d.did: d for d in self._devices}
        for did, card in getattr(self, "_cards", {}).items():
            dev = lookup.get(did)
            if dev is None:
                continue
            metrics = self._metrics.get(did)
            card.set_subtitle(
                f"{dev.room_name} · 离线" if not dev.online
                else (f"{dev.room_name} | {metrics}" if metrics else dev.room_name))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 记住"用户让挂件开着"：重启后据此恢复（见 board_store.get_visible）
        board_store.set_visible(True)
        # 显示时把隐藏期间累积的读数补回卡片
        self._refresh_subtitles()

    def hide_by_user(self) -> None:
        """用户主动隐藏（点 ×）。

        只有用户意图才改 `visible`：应用退出、启动时不满足条件而隐藏，
        都走普通 `hide()`，不会覆盖"上次是开着的"这个记录。
        """
        board_store.set_visible(False)
        self.hide()

    def refresh_power(self, dids: list[str]) -> None:
        """补拉尚未确认开关能力的设备状态（与托盘窗口同一套串行队列）。"""
        dids = [d for d in dids if d and self._known_power.get(d) is None]
        if not dids:
            return
        # 快照版本号：回来时若用户已经点过这台设备，丢弃这条旧结果
        snapshot = dict(self._power_gen)
        self._jobs.submit(
            lambda: self._service.power_states(dids),
            on_success=lambda states, snap=snapshot: self._apply_power(states, snap),
            on_error=lambda _: None,
        )

    def _apply_power(self, states: dict[str, bool | None],
                     snapshot: dict[str, int] | None = None) -> None:
        import shiboken6

        if not shiboken6.isValid(self):
            return
        states = {d: s for d, s in states.items()
                  if snapshot is None
                  or snapshot.get(d, 0) == self._power_gen.get(d, 0)}
        self._known_power.update(states)
        if self._collapsed:
            self._sync_dots()
            return
        for did, state in states.items():
            card = getattr(self, "_cards", {}).get(did)
            if card is not None and shiboken6.isValid(card):
                card.set_power_state(state)

    # ---------- 卡片网格 ----------

    def _rebuild(self) -> None:
        self._destroy_grid()
        # 每次重建都读最新清单：管理对话框保存或改列数后，内存里的
        # _dids 可能是旧的，只有读盘才不会用过期清单排版
        self._dids = board_store.load_devices()
        lookup = {d.did: d for d in self._devices}
        cards = [lookup[d] for d in self._dids if d in lookup]
        # 标题带产品名：桌面上可能同时开着一堆挂件，得能认出这是谁家的
        self._title.setText(f"{_BOARD_TITLE} · {len(cards)}")
        if not cards:
            self._empty.show()
            self._scroll.hide()
            return
        self._empty.hide()
        self._scroll.show()
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_GRID_SPACING)
        grid.setVerticalSpacing(_GRID_SPACING)
        for c in range(self._columns):
            grid.setColumnStretch(c, 1)
        self._cards = {}
        for idx, dev in enumerate(cards):
            card = _BoardCard(self, dev, self._known_power.get(dev.did))
            self._cards[dev.did] = card
            grid.addWidget(card, idx // self._columns, idx % self._columns)
        self._grid = grid
        self._list_lay.addLayout(grid)
        self._list_lay.addStretch(1)
        if not self._collapsed:
            # 列数/设备数变化后重算尺寸并记住展开态几何（拖动位置保持不变）
            pos = self.geometry().topLeft()
            self._expanded_geom = (pos.x(), pos.y(),
                                   self._board_width(), self._board_height())
            self.resize(self._board_width(), self._board_height())
            self._save_timer.start()
        online = [d.did for d in cards if d.online and self._known_power.get(d.did) is None]
        if online:
            self.refresh_power(online)

    def _destroy_grid(self) -> None:
        grid = getattr(self, "_grid", None)
        if grid is None:
            return
        for i in range(self._list_lay.count()):
            item = self._list_lay.itemAt(i)
            if item and item.layout() is grid:
                self._list_lay.takeAt(i)
                break
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        grid.deleteLater()
        self._grid = None
        self._cards = {}
        # 移除多余 stretch，避免反复重建后列表底部空出间距
        for i in range(self._list_lay.count() - 1, -1, -1):
            item = self._list_lay.itemAt(i)
            if item and item.spacerItem():
                self._list_lay.takeAt(i)
                break

    def set_columns(self, value: int) -> None:
        """设定一行放多少个设备（1–6），越界值落库时钳制。"""
        self._columns = min(max(int(value), 1), 6)
        board_store.set_columns(self._columns)
        if self._collapsed:
            return
        self._rebuild()

    def columns(self) -> int:
        return self._columns

    # ---------- 交互 ----------

    def _toggle_power(self, did: str, btn) -> None:
        """点开关：已知状态时直接写反值（1 次往返）+ 乐观翻转按钮。"""
        current = self._known_power.get(did)
        if current is None:
            # 状态未知（首次探测还没回来）：只能让服务端读一次再写。
            # 仍按用户操作优先级排队，避免排在轮询后面
            btn.set_busy(True)
            self._jobs.submit(
                lambda: self._service.toggle_power(did),
                on_success=lambda ns, d=did, b=btn: self._on_power_done(d, ns, b),
                on_error=lambda e, b=btn: b.set_busy(False),
                priority=PRIORITY_CONTROL,
            )
            return
        target = not current
        # 乐观 UI：立刻翻转，用户无需等网络；失败再回滚
        self._bump_power_gen(did)
        self._known_power[did] = target
        btn.set_state(target)
        btn.set_busy(True)
        self._jobs.submit(
            lambda: (self._service.set_power_state(did, target), target)[1],
            on_success=lambda ns, d=did, b=btn: self._on_power_done(d, ns, b),
            on_error=lambda e, d=did, b=btn, c=current: self._on_power_failed(d, c, b, e),
            priority=PRIORITY_CONTROL,
        )

    def _on_power_done(self, did: str, new_state: bool, btn) -> None:
        import shiboken6

        self._bump_power_gen(did)
        self._known_power[did] = new_state
        if shiboken6.isValid(btn):
            btn.set_state(new_state)
            btn.set_busy(False)
        self.power_changed.emit(did, new_state)
        if self._collapsed:
            self._sync_dots()

    def _on_power_failed(self, did: str, revert_to: bool, btn, error: Exception) -> None:
        """写入失败：把乐观翻转的按钮回滚，并提示一次原因。"""
        import shiboken6

        self._bump_power_gen(did)
        self._known_power[did] = revert_to
        if shiboken6.isValid(btn):
            btn.set_state(revert_to)
            btn.set_busy(False)
        self.power_changed.emit(did, revert_to)
        from app.ui.toast import Toast

        Toast.info(self, f"操作失败：{error}", 4000)

    def _bump_power_gen(self, did: str) -> None:
        """本地操作过这台设备：让在途的旧轮询结果作废。"""
        self._power_gen[did] = self._power_gen.get(did, 0) + 1

    def apply_power_states(self, states: dict[str, bool | None]) -> None:
        """轮询读到新状态后就地更新卡片按钮，不整窗重建。"""
        import shiboken6

        if not shiboken6.isValid(self):
            return
        self._known_power.update(states)
        if self._collapsed:
            self._sync_dots()
            return
        for did, state in states.items():
            card = getattr(self, "_cards", {}).get(did)
            if card is not None and state is not None and shiboken6.isValid(card):
                card.set_power_state(state)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """标题行 / 面板空白 / 折叠横条的统一鼠标处理。

        三块区域都能拖动窗口，差别只在"没拖动的那一下"：

            标题行   → 收起为细横条
            面板空白 → 什么都不做
            折叠横条 → 展开

        拖动与点击共用左键，靠位移阈值区分：按下记原点，移动超过
        _DRAG_THRESHOLD 才算拖拽（此后松手不触发点击动作），否则松手
        按区域执行各自动作。按下时消费事件，保证后续 move/release
        还能送回这里；卡片与按钮是子控件，会自己消费点击不受影响。

        **必须容错**：installEventFilter 之后 Qt 会立刻投递事件（构造期
        就在跑），此时 _bar / _handle 可能还没建好。Python 异常若从
        Qt 虚函数里抛出，会污染后续 Qt 调用——表现为下一个
        QHBoxLayout(...) 莫名返回 NULL。
        """
        try:
            etype = event.type()
            handle = getattr(self, "_handle", None)
            bar = getattr(self, "_bar", None)
            root = getattr(self, "_root", None)
            is_handle = handle is not None and obj is handle
            is_bar = bar is not None and obj is bar
            is_root = root is not None and obj is root
            if not (is_handle or is_bar or is_root):
                return super().eventFilter(obj, event)

            if etype == QEvent.Type.MouseButtonPress:
                if event.button() != Qt.MouseButton.LeftButton:
                    return False
                self._press_origin = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft())
                self._press_moved = False
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True

            if etype == QEvent.Type.MouseMove:
                if (self._press_origin is not None
                        and event.buttons() & Qt.MouseButton.LeftButton):
                    target = event.globalPosition().toPoint() - self._press_origin
                    # 越过阈值才算拖拽：轻微抖动不应把"点击"变成"拖动"
                    if not self._press_moved:
                        if (target - self.pos()).manhattanLength() < _DRAG_THRESHOLD:
                            return True
                        self._press_moved = True
                    self.move(self._clamp_to_screen(target.x(), target.y()))
                    self._remember_geometry()
                elif self._press_origin is not None:
                    # 松手事件丢在窗口外（拖太快）时在这里收尾，避免光标卡住
                    self._press_origin = None
                    self.unsetCursor()
                return True

            if etype == QEvent.Type.MouseButtonRelease:
                if self._press_origin is None:
                    return True
                self._press_origin = None
                self.unsetCursor()
                if self._press_moved:
                    # 拖过：只落盘位置，不触发点击动作
                    self._press_moved = False
                    self._save_geometry()
                    return True
                self._press_moved = False
                # 没拖动 → 视为单击
                if is_bar:
                    self._expand()
                elif is_handle:
                    self._collapse()
                return True
        except Exception:
            # 事件过滤里的意外不得冒泡到 Qt（见上面的说明）
            return False
        return super().eventFilter(obj, event)

    def _clamp_to_screen(self, x: int, y: int) -> QPoint:
        """拖动时限制在工作区可见范围内，避免窗口被拖丢到屏幕外。"""
        screen = QGuiApplication.screenAt(QPoint(x + 40, y + 10)) \
            or QGuiApplication.primaryScreen()
        if screen is None:
            return QPoint(x, y)
        geo = screen.availableGeometry()
        x = min(max(x, geo.left() - self.width() + 60), geo.right() - 60)
        y = min(max(y, geo.top()), geo.bottom() - 20)
        return QPoint(x, y)

    def _remember_geometry(self) -> None:
        """记住展开态几何。

        折叠态拖动横条时只更新位置，宽高仍用展开态的——否则展开后
        会变成细横条的尺寸；这样"折叠着挪个位置再展开"也能落到新地方。
        """
        geo = self.geometry()
        if self._collapsed:
            self._expanded_geom = (geo.x(), geo.y(),
                                   self._board_width(), self._board_height())
            return
        self._expanded_geom = (geo.x(), geo.y(), geo.width(), geo.height())

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._remember_geometry()
        self._save_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._collapsed:
            self._remember_geometry()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._save_geometry()
        super().hideEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setObjectName("appMenu")
        menu.addAction("管理挂件设备…").triggered.connect(self.manage_requested.emit)
        collapse_text = "展开挂件" if self._collapsed else "收起为细横条"
        menu.addAction(collapse_text).triggered.connect(self.toggle_collapse)
        menu.addSeparator()
        menu.addAction("刷新设备").triggered.connect(
            lambda: self.refresh_power(list(self._dids)))
        menu.addAction("隐藏挂件").triggered.connect(self.hide)
        menu.exec(self.mapToGlobal(pos))

    def retheme(self) -> None:
        """主题切换。

        自定义背景色的会话里主题切换不影响挂件底色（用户已经选了固定色），
        但仍然重算一遍配色表：以后新增强调色时不会漏刷。
        """
        self._palette = _BoardPalette(self._bg)
        self._apply_styles()


class _BoardCard(QFrame):
    """挂件里的单台设备卡：名称 + 副标题 + 圆形电源钮。

    与托盘快捷行同款结构与配色，按钮复用 PowerButton，保证两处观感一致。
    """

    def __init__(self, board: WidgetsBoard, dev: DeviceInfo, power: bool | None,
                 parent=None):
        super().__init__(parent)
        self._did = dev.did
        self._board = board
        self._online = dev.online
        self.setObjectName("boardCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._name = QLabel()
        self._name.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        # 水平 Ignored：布局可任意压缩标签，长名称不会把列撑宽
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._name.setText(dev.name)
        self._name.setToolTip(f"{dev.name} · {dev.room_name}")
        self._sub = QLabel()
        self._sub.setFont(QFont("Microsoft YaHei UI", 8))
        self._sub.setStyleSheet(
            f"color: {self._board.palette.text_secondary}; background: transparent;"
            " font-size: 8pt;")
        self._sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._sub.setText(self._sub_text(dev, board._metrics.get(dev.did)))
        text_col.addWidget(self._name)
        text_col.addWidget(self._sub)
        lay.addLayout(text_col, stretch=1)

        self.retheme()
        self._layout = lay
        self._power_btn = self._make_power_btn(power)

    def _make_power_btn(self, power: bool | None):
        """电源钮在探测确认设备有可写开关属性后才创建并插入布局。

        与主窗口 DeviceCard / 托盘快捷行同一时序：列表接口不带开关
        信息，先探测后出现，避免无开关设备留一个永远点不动的按钮。
        """
        if power is None:
            return None
        btn = PowerButton(28, icon_size=22)
        btn.set_state(power)
        btn.set_online(self._online)
        btn.clicked.connect(
            lambda _checked=False, d=self._did, b=btn: self._board._toggle_power(d, b))
        self._layout.addWidget(btn)
        return btn

    @staticmethod
    def _sub_text(dev: DeviceInfo, metrics: str | None) -> str:
        if not dev.online:
            return f"{dev.room_name} · 离线"
        return f"{dev.room_name} | {metrics}" if metrics else dev.room_name

    def set_subtitle(self, text: str) -> None:
        self._sub.setText(text)

    def set_power_state(self, state: bool | None) -> None:
        if self._power_btn is None:
            if state is None:
                return
            self._power_btn = self._make_power_btn(state)
            return
        self._power_btn.set_state(state)

    def retheme(self) -> None:
        # 配色由挂件统一给出：跟随主题或用户挑的固定背景色
        p = self._board.palette
        self.setStyleSheet(
            f"QFrame#boardCard {{ background: {p.card};"
            f" border: 1px solid {p.line}; border-radius: 10px; }}"
            f"QFrame#boardCard:hover {{ background: {p.card_hover};"
            f" border-color: {p.card_border_hover}; }}")
        color = p.text_primary if self._online else p.offline_text
        self._name.setStyleSheet(f"color: {color}; background: transparent;")
        sub_color = p.text_secondary if self._online else p.offline_sub
        self._sub.setStyleSheet(
            f"color: {sub_color}; background: transparent; font-size: 8pt;")
        # 构造期在电源钮创建之前也会调 retheme，故用 getattr 兜底
        power_btn = getattr(self, "_power_btn", None)
        if power_btn is not None:
            power_btn._apply()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # 电源钮自己消费点击，走到这里的是卡片空白区 → 打开完整详情
        if event.button() == Qt.MouseButton.LeftButton:
            self._board.open_device_requested.emit(self._did)
        super().mouseReleaseEvent(event)


def _clamp_opacity(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.92
    return round(min(max(v, 0.30), 1.00), 2)