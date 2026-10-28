# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""主窗口：米家 APP 风格的深色卡片视图。

顶部为家庭名与房间筛选 tab，中部是设备卡片网格（列数随窗口宽度
自适应）。卡片电源钮提供快速开关：设备列表接口不含开关状态，
首次点击先经详情接口确认能力，再读取当前值取反写入，成功后
把真实状态回填到卡片；点击卡片本体打开详情对话框。
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import sys
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEvent, QPropertyAnimation, QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from app.siui.components.button import SiToggleButtonRefactor

import qtawesome as qta

from app.core import cache as device_cache
from app.core import icon_store, name_override_store, schedule_store, settings_store
from app.core.jobs import PRIORITY_CONTROL, PRIORITY_IDLE, JobExecutor
from app.core.models import DeviceInfo, SceneInfo, is_speaker
from app.core.scheduler import Scheduler
from app.core.service import MijiaService, format_metric
from app.ui.board_manager import BoardManagerDialog
from app.ui.device_card import DeviceCard
from app.ui.device_dialog import DeviceDetailDialog
from app.ui.prop_widgets import FlowLayout
from app.ui.scene_card import SceneCard
from app.ui.schedule_dialog import ScheduleDialog
from app.ui import si_theme
from app.ui.si_theme import SiColors, themed_tab_button
from app.ui.toast import Toast
from app.ui.tray import TrayController, TrayManagerDialog
from app.ui.voice_fab import VoiceFab
from app.ui.widgets_board import WidgetsBoard
from app.ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

_ALL_ROOMS = "全屋"
_ALL_HOMES = "全部"
# 场景选项卡：位于「全屋」左侧的特殊房间值，选中后网格切换为场景视图
_SCENE_TAB = "场景"

# 外部（APP/语音/物理按键）改状态后卡片靠轮询跟随。
# 3 秒：一次整批读约 0.3~0.8 秒（批次已提到 20 台/次），加上间隔后
# 状态最迟约 4 秒内跟上；5 秒时代码体验上就是"按了墙上的开关，屏幕半天不动"。
_POLL_INTERVAL_MS = 3_000
_METRICS_INTERVAL_MS = 5 * 60 * 1000
# 定时任务的扫描间隔：10 秒足以落在目标分钟内，又不会空转太频繁
_SCHEDULE_INTERVAL_MS = 10_000


def _refresh_summary(total: int, added: int, removed: int) -> str:
    """刷新结果通知文案：有增减报增减，无变化明确告知。"""
    if not added and not removed:
        return f"设备列表无变化（共 {total} 台）"
    if added and removed:
        return f"本次刷新：新增 {added} 台，移除 {removed} 台"
    if added:
        return f"本次刷新：新增 {added} 台设备"
    return f"本次刷新：移除 {removed} 台设备"

# 无边框窗口的边缘拖拽热区宽度（像素）
_RESIZE_MARGIN = 8

# Windows 原生窗口消息与命中测试代码
_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_HTCLIENT = 1
_HTCAPTION = 2
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17


def _split_metrics(text: str) -> tuple[str, str]:
    """把 "22.2°C 67%" 拆成温度与湿度两段，缺失的一段返回空串。"""
    temp = ""
    humidity = ""
    for part in text.split():
        if part.endswith("°C"):
            temp = part
        elif part.endswith("%"):
            humidity = part
    return temp, humidity


class _IconButton(QPushButton):
    """顶栏纯图标按钮：无底色、无边框，悬停时图标提亮。

    顶栏右侧的刷新/设置/更多都是低频但需要常驻的入口，做成带框按钮
    会显得笨重；只留图标 + 悬停变色，既干净又能看出可点。
    """

    def __init__(self, icon_name: str, tip: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._hover = False
        self.setFixedSize(32, 32)
        self.setIconSize(QSize(24, 24))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; }")
        self._sync_icon()

    def _sync_icon(self) -> None:
        if not self.isEnabled():
            color = SiColors.TEXT_DISABLED
        else:
            # 浅色底上深灰图标显得重，默认再淡一档；深色底保持可辨识度
            light = si_theme.current_theme() == "light"
            if self._hover:
                color = SiColors.TEXT_SECONDARY if light else SiColors.TEXT_PRIMARY
            else:
                color = SiColors.TEXT_MUTED if light else SiColors.TEXT_SECONDARY
        self.setIcon(qta.icon(self._icon_name, color=color))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self._sync_icon()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self._sync_icon()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        # 刷新中禁用时图标要同步变灰
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_icon()
        super().changeEvent(event)

    def retheme(self) -> None:
        self._sync_icon()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._service = MijiaService()
        self._jobs = JobExecutor(self)
        # 第二条后台线程：只跑「纯 requests 的外部网页抓取」（产品页中文名、
        # 图标图片下载）。它们慢（实测单个产品页 2.4 秒、超时 30 秒）且与
        # 设备控制无关，留在主队列里会把用户点的开关堵在后面——这正是
        # 「点开关半天没反应」的根因。这些调用用的是模块级 requests
        # 而非上游共享 Session，分线程不违反线程安全前提。
        self._web_jobs = JobExecutor(self)

        from app import resource_path
        self.setWindowIcon(QIcon(str(resource_path("app/ui/icon.png"))))
        self.setWindowTitle("米家 - MiHome for Windows")
        # 保持原生窗口框架，通过拦截 WM_NCCALCSIZE 抹掉系统标题栏区域：
        # 拖动/双击最大化/最小化动画/Aero Snap/边缘缩放全部由系统原生处理
        self.setWindowFlags(Qt.Window)
        # 默认窗口尺寸在首次显示时按真实 DPR 应用（见 showEvent）：
        # 构造期窗口未创建，DPR 读数不可靠（曾导致默认尺寸放大一圈）
        self._default_size_applied = False
        self.setMinimumSize(760, 520)

        self._all_devices: list[DeviceInfo] = []
        self._cards: dict[str, DeviceCard] = {}
        # did -> 本地操作版本号：每次用户/定时改这台设备的开关就 +1，
        # 用来丢弃"操作之前发出、操作之后才回来"的轮询结果
        self._power_gen: dict[str, int] = {}
        self._homes: list[str] = []
        self._current_home: str = ""
        self._current_room = _ALL_ROOMS
        # 是否已经按设置里的默认标签初始化过房间选择
        self._room_initialized = False
        self._settings_dialog: SettingsDialog | None = None
        # did -> 开关状态记忆；None 表示确认无开关能力。
        # 探测结论是设备的固有属性，跨家庭/房间/刷新复用；
        # 串行队列 FIFO 保证探测与开关写入的回调顺序不会互相覆盖
        self._known_power: dict[str, bool | None] = {}
        # did -> 环境读数文案（温湿度），随轮询更新、随网格重建回填
        self._metrics: dict[str, str | None] = {}
        # did -> {属性名: 原始数值}，目前只存温湿度：顶部环境胶囊要用
        # 真实读数，不能解析被用户自定义过的副标题文案
        self._climate: dict[str, dict[str, float]] = {}
        # 耗材明细（全屋口径）：不足项数大于 0 时显示提示胶囊
        self._consumables: list[dict] = []
        self._consumable_low = 0
        # 刷新防重入：请求在途时忽略再次点击
        self._loading_devices = False
        # 启动自动检查更新每次进程只做一次，避免 start 被重复触发
        self._update_check_done = False
        # 轮询防重入：上一轮批量读取未返回时跳过本轮定时触发
        self._poll_in_flight = False
        # 网格重排防抖：拖动窗口会触发密集 resizeEvent，全部重建
        # 卡片代价太高，等布局稳定且列数变化时才重建
        self._grid_columns = 0
        # 托盘常驻内存优化：窗口隐藏期间不构建卡片网格（数十个
        # 卡片控件 + 样式约占 20-30MB），标记脏位、首次显示时构建
        self._grid_dirty = False
        # 隐藏无可控制功能/离线设备（设置开关，默认关）
        from app.core.settings_store import (
            get_hide_no_func_devices, get_hide_offline_devices)
        self._hide_no_func = get_hide_no_func_devices()
        self._hide_offline = get_hide_offline_devices()
        # 场景选项卡（设置开关，默认开）与场景视图状态：
        # _scenes 为 None 表示尚未加载，空列表为「确认无场景」
        from app.core.settings_store import get_show_scene_tab
        self._show_scene_tab = get_show_scene_tab()
        self._scenes: list[SceneInfo] | None = None
        self._scene_cards: dict[str, SceneCard] = {}
        # 场景列表后台拉取防重入
        self._loading_scenes = False
        # 产品页名称回退的异步查询防重入
        self._localize_busy = False
        # 已尝试过产品页查询的型号：失败后不再每 5 秒重试（实测该站点
        # 单次 2.4 秒，网络差时重试会把控制队列灌满）
        self._localize_attempted: set[str] = set()
        # DPI 变化时恢复期望逻辑尺寸：Qt 在缩放变化后保持物理尺寸
        # （逻辑尺寸被重算），窗口会显得偏大且底部留白；这里记住
        # 用户视角的逻辑尺寸，变化后恢复，让窗口跟随元素一起缩放
        self._expected_logical: tuple[int, int] | None = None
        self._last_dpr: float | None = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setInterval(_METRICS_INTERVAL_MS)
        self._metrics_timer.timeout.connect(self._refresh_metrics)
        self._metrics_timer.timeout.connect(self._push_tray_metrics)
        # 定时开关：10 秒扫一次，够在目标分钟那一分钟内触发；
        # 判定只认"正点那一分钟"，错过的时刻不补执行（见 core/scheduler.py）
        self._scheduler = Scheduler()
        self._schedule_dialog: ScheduleDialog | None = None
        # 正在执行中的规则 id：一次 tick 到下一次 tick 之间避免重复提交
        self._schedule_pending: set[str] = set()
        self._schedule_started = False
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(_SCHEDULE_INTERVAL_MS)
        self._schedule_timer.timeout.connect(self._on_schedule_tick)

        # 右下角小爱语音悬浮球：设备列表里存在小爱音箱才显示
        self._voice_fab = VoiceFab(self)
        self._voice_fab.submitted.connect(self._on_voice_command)
        self._voice_did: str | None = None

        # 主题控制器：跟随系统模式下监听系统配色变化
        from app.ui import si_theme as _si_theme_mod
        from app.ui.theme_service import ThemeController
        self._theme_ctrl = ThemeController()
        self._si_theme_mod = _si_theme_mod
        self._theme_ctrl.theme_changed.connect(self._on_theme_changed)
        # 托盘图标跟系统配色（任务栏底色由系统决定），独立于应用主题
        self._theme_ctrl.system_scheme_changed.connect(self._on_system_scheme_changed)

        # 系统托盘：左键快捷窗口，默认空需用户自主添加
        self._force_quit = False
        try:
            self._tray = TrayController(self._service, self._jobs, self)
            from app.core.settings_store import get_minimize_to_tray
            self._tray.set_tray_visible(get_minimize_to_tray())
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Tray] 创建失败: {e}")
            self._tray = None

        # 桌面挂件：常驻桌面的设备控制板，与托盘快捷窗口并列的独立入口。
        # 构造失败不得影响主流程（挂件是增强功能，缺失只少个面板）
        # 当前打开的状态明细面板（同一时刻只会有一个）
        self._status_panel = None
        self._board_manager: BoardManagerDialog | None = None
        try:
            self._board = WidgetsBoard(self._service, self._jobs, None)
            self._board.manage_requested.connect(self.show_board_manager)
            self._board.open_device_requested.connect(self._on_open_device)
            # 挂件内开关后回传，保持主界面卡片与开关记忆一致
            self._board.power_changed.connect(self._on_board_power_changed)
        except Exception as e:
            logger.warning("桌面挂件创建失败: %s", e)
            self._board = None

        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 内嵌标题栏（无边框窗口的拖动/窗口控制区） ----
        root.addWidget(self._build_title_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        content = QVBoxLayout()
        content.setContentsMargins(24, 16, 24, 8)
        content.setSpacing(10)

        # ---- 顶栏：家庭切换器 + 刷新 ----
        top_bar = QHBoxLayout()
        # 家庭名是下拉切换器，多家庭账号按米家 APP 的方式隔离展示
        self._home_btn = QPushButton("我的家庭")
        self._home_btn.setObjectName("homeSwitcher")
        self._home_btn.setCursor(Qt.PointingHandCursor)
        self._home_btn.clicked.connect(self._show_home_menu)
        top_bar.addWidget(self._home_btn)
        # 设备数徽标：单枚小胶囊（总量+在线是一体），锁高 22、垂直居中，
        # 免得被"全部"标题撑成大方块
        self._count_label = QLabel()
        self._count_label.setObjectName("deviceCountLabel")
        self._count_label.setFixedHeight(22)
        self._count_label.setTextFormat(Qt.TextFormat.RichText)
        top_bar.addWidget(self._count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        top_bar.addStretch(1)
        # 刷新与设置是高频入口，做成常驻图标按钮；托盘/定时任务/挂件
        # 这些低频配置收进「更多」，菜单只留必要的几项
        self._refresh_btn = _IconButton("mdi.refresh", "刷新设备", self)
        self._refresh_btn.clicked.connect(self.load_devices)
        self._settings_btn = _IconButton("mdi.cog-outline", "设置", self)
        self._settings_btn.clicked.connect(self.show_settings)

        self._more_menu = QMenu()
        self._more_menu.addAction("托盘管理").triggered.connect(self.show_tray_manager)
        self._more_menu.addAction("定时任务").triggered.connect(self.show_schedules)
        # 桌面挂件不再是"随手开关"：在 设置 → 应用功能 里统一启用/停用。
        # 管理入口只在启用时才出现（关闭时它无处可管，留着只会让人误解）
        self._board_manage_action = QAction("管理桌面挂件", self._more_menu)
        self._board_manage_action.triggered.connect(self.show_board_manager)
        self._more_menu.addAction(self._board_manage_action)
        # 显隐入口紧跟管理项，同样只在挂件开关开启时出现
        self._board_toggle_action = QAction("隐藏桌面挂件", self._more_menu)
        self._board_toggle_action.triggered.connect(self.toggle_board_visible)
        self._more_menu.addAction(self._board_toggle_action)
        # 「关于」固定在菜单最底部
        self._more_menu.addSeparator()
        self._more_menu.addAction("关于").triggered.connect(self.show_about)
        self._more_btn = _IconButton("mdi.dots-horizontal", "更多", self)
        self._more_btn.clicked.connect(self._show_more_menu)
        btn_wrapper = QWidget()
        btn_wrapper.setStyleSheet("background: transparent;")
        btn_layout = QHBoxLayout(btn_wrapper)
        btn_layout.setContentsMargins(0, 30, 25, 0)
        btn_layout.setSpacing(2)
        btn_layout.addWidget(self._refresh_btn)
        btn_layout.addWidget(self._settings_btn)
        btn_layout.addWidget(self._more_btn)
        top_bar.addWidget(btn_wrapper)
        content.addLayout(top_bar)

        # ---- 房间 tab 行：标签流式排列，放不下自动换行 ----
        self._tab_row = QHBoxLayout()
        self._tab_row.setSpacing(10)
        # 右侧多留一截：排序按钮要与顶栏那组图标按钮的右边缘对齐
        self._tab_row.setContentsMargins(0, 0, 25, 0)
        self._tab_buttons: dict[str, SiToggleButtonRefactor] = {}
        # 标签数量与宽度都不固定，用流式布局让它们像设备卡片一样自动换行；
        # 排序按钮单独固定在右侧，不参与换行
        self._tab_flow = FlowLayout(margin=0, spacing=14)
        self._tab_row.addLayout(self._tab_flow, stretch=1)
        self._sort_btn = self._make_tab_sort_button()
        self._tab_row.addWidget(
            self._sort_btn, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(self._tab_row)

        # ---- 环境状态胶囊：房间栏下方一行，点击看明细 ----
        self._temp_pill = self._make_status_pill(
            "mdi.thermometer", "当前范围的温度", self._show_env_detail)
        self._humidity_pill = self._make_status_pill(
            "mdi.water-percent", "当前范围的湿度", self._show_env_detail)
        self._light_pill = self._make_status_pill(
            "mdi.power", "当前范围内开启的设备", self._show_light_detail)
        self._consumable_pill = self._make_status_pill(
            "mdi.circle-slice-8", "耗材不足的设备", self._show_consumable_detail)
        self._status_row = QHBoxLayout()
        self._status_row.setSpacing(8)
        for pill in (self._temp_pill, self._humidity_pill,
                     self._light_pill, self._consumable_pill):
            self._status_row.addWidget(pill)
        self._status_row.addStretch(1)
        content.addLayout(self._status_row)

        # ---- 卡片滚动区 ----
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._grid_host = QWidget()
        self._grid_host.setObjectName("gridHost")
        self._grid_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 布局只创建一次，重建网格时仅清空条目；反复替换布局会因
        # deleteLater 的延迟执行陷入死循环
        from PySide6.QtWidgets import QGridLayout
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 4, 0, 12)
        self._grid.setSpacing(14)
        self._scroll.setWidget(self._grid_host)
        # 垂直滚动条显隐会改变视口宽度但不触发窗口 resizeEvent，
        # 监听视口自身尺寸变化，否则列数校验漏掉这类宽度变化
        self._scroll.viewport().installEventFilter(self)
        content.addWidget(self._scroll, stretch=1)

        content_host = QWidget()
        content_host.setObjectName("contentHost")
        content_host.setLayout(content)
        body.addWidget(content_host, stretch=1)
        root.addLayout(body)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status_hint)
        self._status_hint = QLabel()
        self._status_hint.setObjectName("statusHint")
        self._status_hint.hide()
        content.addWidget(self._status_hint)

        # 底部黑色状态栏已移除，提示改为内容区内部 3 秒自动消失
        self.setStatusBar(None)

        # 顶栏/标题栏固定控件的内联样式集中在此设置，主题切换时重设
        self._reapply_chrome_styles()

    # ---------- 主题 ----------

    def _reapply_chrome_styles(self) -> None:
        """主题相关的固定控件内联样式：主题切换时整体重设。

        卡片网格、托盘行、工作台等可重建结构由 _on_theme_changed
        走各自的重建入口；这里只管不重建的框架件。
        """
        # 设备数小胶囊：紧贴文字、不高过标题
        self._count_label.setStyleSheet(
            f"color: {SiColors.TEXT_MUTED}; background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 11px;"
            " padding: 0 10px; font-size: 9pt;")
        # 胶囊用卡片底色 + 细描边：浅色主题下窗口底色与 SURFACE 太接近，
        # 不描边会整块"消失"；悬停时描边变主题色，提示可点击
        pill_qss = (
            f"QPushButton {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 14px;"
            f" padding: 0 12px 0 9px; color: {SiColors.TEXT_PRIMARY};"
            " font-size: 9pt; }"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER};"
            f" border-color: {SiColors.THEME}; }}")
        for pill in (self._temp_pill, self._humidity_pill,
                     self._light_pill, self._consumable_pill):
            pill.setStyleSheet(pill_qss)
            pill.setIcon(qta.icon(pill._icon_name, color=SiColors.TEXT_SECONDARY))
        self._more_menu.setStyleSheet(
            f"QMenu {{ background: {SiColors.CARD}; border: 1px solid {SiColors.BTN_HOVER}; border-radius: 8px; padding: 4px; }}"
            f"QMenu::item {{ color: {SiColors.TEXT_PRIMARY}; padding: 6px 24px 6px 12px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background: {SiColors.BTN_HOVER}; }}")

        for btn in (self._refresh_btn, self._settings_btn, self._more_btn):
            btn.retheme()

    def apply_theme_mode(self, mode: str, accent: str | None = None) -> None:
        """设置页切换主题/主题色入口：应用后经 theme_changed 信号触发重建。

        `accent` 为 None 表示沿用设置里的主题色；传值则用于预览（不落盘）。
        """
        self._theme_ctrl.set_mode(mode, accent)

    def _on_system_scheme_changed(self, scheme: str) -> None:
        """系统深浅色变化：托盘图标跟随任务栏底色，与应用主题无关。"""
        if self._tray is not None:
            self._tray.apply_system_icon_theme(scheme)

    def apply_tray_icon_color(self, choice: str | None = None) -> None:
        """设置页改了托盘图标颜色：立即按当前系统深浅重画一次。

        choice 是设置页刚点选、还没保存的颜色；传 None 表示按已保存
        的设置刷新（例如取消时还原）。
        """
        if self._tray is None:
            return
        self._tray.apply_system_icon_theme(
            self._theme_ctrl.system_scheme(), choice)

    def _on_theme_changed(self, theme: str) -> None:
        """主题切换广播：重建可重建结构并刷新固定件。"""
        self._reapply_chrome_styles()
        self._rebuild_tabs()
        self._rebuild_grid()
        self._update_count_label()
        self._voice_fab.retheme()
        if self._tray is not None:
            # 托盘快捷窗口整窗重建后，立刻回填设备/开关/读数；
            # 若重建前窗口正显示，set_devices 会自动重新呼出
            self._tray.retheme()
            self._update_tray_devices()
            self._push_tray_metrics()
        if self._board is not None:
            self._board.retheme()
        # 打开中的挂件管理对话框（内联样式为构造期求值）同步刷新
        bdlg = self._board_manager
        if bdlg is not None and shiboken6.isValid(bdlg) and bdlg.isVisible():
            bdlg.retheme()
        # 打开中的设置页自身也刷新（其样式为构造时求值的内联样式）
        dlg = self._settings_dialog
        if dlg is not None and shiboken6.isValid(dlg) and dlg.isVisible():
            dlg.retheme()
        # 托盘独立打开的详情对话框（即用即建，但开着时切主题需重建）
        from PySide6.QtWidgets import QApplication
        from app.ui.device_dialog import DeviceDetailDialog
        for w in QApplication.topLevelWidgets():
            if isinstance(w, DeviceDetailDialog) and w.isVisible():
                w.retheme()
        if self._all_devices:
            self._refresh_power_states(force=False)

    # ---------- 内嵌标题栏与窗口操作 ----------

    @staticmethod
    def _make_status_pill(icon_name: str, tip: str, on_click) -> QPushButton:
        """环境状态胶囊：图标 + 数值，点击弹出明细列表。"""
        pill = QPushButton()
        pill.setFixedHeight(28)
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.setIconSize(QSize(14, 14))
        pill.setToolTip(tip)
        pill.setVisible(False)
        pill.clicked.connect(on_click)
        pill._icon_name = icon_name  # type: ignore[attr-defined]
        return pill

    def _build_title_bar(self) -> QWidget:
        """无边框窗口的内嵌标题栏：拖动移动、双击最大化、窗口控制钮。"""
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(32)

        title = QLabel("米家 - MiHome for Windows")
        title.setObjectName("titleBarText")

        min_btn = self._make_window_button("\uE921", self.showMinimized)
        self._max_btn = self._make_window_button("\uE922", self._toggle_maximized)
        self._max_btn.setObjectName("titleBarMax")
        close_btn = self._make_window_button("\uE8BB", self.close)
        close_btn.setObjectName("titleBarClose")

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 4, 0)
        lay.setSpacing(6)
        lay.addWidget(title)
        lay.addStretch(1)
        lay.addWidget(min_btn)
        lay.addWidget(self._max_btn)
        lay.addWidget(close_btn)
        return bar

    @staticmethod
    def _make_window_button(text: str, on_click) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("titleBarBtn")
        button.setFixedSize(26, 22)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(on_click)
        return button

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._max_btn.setText("\uE923" if self.isMaximized() else "\uE922")

    # ---------- 原生窗口行为（Windows 消息拦截） ----------
    #
    # 拦截 WM_NCCALCSIZE 抹掉系统标题栏的视觉区域，但保留原生窗口
    # 框架：拖动、双击最大化、最小化动画、Aero Snap、边缘缩放全部
    # 由系统接管（经 WM_NCHITTEST 上报命中区域），观感与原生无异。

    def nativeEvent(self, eventType, message):  # noqa: N802 (Qt 命名约定)
        if sys.platform == "win32" and eventType == "windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == _WM_NCCALCSIZE and msg.wParam:
                # 返回 0 去掉非客户区（标题栏+边框视觉），客户区占满窗口
                if self.isMaximized():
                    # 系统最大化时窗口会外扩一圈边框宽度，收缩客户区补偿
                    rect = wintypes.RECT.from_address(int(msg.lParam))
                    pad = self._system_frame_padding()
                    rect.left += pad
                    rect.top += pad
                    rect.right -= pad
                    rect.bottom -= pad
                return True, 0
            if msg.message == _WM_NCHITTEST:
                return True, self._hit_test(msg.lParam)
        return False, 0

    def _system_frame_padding(self) -> int:
        """系统边框宽度（含扩展边距），随 DPI 缩放。"""
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        try:
            dpi = user32.GetDpiForWindow(hwnd)
        except AttributeError:
            dpi = 96
        base = user32.GetSystemMetrics(32) + user32.GetSystemMetrics(92)  # SM_CXFRAME + SM_CXPADDEDBORDER
        return int(base * dpi / 96)

    def _hit_test(self, lParam: int) -> int:
        """把鼠标位置翻译成系统命中代码：边缘缩放 / 标题栏拖动 / 客户区。"""
        # lParam 低/高 16 位分别是屏幕坐标 x/y（有符号，多显示器可为负）
        x = ctypes.c_short(lParam & 0xFFFF).value
        y = ctypes.c_short((lParam >> 16) & 0xFFFF).value
        pt = wintypes.POINT(x, y)
        hwnd = int(self.winId())
        ctypes.windll.user32.ScreenToClient(hwnd, ctypes.byref(pt))
        # ScreenToClient 返回物理像素，Qt 的尺寸是逻辑像素，需先统一
        dpr = self.devicePixelRatioF() or 1.0
        cx, cy = pt.x / dpr, pt.y / dpr

        if not self.isMaximized():
            m = _RESIZE_MARGIN
            on_left = cx <= m
            on_right = cx >= self.width() - m
            on_top = cy <= m
            on_bottom = cy >= self.height() - m
            if on_top and on_left:
                return _HTTOPLEFT
            if on_top and on_right:
                return _HTTOPRIGHT
            if on_bottom and on_left:
                return _HTBOTTOMLEFT
            if on_bottom and on_right:
                return _HTBOTTOMRIGHT
            if on_left:
                return _HTLEFT
            if on_right:
                return _HTRIGHT
            if on_top:
                return _HTTOP
            if on_bottom:
                return _HTBOTTOM

        # 标题栏区域交给系统（拖动/双击最大化）；窗口控制钮保持 HTCLIENT
        if cy <= 32:
            child = self.childAt(int(cx), int(cy))
            if not isinstance(child, QPushButton):
                return _HTCAPTION
        return _HTCLIENT

    # ---------- 启动与数据加载 ----------

    def _show_status(self, text: str, timeout_ms: int = 3000) -> None:
        self._status_hint.setText(text)
        self._status_hint.show()
        self._status_timer.start(timeout_ms)

    def _clear_status_hint(self) -> None:
        self._status_hint.hide()

    def start(self) -> None:
        """应用入口第一步：校验登录态，决定拉列表还是弹扫码窗。"""
        self._show_status("正在检查登录状态…")
        self._jobs.submit(
            self._service.login_status,
            on_success=self._after_login_check,
            # login_status 内部已兜底返回 False；这里只拦「根本没法问」的
            # 意外异常，同样按未登录处理，绝不让启动检查无声卡死
            on_error=lambda exc: (
                logger.warning("登录状态检查失败: %s", exc),
                self._after_login_check(False),
            ),
        )
        self._maybe_check_update()

    def _maybe_check_update(self) -> None:
        """按设置在启动时后台检查一次 GitHub 新版本。

        延迟数秒发起：错开设备列表加载与可能的扫码弹窗，避免多个
        对话框同时抢占；静默启动（托盘常驻）也要查，发现新版时
        对话框独立居中显示。
        """
        if self._update_check_done:
            return
        self._update_check_done = True
        from app.core.settings_store import get_check_update_enabled
        if not get_check_update_enabled():
            return
        from PySide6.QtCore import QTimer

        def _run() -> None:
            from app.ui.update_flow import check_update
            check_update(self, manual=False)

        QTimer.singleShot(3000, self, _run)

    def _after_login_check(self, logged_in: bool) -> None:
        if logged_in:
            self._startup_load()
            return
        # 关键：不能在这个回调里直接开模态登录窗。
        # 本函数是 JobExecutor 投递结果的回调（运行在投递定时器的栈上），
        # 而模态窗的 exec() 会在里面再跑一层事件循环；此时「取二维码」这个
        # 新任务虽然被 worker 执行完了，结果却要靠同一个投递定时器送到界面，
        # 而那个定时器正被挡在栈上 —— 于是扫码页永远停在「正在获取登录二维码…」。
        # 先把手交还给事件循环，下一拍再开窗。
        from PySide6.QtCore import QTimer as _QTimer

        _QTimer.singleShot(0, self, self._open_login_dialog)

    def _open_login_dialog(self) -> None:
        # 惰性导入：qrcode 连带 PIL 约 15MB，仅在真正需要扫码时加载；
        # 凭据缓存的绝大多数会话永远走不到这里
        from app.ui.login_dialog import LoginDialog

        logger.info("未登录：打开扫码登录窗口")
        dialog = LoginDialog(self._service, self._jobs, self)
        if dialog.exec_and_wait() == LoginDialog.Accepted:
            self._startup_load()
        else:
            QMessageBox.information(self, "未登录", "未完成扫码登录，程序即将退出。")
            self.close()

    def _startup_load(self) -> None:
        """启动加载：缓存命中直接渲染，未命中走在线拉取。

        缓存里的开关记忆与温湿度读数一并恢复，启动瞬间即有完整
        可用的界面与上次读数，新值拿到后覆盖。
        """
        self._poll_timer.start()
        self._metrics_timer.start()
        self._start_scheduler()
        cached = device_cache.load()
        if cached is None:
            self.load_devices()
            return
        devices, known_power, metrics = cached
        self._known_power.update(known_power)
        self._metrics.update(metrics)
        self._show_status("已从本地缓存加载，点击右上角菜单刷新可同步最新设备")
        self._apply_devices(devices)
        self._apply_board_enabled()
        # 缓存命中不会走 _on_devices_loaded，耗材要单独拉一次，
        # 否则启动后要手动刷新才出现
        self._refresh_consumables()

    def _apply_board_enabled(self) -> None:
        """按设置里的「桌面挂件」开关显示/隐藏挂件，并同步各菜单入口。

        开关同时承担"是否自启动"和"是否显示"两件事（旧版拆成
        autostart + visible 两个字段，勾了却关不掉、关了又自己弹，
        语义一直有歧义）。这里只读这一个值。
        """
        from app.core import board_store

        enabled = board_store.get_enabled()
        if self._board is not None:
            if enabled:
                self.show_board()
            else:
                self.hide_board()
        # 显隐状态定下来后再同步菜单：显隐项的文案依赖挂件当前是否可见
        self._refresh_board_menu_items()

    def _board_visible(self) -> bool:
        return self._board is not None and self._board.isVisible()

    def _refresh_board_menu_items(self) -> None:
        """同步挂件菜单入口：开关关闭时全部隐藏，显隐项按当前状态取名。"""
        from app.core import board_store

        enabled = board_store.get_enabled()
        action = getattr(self, "_board_manage_action", None)
        if action is not None:
            action.setVisible(enabled)
        toggle = getattr(self, "_board_toggle_action", None)
        if toggle is not None:
            # 挂件构造失败时显隐项没有意义（管理入口仍保留，它只依赖设备清单）
            toggle.setVisible(enabled and self._board is not None)
            toggle.setText(
                "隐藏桌面挂件" if self._board_visible() else "显示桌面挂件")
        if self._tray is not None:
            self._tray.set_board_enabled(enabled)

    # ---------- 定时任务 ----------

    def _start_scheduler(self) -> None:
        """登录完成、服务可用后启动定时扫描。

        启动瞬间先跑一次 tick：这样「此刻正好是 7:00」时建立的规则
        也能立刻生效，不必等到下一个 10 秒周期。
        """
        if self._schedule_started:
            return
        self._schedule_started = True
        self._reload_schedules()
        self._schedule_timer.start()
        # 设备列表还没载入时不跑首次 tick：此刻报"错过"只能显示 did 后六位，
        # 也分不清设备是否已被移除。等 10 秒后的常规 tick 即可。
        if self._all_devices:
            self._on_schedule_tick()

    def _reload_schedules(self) -> None:
        """把磁盘上的规则读进调度器（对话框里增删/启停后调用）。"""
        self._scheduler.set_rules(schedule_store.load())

    def _on_schedule_tick(self) -> None:
        """定时器回调：执行到点的规则，并播报今天错过的规则。"""
        result = self._scheduler.tick()
        for rule in result.missed:
            device = next((d for d in self._all_devices if d.did == rule["did"]), None)
            name = device.name if device is not None else rule["did"][-6:]
            logger.info("定时任务已错过（不补执行）: %s %s", name, rule["time"])
        if result.missed:
            names = "、".join(
                next((d.name for d in self._all_devices if d.did == r["did"]),
                     r["did"][-6:])
                for r in result.missed[:3]
            )
            more = "" if len(result.missed) <= 3 else f" 等 {len(result.missed)} 条"
            Toast.info(
                self,
                f"今天已错过定时：{names}{more}（错过的时刻不补执行）",
                5000,
            )
        for rule in result.due:
            self._run_schedule_rule(rule)

    def _run_schedule_rule(self, rule: dict) -> None:
        """把一条到点规则写进控制队列；成功后落 last_fired 防同日重复。"""
        rule_id = str(rule.get("id", ""))
        if rule_id in self._schedule_pending:
            return
        self._schedule_pending.add(rule_id)
        did = rule["did"]
        turn_on = rule["action"] == "on"
        device = next((d for d in self._all_devices if d.did == did), None)
        if self._all_devices and device is None:
            # 设备已从账号里移除：规则保留但不执行，与对话框里的提示一致。
            # 不 mark_fired，用户把规则改到别的设备后当天仍可生效。
            logger.info("定时规则指向的设备已不存在，跳过: %s", did)
            self._schedule_pending.discard(rule_id)
            return
        name = device.name if device is not None else did[-6:]
        today = datetime.now().strftime("%Y-%m-%d")

        def _on_success(_result=None, rid=rule_id, d=did, on=turn_on,
                        rname=name, at=rule["time"], date_text=today):
            self._schedule_pending.discard(rid)
            schedule_store.mark_fired(rid, date_text)
            self._scheduler.mark_fired(rid)
            self._apply_scheduled_state(d, on)
            Toast.info(self, f"已按定时{'打开' if on else '关闭'}「{rname}」（{at}）", 3000)

        def _on_error(error, rid=rule_id, rname=name, at=rule["time"]):
            self._schedule_pending.discard(rid)
            logger.warning("定时任务执行失败 %s %s: %s", rname, at, error)
            Toast.info(self, f"定时执行失败「{rname}」（{at}）：{error}", 5000)

        self._jobs.submit(
            lambda d=did, on=turn_on: self._service.set_power_state(d, on),
            on_success=_on_success,
            on_error=_on_error,
            priority=PRIORITY_CONTROL,
        )

    def _apply_scheduled_state(self, did: str, turn_on: bool) -> None:
        """定时执行成功后同步三处界面：主界面卡片、托盘、桌面挂件。"""
        # 定时也算"本地操作"：在途轮询读到的是执行前的旧值，必须丢弃
        self._bump_power_gen(did)
        self._known_power[did] = turn_on
        card = self._cards.get(did)
        if card is not None and shiboken6.isValid(card):
            card.set_power_state(turn_on)
        try:
            self._update_tray_devices()
        except Exception:
            pass
        if self._board is not None:
            try:
                self._board.apply_power_states({did: turn_on})
            except Exception:
                pass

    def show_schedules(self) -> None:
        """打开定时任务对话框（托盘菜单与更多菜单共用入口）。"""
        dlg = self._schedule_dialog
        if dlg is not None and shiboken6.isValid(dlg) and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        try:
            # 最小化到任务栏时 isHidden() 为 False，但以它为父窗口会让
            # 对话框跟着最小化、看起来"没打开"；故一并判断 isMinimized
            parent = None if (self.isHidden() or self.isMinimized()) else self
            dlg = ScheduleDialog(self._all_devices, parent)
            self._schedule_dialog = dlg
            dlg.rules_changed.connect(self._reload_schedules)
            dlg.exec()
        except Exception as exc:  # 打不开时把原因摆到用户面前，别静默失败
            self._schedule_dialog = None
            logger.exception("打开定时任务对话框失败")
            QMessageBox.warning(
                self, "定时任务打不开",
                f"打开定时任务面板失败：{exc}\n\n详细信息见 debug.log。")
            return
        finally:
            if dlg is not None:
                dlg.deleteLater()
            self._schedule_dialog = None
        # 对话框里可能删/停了规则，关掉后立刻重载，避免还在按旧规则触发
        self._reload_schedules()

    def load_devices(self) -> None:
        if self._loading_devices:
            return
        self._loading_devices = True
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setToolTip("刷新中…")
        self._show_status("正在获取设备列表…")
        self._jobs.submit(
            self._service.list_devices,
            on_success=self._on_devices_loaded,
            on_error=self._on_load_error,
        )

    def _on_devices_loaded(self, devices: list[DeviceInfo]) -> None:
        self._loading_devices = False
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setToolTip("刷新设备")
        # 对比要在替换前做：added/removed 以旧列表为基准
        added, removed = self._diff_devices(devices)
        self._apply_devices(devices)
        device_cache.save(devices, self._known_power, self._metrics)
        self._apply_board_enabled()
        self._refresh_consumables()
        if self._all_devices or added or removed:
            Toast.info(self, _refresh_summary(len(devices), added, removed))

    def _diff_devices(self, devices: list[DeviceInfo]) -> tuple[int, int]:
        old = {d.did for d in self._all_devices}
        new = {d.did for d in devices}
        return len(new - old), len(old - new)

    def _apply_devices(self, devices: list[DeviceInfo]) -> None:
        self._all_devices = devices
        self._apply_custom_names()
        homes = sorted(
            {d.home_name for d in devices},
            key=lambda h: (h == "未知", h),
        )
        self._homes = [_ALL_HOMES] + homes
        if self._current_home not in self._homes:
            self._current_home = self._homes[0] if self._homes else _ALL_HOMES
        # 默认标签只在首次加载时应用：之后刷新设备要保持用户当前看的
        # 房间，否则点一次刷新就被拽回默认页；房间消失时退回全屋
        if not self._room_initialized:
            self._current_room = self._preferred_room()
            self._room_initialized = True
        elif self._current_room not in self._natural_tabs():
            self._current_room = _ALL_ROOMS
        self._home_btn.setText(f"{self._current_home} ▾")
        self._rebuild_tabs()
        self._rebuild_grid()
        self._update_count_label()
        self._refresh_power_states(force=False)
        self._refresh_metrics()
        self._update_voice_fab()
        self._update_tray_devices()
        self._update_board_devices()
        # 本地化必须在列表落地后立即跑一次：只在开关轮询回调里触发的话，
        # 无开关设备（手环、牙刷）可能永远等不到，界面就一直是英文名
        self._maybe_localize_names()
        self._load_card_icons()
        # 场景入口开启时启动即预取场景列表，进场景页无需等待
        if self._show_scene_tab:
            self._load_scenes()

    # ---------- 设备图标 ----------

    def _load_card_icons(self) -> None:
        """增量拉取设备图标：只对缓存里没有的型号调接口。

        已下载图片的卡片回填由 _rebuild_grid 负责（建卡即查本地文件），
        本方法只处理缺失部分：拉 URL、缺图下发下载；图标 URL 与图片
        都落本地缓存（见 icon_store），设备列表不变时不会重复调接口。
        """
        models = sorted({d.model for d in self._all_devices if d.model})
        if not models:
            return
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

    def _apply_card_icon(self, model: str, path: str) -> None:
        """同一型号的所有卡片共用一份图标图片。"""
        if not path:
            return
        for card in self._cards.values():
            if card.device.model == model:
                card.set_icon(Path(path))

    def _apply_custom_names(self) -> None:
        """用本地自定义名称覆盖设备显示名。

        云端名单独存一份到 cloud_name，用户恢复默认时直接取回，
        不必等下一次刷新设备列表。
        """
        overrides = name_override_store.load_all()
        for device in self._all_devices:
            if not device.cloud_name:
                device.cloud_name = device.name
            device.name = overrides.get(device.did) or device.cloud_name

    def _maybe_localize_names(self) -> None:
        """未改名英文设备用中文名替换显示名（两个来源按序兜底）。

        ① spec 缓存里的产品名（多数设备有，零网络开销）；
        ② miot-spec 产品页（/p/<model>）的中文商品名——无公开 spec
          的设备（如仅蓝牙类）只有这里有，经后台线程抓取并缓存
          （含“确认无中文名”的结果，不重复查询）。
        改名设备与中文名设备不受影响；spec 产品名相同的设备
        （同型号多台）追加 did 尾号以区分。
        """
        if not self._all_devices:
            return
        try:
            # 有自定义名的设备不参与本地化：用户改的名字优先级最高，
            # 否则英文自定义名会被规格里的中文名替换掉，主页与详情页
            # 显示的名字就会不一致
            overrides = name_override_store.load_all()
            ascii_devices = [d for d in self._all_devices
                             if d.name.isascii() and d.model
                             and d.did not in overrides]
            # 这个方法挂在开关轮询回调上，几秒一次；没有需要本地化的
            # 设备就立刻返回，别做无谓的字典构建与规格遍历
            if not ascii_devices:
                return
            names_now = {d.did: d.name for d in ascii_devices}
            replacements = self._service.localized_product_names(
                list(names_now), names_now)
            model_to_did = {d.model: d.did for d in ascii_devices}
            models = sorted(model_to_did)
            if models:
                # 产品页名称按型号缓存，需映射回 did 再并入替换表
                for model, product_name in self._service.cached_product_page_names(models).items():
                    replacements[model_to_did[model]] = product_name
        except Exception:
            logger.exception("本地化设备名失败")
            return
        if replacements:
            if self._apply_display_names(replacements, overrides):
                return  # 已重建并同步托盘
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

    def _apply_display_names(self, replacements: dict[str, str],
                             overrides: dict[str, str]) -> bool:
        """把替换名写入设备对象并重建展示；返回是否有实际变化。

        这里再挡一次有自定义名的设备：用户改的名字优先级最高，
        即使调用方漏过滤，也不能被规格里的中文名覆盖。
        overrides 由调用方传入，省掉一次重复读取。
        """
        changed = False
        for d in self._all_devices:
            if d.did in overrides:
                continue
            new_name = replacements.get(d.did)
            if new_name and new_name != d.name:
                d.name = new_name
                # 本地化后的名字才是"没有自定义名时该显示的名字"，
                # cloud_name 要同步，否则恢复默认会退回云端英文名
                d.cloud_name = new_name
                changed = True
        if not changed:
            return False
        # 同型号多台替换后同名：追加 did 尾号区分（如 ·468）
        by_name: dict[str, int] = {}
        for d in self._all_devices:
            by_name[d.name] = by_name.get(d.name, 0) + 1
        for d in self._all_devices:
            if by_name.get(d.name, 0) > 1 and d.did in replacements:
                d.name = f"{d.name}·{d.did[-3:]}"
                # 加尾号后的名字同样是默认展示名，cloud_name 跟着走
                d.cloud_name = d.name
        self._rebuild_grid()
        self._update_count_label()
        self._update_tray_devices()
        return True

    def _on_product_names_fetched(self) -> None:
        self._localize_busy = False
        self._maybe_localize_names()

    def _on_product_names_failed(self, error: Exception) -> None:
        # service 层已把查询失败按「无中文名」缓存（避免反复重试堵队列），
        # 这里只会收到罕见意外；记录后等下次轮询再走完整流程
        self._localize_busy = False
        logger.warning("产品页名称查询失败: %s", error)

    def _update_tray_devices(self) -> None:
        if self._tray is not None:
            try:
                self._tray.set_devices(
                    self._displayed_devices(), self._known_power)
            except Exception:
                # 托盘窗口可能在关闭动画里，同步失败不影响主界面
                logger.warning("同步托盘设备失败", exc_info=True)

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
        """显示桌面挂件（设置开关 / 折叠切换共用）。

        不写任何持久化状态：是否显示只由设置里的「桌面挂件」开关决定。
        已经可见时不再 raise_，避免改设置时把焦点从设置窗抢走。
        """
        if self._board is None:
            return
        self._board.set_devices(self._all_devices, self._known_power)
        self._board.show()
        # 非置顶时 raise_ 会把挂件顶到所有窗口之上（它只是桌面挂件，
        # 不该盖住用户正在用的程序），改成按开关归位
        self._board.apply_z_order()

    def hide_board(self) -> None:
        """隐藏挂件（关闭设置开关 / 菜单里手动隐藏时调用）。"""
        if self._board is not None:
            self._board.hide()

    def toggle_board_visible(self) -> None:
        """显示或隐藏桌面挂件（主界面与托盘菜单共用）。

        只切换本次显隐，不改「桌面挂件」开关：开关仍开着，重启后
        挂件照常出现。
        """
        if self._board is None:
            return
        if self._board.isVisible():
            self.hide_board()
        else:
            self.show_board()
        # 显隐项文案依赖挂件当前是否可见，切换后立即同步两处菜单
        self._refresh_board_menu_items()

    def toggle_board_collapse(self) -> None:
        """折叠/展开挂件（挂件自身与托盘菜单共用）。"""
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
        # 单实例守卫：连点「+」会排队触发多次本函数，模态窗的 exec() 一旦
        # 返回就再开一个，屏幕上便叠出好几个「管理桌面挂件」（用户实测反馈）
        if self._board_manager is not None:
            self._board_manager.raise_()
            self._board_manager.activateWindow()
            return
        parent = None if self.isHidden() else self
        # 场景清单：优先用已缓存的全量场景；没有就现场拉一次（失败则空列表，
        # 至少设备部分仍可管理，不会因为场景拉取失败打不开对话框）
        scenes = self._scenes
        if scenes is None:
            try:
                from app.core.models import SceneInfo  # noqa: F401
                scenes = self._service.scene_list(None)
            except Exception:
                scenes = []
        scenes_dict = [{"scene_id": s.scene_id, "name": s.name,
                        "home_id": s.home_id, "home_name": s.home_name}
                       for s in (scenes or [])]
        dlg = BoardManagerDialog(self._all_devices, parent, scenes=scenes_dict)
        self._board_manager = dlg
        dlg.opacity_preview.connect(self._on_board_opacity_preview)
        dlg.columns_preview.connect(self._on_board_columns_preview)
        dlg.background_preview.connect(self._on_board_background_preview)
        dlg.topmost_preview.connect(self._on_board_topmost_preview)
        dlg.lock_preview.connect(self._on_board_lock_preview)
        # 对话框遮挡会让挂件暂停「悬停恢复不透明」，否则看不到透明度效果
        if self._board is not None:
            self._board.set_previewing(True)
        result = QDialog.Rejected
        try:
            result = dlg.exec()
        except Exception as exc:
            # 不兜住的话：previewing 永远停在 True（挂件不再随悬停恢复不透明），
            # _board_manager 也永远不为 None —— 这之后管理窗再也打不开了
            logger.exception("打开挂件管理对话框失败")
            Toast.info(self, f"挂件管理窗打开失败：{exc}", 4000)
        finally:
            dlg.deleteLater()
            self._board_manager = None
            if self._board is not None:
                self._board.set_previewing(False)
        if result == QDialog.Accepted:
            from app.core import board_store
            board_store.save_devices(dlg.selected_dids())
            board_store.save_scenes(dlg.selected_scenes())
            board_store.save_order(dlg.ordered_items())
            self._update_board_devices()
            Toast.info(self, "挂件内容已更新", 2000)

    def _on_board_opacity_preview(self, value: float) -> None:
        # 落盘在对话框侧已完成，这里只负责让窗口实时可见
        if self._board is not None:
            self._board.set_opacity(value, persist=False)

    def _on_board_columns_preview(self, value: int) -> None:
        if self._board is not None:
            self._board.set_columns(value)

    def _on_board_background_preview(self, color) -> None:
        # 落盘在对话框侧已完成，这里只让真实窗口立即换色
        if self._board is not None:
            self._board.set_bg(color, persist=False)

    def _on_board_topmost_preview(self, topmost: bool) -> None:
        if self._board is not None:
            self._board.set_topmost(bool(topmost), persist=False)

    def _on_board_lock_preview(self, locked: bool) -> None:
        if self._board is not None:
            self._board.set_locked(bool(locked), persist=False)

    def _show_more_menu(self) -> None:
        """在更多按钮左下方弹出菜单，右边缘与按钮右边缘对齐。"""
        self._refresh_board_menu_items()
        self._more_menu.adjustSize()
        pos = self._more_btn.mapToGlobal(self._more_btn.rect().bottomRight())
        menu_w = self._more_menu.sizeHint().width()
        self._more_menu.exec(QPoint(pos.x() - menu_w, pos.y() + 8))

    def request_force_quit(self) -> None:
        """托盘菜单「退出」调用：closeEvent 跳过隐藏到托盘，直接退出。"""
        self._force_quit = True

    def devices(self) -> list[DeviceInfo]:
        """当前全量设备列表（托盘快捷窗口打开详情用）。"""
        return self._all_devices

    def show_tray_manager(self) -> None:
        """打开托盘快捷项管理对话框（托盘菜单与主界面菜单共用入口）。"""
        if not self._all_devices:
            Toast.info(self, "请先刷新获取设备列表", 2500)
            return
        # 管理时收起快捷窗口避免与对话框重叠时显示旧状态
        if self._tray is not None:
            self._tray.hide_quick()
        # 主窗口可能已隐藏到托盘，管理对话框需独立显示
        parent = None if self.isHidden() else self
        # 场景数据始终按「全部家庭」口径：主界面缓存只在当前家庭为
        # 全部时才通用，否则交给对话框切页时自行拉取
        cached = self._scenes if self._current_home == _ALL_HOMES else None
        dlg = TrayManagerDialog(
            self._all_devices, parent,
            scenes=cached,
            scene_loader=(lambda: self._service.scene_list(None))
            if self._show_scene_tab else None,
            jobs=self._jobs,
        )
        result = dlg.exec()
        # 数据须在 deleteLater 前取出
        order = dlg.selected_order()
        scene_by_id = {s["scene_id"]: s for s in dlg.selected_scene_data()}
        dlg.deleteLater()
        if result == QDialog.Accepted:
            from app.core import tray_store as _ts
            # 三处写入保持同一展示顺序：order 为准，devices/scenes 按它
            # 排序落盘，order 缺失兜底合成时顺序才与旧显示一致
            dids = [it["id"] for it in order if it["type"] == "device"]
            scenes = [scene_by_id[it["id"]] for it in order
                      if it["type"] == "scene" and it["id"] in scene_by_id]
            _ts.save(dids)
            _ts.save_scenes(scenes)
            _ts.save_order(order)
            self._update_tray_devices()
            Toast.info(self, "托盘项目已更新", 2000)

    def show_about(self) -> None:
        """打开关于对话框（即用即建）。"""
        from app.ui.about_dialog import AboutDialog
        dlg = AboutDialog(self, api_version=self._service.api_version())
        dlg.exec()
        dlg.deleteLater()

    def show_settings(self) -> None:
        """打开设置对话框（托盘菜单与主界面菜单共用入口）。"""
        # 弹出期间置顶已有实例，避免托盘与主界面重复弹出
        dlg = self._settings_dialog
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = SettingsDialog(self, devices=self._all_devices)
        self._settings_dialog = dlg
        dlg.exec()
        scale_changed = getattr(dlg, "_scale_changed", False)
        dlg.deleteLater()
        self._settings_dialog = None
        # 界面缩放改动需重启应用生效：提供一键重启（常驻托盘时关窗口
        # 只是隐藏到托盘、进程仍在，仅提示会让用户以为没生效）
        if scale_changed:
            from app.ui.restart import restart_app
            ret = QMessageBox.question(
                self, "界面缩放已保存",
                "界面缩放需重启应用后生效。\n是否立即重启？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes)
            if ret == QMessageBox.Yes:
                self._force_quit = True
                restart_app()
        # 设置可能变了，同步托盘图标显隐
        from app.core.settings_store import get_minimize_to_tray
        if self._tray is not None:
            self._tray.set_tray_visible(get_minimize_to_tray())
        # 「桌面挂件」开关可能刚被切换：立即生效（开启即显示、关闭即隐藏），
        # 同时刷新两个菜单里的挂件入口
        self._apply_board_enabled()
        # 两个隐藏开关可能变化：重载并刷新展示
        from app.core.settings_store import (
            get_hide_no_func_devices, get_hide_offline_devices)
        new_hide = get_hide_no_func_devices()
        new_hide_off = get_hide_offline_devices()
        if new_hide != self._hide_no_func or new_hide_off != self._hide_offline:
            self._hide_no_func = new_hide
            self._hide_offline = new_hide_off
            self._rebuild_tabs()
            self._rebuild_grid()
            self._update_count_label()
            self._update_tray_devices()
        # “显示场景选项卡”开关可能变化：重载选项卡并刷新网格；
        # 关闭时若正停留在场景视图，重置回全屋，避免入口消失后无法切回
        from app.core.settings_store import get_show_scene_tab
        new_show = get_show_scene_tab()
        if new_show != self._show_scene_tab:
            self._show_scene_tab = new_show
            if not new_show and self._current_room == _SCENE_TAB:
                self._current_room = _ALL_ROOMS
            self._rebuild_tabs()
            self._rebuild_grid()
            self._update_count_label()
        # 同步小爱悬浮按钮显隐
        self._update_voice_fab()

    def _update_voice_fab(self) -> None:
        """根据设置与设备列表决定小爱悬浮按钮显隐。

        输出音箱：设置的默认音箱（在线时）优先，否则回退第一个在线音箱。
        """
        from app.core.settings_store import get_default_speaker_did, get_voice_fab_enabled
        pref = get_default_speaker_did()
        speaker = next(
            (d for d in self._all_devices
             if d.did == pref and is_speaker(d) and d.online),
            None,
        )
        if speaker is None:
            speaker = next(
                (d for d in self._all_devices if is_speaker(d) and d.online),
                None,
            )
        self._voice_did = speaker.did if speaker else None
        # 仅当设置开启且存在在线音箱时显示
        show = get_voice_fab_enabled() and speaker is not None
        self._voice_fab.setVisible(show)
        if not show:
            self._voice_fab.collapse()

    def _on_voice_command(self, text: str) -> None:
        did = self._voice_did
        if did is None:
            return
        # 设置的默认音箱离线/不在列表 → 已回退：提示里说明，并把
        # 设置回退为「自动」（否则偏好一直指向离线设备，每次都要回退）
        from app.core.settings_store import get_default_speaker_did, set_default_speaker_did
        pref = get_default_speaker_did()
        fallback_note = ""
        if pref and pref != did:
            fallback_note = "（首选音箱离线，已由当前在线音箱代答）"
            set_default_speaker_did("")
        self._jobs.submit(
            lambda: self._service.run_action(did, "execute-text-directive", [text]),
            on_success=lambda _, note=fallback_note: Toast.info(
                self, f"已告诉小爱同学：{text}{note}", 3000),
            on_error=lambda e: Toast.info(self, f"执行失败：{e}", 4000),
        )

    def _update_count_label(self) -> None:
        muted = SiColors.TEXT_MUTED
        # 在线圆点固定为米家绿：它是"在线状态"的语义色，不随主题色变
        online_dot = "#3dbba4"
        if self._current_room == _SCENE_TAB:
            count = len(self._scenes or [])
            self._count_label.setText(
                f'<span style="color:{muted}">共 {count} 个场景</span>')
            for pill in (self._temp_pill, self._humidity_pill,
                         self._light_pill, self._consumable_pill):
                pill.setVisible(False)
            return
        subset = self._visible_subset()
        total = len(subset)
        online = sum(1 for d in subset if d.online)
        self._count_label.setText(
            f'<span style="color:{muted}">共 {total} 台设备</span>'
            f'&nbsp;&nbsp;<span style="color:{online_dot}">●</span>'
            f'&nbsp;<span style="color:{muted}">{online} 台在线</span>'
        )
        self._update_status_pills(subset)

    def _update_status_pills(self, subset: list[DeviceInfo]) -> None:
        """刷新环境状态胶囊。

        温湿度按"第一个有读数的设备"取值：全屋时是列表里第一台温湿度计，
        切到某个房间后自然变成该房间内的第一台，不需要额外聚合。
        开启数只统计当前范围内状态确认为开的设备。
        """
        # 取第一个能读到温湿度的设备。这里读 _climate 里的原始值，
        # 不能解析副标题文案：那可能已被改成显示功率，解析出来是错的
        temp_text = ""
        humidity_text = ""
        for device in subset:
            values = self._climate.get(device.did)
            if not values:
                continue
            if not temp_text and "temperature" in values:
                temp_text = format_metric("temperature", values["temperature"])
            if not humidity_text:
                for key in ("relative-humidity", "humidity"):
                    if key in values:
                        humidity_text = format_metric(key, values[key])
                        break
            if temp_text and humidity_text:
                break
        self._temp_pill.setText(temp_text)
        self._temp_pill.setVisible(bool(temp_text))
        self._humidity_pill.setText(humidity_text)
        self._humidity_pill.setVisible(bool(humidity_text))

        lights = sum(1 for d in subset if self._known_power.get(d.did) is True)
        self._light_pill.setText(f"开启 {lights}")
        self._light_pill.setVisible(lights > 0)

        # 耗材跟随当前家庭/房间归属，不再用全屋口径
        low = sum(1 for item in self._consumables_in_scope(subset) if item.get("low"))
        self._consumable_low = low
        self._consumable_pill.setText(f"耗材不足 {low}")
        self._consumable_pill.setVisible(low > 0)

    def _refresh_consumables(self) -> None:
        """异步拉一次耗材明细（全屋口径），失败静默不影响主流程。"""
        self._jobs.submit(
            self._service.consumable_items,
            on_success=self._on_consumables_loaded,
            on_error=lambda exc: logger.debug("耗材统计失败: %s", exc),
        )

    def _on_consumables_loaded(self, items) -> None:
        self._consumables = items or []
        self._consumable_low = sum(1 for item in self._consumables if item.get("low"))
        self._update_count_label()

    def _visible_subset(self) -> list[DeviceInfo]:
        """当前家庭 + 房间筛选后的设备列表（统计与明细共用）。"""
        visible = self._visible_devices()
        if self._current_home == _ALL_HOMES:
            return visible
        return [d for d in visible if d.home_name == self._current_home]

    def _open_status_panel(self, title: str, subtitle: str, groups) -> None:
        """打开状态明细面板（胶囊点击的统一入口）。"""
        from app.ui.status_panel import StatusPanelDialog

        dlg = StatusPanelDialog(title, subtitle, groups, self)
        # 面板里的设备卡片与主页同款：开关/点开详情都走主窗口同一条链路
        dlg.power_toggled.connect(self._on_power_clicked)
        dlg.open_requested.connect(self._on_open_device)
        # 开关结果回来后要回填面板里的卡片（失败回滚也走这里）
        self._status_panel = dlg
        try:
            dlg.exec()
        finally:
            self._status_panel = None
            dlg.deleteLater()

    def _show_env_detail(self) -> None:
        rows = [
            {
                "device": d,
                "state": self._known_power.get(d.did),
                "metrics": self._metrics.get(d.did),
                "icon": icon_store.icon_path(d.model),
            }
            for d in self._visible_subset() if self._metrics.get(d.did)
        ]
        self._open_status_panel("温湿度", f"{len(rows)} 台设备有读数", [("", rows)])

    def _show_light_detail(self) -> None:
        by_room: dict[str, list[dict]] = {}
        for device in self._visible_subset():
            if self._known_power.get(device.did) is True:
                by_room.setdefault(device.room_name, []).append({
                    "device": device,
                    "state": True,
                    "icon": icon_store.icon_path(device.model),
                })
        groups = [(room, rows) for room, rows in by_room.items()]
        count = sum(len(rows) for _room, rows in groups)
        # 统计口径是"开关为开的设备"，不限于灯具，文案不要写死成灯
        self._open_status_panel("开启的设备", f"{count} 个设备已开启", groups)

    def _consumables_in_scope(self, subset=None) -> list[dict]:
        """当前家庭/房间范围内的耗材，设备名换成列表里的中文名。

        耗材接口返回的是设备英文名（如 mijia E-toothbrush t700），
        这里用 did 对齐设备列表，统一显示界面里已有的中文名。
        subset 由调用方传入可省掉一次可见设备遍历。
        """
        devices = subset if subset is not None else self._visible_subset()
        lookup = {d.did: d for d in devices}
        result = []
        for item in self._consumables:
            device = lookup.get(item.get("did"))
            if device is None:
                continue
            entry = dict(item)
            entry["name"] = device.name
            entry["room_name"] = device.room_name
            result.append(entry)
        return result

    def _show_consumable_detail(self) -> None:
        rows = []
        for item in self._consumables_in_scope():
            if not item.get("low"):
                continue
            parts = [item.get("room_name") or ""]
            if item.get("updated"):
                when = datetime.fromtimestamp(item["updated"]).strftime("%Y-%m-%d %H:%M")
                parts.append(f"更新于 {when}")
            rows.append({
                "name": item.get("name") or "",
                "sub": " · ".join(p for p in parts if p),
                "percent": item.get("value"),
                "icon": icon_store.icon_path(item.get("model") or ""),
            })
        self._open_status_panel("耗材", f"{len(rows)} 项不足", [("", rows)])

    def _on_poll_tick(self) -> None:
        if self._poll_in_flight or not self._all_devices:
            return
        self._refresh_power_states(force=True)

    def _poll_dids(self) -> list[str]:
        """本轮该轮询开关状态的设备：当前可见设备 + 挂件上的设备。

        挂件是**独立于房间/家庭筛选**的常驻面板（见 _update_board_devices）。
        只按 `_visible_devices()` 轮询时，主界面切到别的房间后挂件里的设备
        就被排除在轮询之外——不是停止探测，而是"再也不刷新"：在米家 App
        里改了开关，挂件上一直显示切换房间那一刻的旧状态。
        """
        dids = [d.did for d in self._visible_devices() if d.online]
        if self._board is None:
            return dids
        extra = set(self._board.tracked_dids()) - set(dids)
        if not extra:
            return dids
        online = {d.did: d.online for d in self._all_devices}
        dids.extend(d for d in extra if online.get(d))
        return dids

    def _refresh_power_states(self, force: bool) -> None:
        """批量拉取需要关注的设备开关状态。

        force=False 只探测还没有结论的设备（首次/新设备）；
        force=True 忽略记忆全量重读（定时轮询）。
        离线设备的云端返回值是最后一次在线时的缓存，不可信，跳过。
        """
        # 以 DeviceInfo.online 为准：离线设备的云端开关值不可信
        dids = self._poll_dids()
        if not dids:
            return
        if not force:
            dids = [d for d in dids if d not in self._known_power]
            if not dids:
                return
        else:
            # 轮询仅针对已确认有开关能力的设备，避免无开关设备无效请求
            dids = [d for d in dids if self._known_power.get(d) is not None]
            if not dids:
                return
        self._poll_in_flight = True
        # 快照"操作版本号"：轮询回来时若某台设备已被用户操作过，
        # 说明这条结果比用户的操作旧，必须丢弃（见 _apply_power_states）
        snapshot = dict(self._power_gen)
        self._jobs.submit(
            lambda: self._service.power_states(dids),
            on_success=lambda states, snap=snapshot: self._apply_power_states(states, snap),
            on_error=self._on_poll_error,
        )

    def _on_poll_error(self, error: Exception) -> None:
        # 轮询失败不打断界面，但必须留痕：持续失败意味着网络/凭据异常
        self._poll_in_flight = False
        logger.warning("轮询设备开关状态失败: %s", error)

    def _bump_power_gen(self, did: str) -> None:
        """标记这台设备的开关状态被"本地操作"改过。

        用途：轮询是 5 秒一次的后台请求，点开关时可能已有一条旧结果在途。
        它回来会把刚翻转的按钮覆盖回旧值（用户看到"绿一下又变灰，
        过一会儿才真的变"）。子设备因为写入要两次请求（子 did 失败→父 did
        重试）耗时更长，这个回弹特别明显。
        """
        self._power_gen[did] = self._power_gen.get(did, 0) + 1

    def _on_board_power_changed(self, did: str, state) -> None:
        """挂件里点了开关：先记版本号再回写，避免在途轮询把状态盖回去。"""
        self._bump_power_gen(did)
        self._apply_power_state(did, state)

    def _apply_power_states(self, states: dict[str, bool | None],
                            snapshot: dict[str, int] | None = None) -> None:
        self._poll_in_flight = False
        stale = {did for did in states
                 if snapshot is not None
                 and snapshot.get(did, 0) != self._power_gen.get(did, 0)}
        if stale:
            # 这批结果里有些设备在请求发出后被操作过：丢弃它们的旧值，
            # 别覆盖用户刚看到的状态
            logger.debug("丢弃过期的开关轮询结果: %s", stale)
        for did, state in states.items():
            if did in stale:
                continue
            self._apply_power_state(did, state)
        self._maybe_localize_names()
        # 开关状态变了，开启的设备数要跟着更新
        self._update_count_label()
        self._update_tray_devices()
        # 挂件就地更新按钮状态（不整窗重建，避免每 5 秒一次重建抖动）
        if self._board is not None:
            try:
                self._board.apply_power_states(
                    {d: s for d, s in states.items() if d not in stale})
            except Exception:
                logger.warning("同步挂件开关状态失败", exc_info=True)

    def _apply_power_state(self, did: str, state: bool | None) -> None:
        # 离线设备的开关值不落记忆：云端返回的是最后一次在线时的缓存。
        # 但也不能写成 None——None 表示"确认没有开关能力"，轮询会永久跳过
        # 它，等设备重新上线也不会再探测，按钮就再也不出现了。
        # 所以离线时直接不更新，保留上一次已知状态（卡片本就按离线灰置）。
        dev = next((d for d in self._all_devices if d.did == did), None)
        if dev is not None and not dev.online:
            return
        self._known_power[did] = state
        card = self._cards.get(did)
        if card is not None and state is not None:
            card.set_power_state(state)
        # 明细面板开着时同步它里面的同款卡片：面板内的按钮是乐观翻转，
        # 这里用真实结果覆盖（下发失败会回滚成原状态）
        panel = getattr(self, "_status_panel", None)
        if panel is not None and shiboken6.isValid(panel) and state is not None:
            panel.update_power(did, state)

    def _refresh_metrics(self) -> None:
        """批量拉取卡片副标题的读数。

        默认只读「无开关能力」的设备（温湿度计这类），有开关的设备
        副标题保持纯房间名。但用户如果给某台设备指定了外显属性
        （插座改成功率），即使它有开关能力也要读，否则那个值永远
        不会刷新。
        """
        selections = settings_store.get_metrics_selection()
        visible = self._visible_devices()
        dids = [
            d.did for d in visible
            if self._known_power.get(d.did) is None or d.did in selections
        ]
        if not dids:
            return
        # 温湿度原始值单独收集：环境胶囊固定展示它们，不能从被用户
        # 改过的副标题文案里反推。候选集取整个可见范围，规格里没有
        # 温湿度属性的会被服务层跳过，不会带来额外请求
        climate: dict[str, dict[str, float]] = {}
        candidates = [d.did for d in visible]
        self._jobs.submit(
            lambda: self._service.read_metrics(
                dids, selections, climate, candidates),
            on_success=lambda metrics: self._apply_metrics(metrics, climate),
            on_error=lambda exc: logger.warning("拉取卡片读数失败: %s", exc),
        )

    def _apply_metrics(self, metrics: dict[str, str | None],
                       climate: dict[str, dict[str, float]] | None = None) -> None:
        if climate is not None:
            self._climate.update(climate)
        dirty = False
        for did, text in metrics.items():
            if text:
                if self._metrics.get(did) != text:
                    dirty = True
                self._metrics[did] = text
            elif did in self._metrics:
                self._metrics.pop(did, None)
                dirty = True
            card = self._cards.get(did)
            # 卡片可能在读数回来的路上随网格重建销毁，先确认对象还在
            if card is not None and shiboken6.isValid(card):
                card.set_metrics(text)
        if dirty and self._all_devices:
            device_cache.save(self._all_devices, self._known_power, self._metrics)
        self._maybe_localize_names()
        self._push_tray_metrics()
        # 温湿度读数变了，顶栏胶囊要跟着更新
        self._update_count_label()

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

    def _on_device_name_changed(self, did: str) -> None:
        """详情页改了设备名：同步设备对象并刷新所有展示入口。

        这里只改对应卡片的标题文字，不重建整个网格：改名信号来自
        详情页的信号栈，此刻删卡片会让正在处理点击的控件提前销毁，
        关闭详情页时容易连带崩溃。
        """
        device = next((d for d in self._all_devices if d.did == did), None)
        if device is None:
            return
        custom = name_override_store.get(did)
        # cloud_name 为空说明还没跑过 _apply_custom_names（旧缓存数据），
        # 此时先把当前名字当作默认名，避免恢复默认时无值可退
        if not device.cloud_name:
            device.cloud_name = device.name
        device.name = custom or device.cloud_name
        card = self._cards.get(did)
        # 卡片可能已随网格重建被销毁，先确认底层对象还在
        if card is not None and shiboken6.isValid(card):
            card.set_display_name(device.name)
        # 托盘与挂件是独立窗口，重建它们不涉及当前点击事件的控件
        self._update_tray_devices()
        self._update_board_devices()
        if self._all_devices:
            device_cache.save(self._all_devices, self._known_power, self._metrics)

    def _on_metrics_config_changed(self, did: str) -> None:
        """详情页改了卡片外显：清掉旧文案并按新选择重读一次。"""
        if self._metrics.pop(did, None) is not None:
            card = self._cards.get(did)
            if card is not None and shiboken6.isValid(card):
                card.set_metrics(None)
            # 缓存里也要同步清掉，否则下次启动会先把旧文案贴回卡片
            if self._all_devices:
                device_cache.save(
                    self._all_devices, self._known_power, self._metrics)
        self._refresh_metrics()

    def _on_detail_metrics(self, did: str, text: str) -> None:
        """详情页回读到温湿度后立即回写卡片与缓存（手动刷新路径）。

        用户给这台设备挑过别的外显属性（比如功率）时不回写：详情页
        始终只读温湿度，写回去会把卡片上的自定义值覆盖掉。
        """
        if not text:
            return
        # 用 in 判断而不是取值的真假：用户选择"不外显"时值是空列表，
        # 那也是自定义设置，不该被详情页回传的温湿度覆盖
        if did in settings_store.get_metrics_selection():
            return
        if self._metrics.get(did) == text:
            return
        self._metrics[did] = text
        card = self._cards.get(did)
        if card is not None:
            card.set_metrics(text)
        if self._all_devices:
            device_cache.save(self._all_devices, self._known_power, self._metrics)

    def _visible_devices(self) -> list[DeviceInfo]:
        return [
            d for d in self._all_devices
            if (self._current_home == _ALL_HOMES or d.home_name == self._current_home)
            and (self._current_room == _ALL_ROOMS or d.room_name == self._current_room)
            and (not self._hide_no_func or self._device_has_functions(d))
            and (not self._hide_offline or d.online)
        ]

    def _device_has_functions(self, device: DeviceInfo) -> bool:
        """None（spec 未拉取）视为有——等首轮轮询证实后再隐藏。"""
        state = self._service.spec_has_published_functions(device.spec_type)
        return state is not False

    def _displayed_devices(self) -> list[DeviceInfo]:
        """主页/托盘展示列表（含家庭/房间过滤与隐藏类开关）。"""
        out = self._all_devices
        if self._hide_no_func:
            out = [d for d in out if self._device_has_functions(d)]
        if self._hide_offline:
            out = [d for d in out if d.online]
        return out

    def _show_home_menu(self) -> None:
        if not self._homes:
            return
        # 原生 QMenu：文字左对齐、无多余滚动条，行为与系统菜单一致；
        # siui 的 SiRoundedMenu 是独立透明窗口还自带滚动条，观感不佳
        menu = QMenu(self)
        menu.setObjectName("appMenu")
        for home in self._homes:
            action = menu.addAction(home)
            action.setCheckable(True)
            action.setChecked(home == self._current_home)
            action.triggered.connect(lambda _, h=home: self._select_home(h))
        menu.exec(self._home_btn.mapToGlobal(
            self._home_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _select_home(self, home: str) -> None:
        if home == self._current_home:
            return
        self._current_home = home
        self._current_room = _ALL_ROOMS
        # 场景列表随家庭范围过滤，切家庭后旧缓存失效
        self._scenes = None
        self._home_btn.setText(f"{home} ▾")
        self._rebuild_tabs()
        self._rebuild_grid()
        self._animate_grid_in()
        self._update_count_label()
        self._refresh_power_states(force=False)
        self._refresh_metrics()

    def _animate_grid_in(self) -> None:
        """切换家庭/房间后卡片网格整体淡入（160ms），结束即移除效果。"""
        effect = QGraphicsOpacityEffect(self._grid_host)
        effect.setOpacity(0.0)
        self._grid_host.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        def _cleanup() -> None:
            # 快速连续切换时旧动画的 finished 会迟到，只清理仍属
            # 自己的效果，避免误删新一轮动画的透明度效果
            if self._grid_host.graphicsEffect() is effect:
                self._grid_host.setGraphicsEffect(None)

        anim.finished.connect(_cleanup)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_load_error(self, error: Exception) -> None:
        # 失败也要复位刷新状态，否则按钮一直禁用、只能重启才能再刷新
        self._loading_devices = False
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setToolTip("刷新设备")
        self._show_status("加载失败", 4000)
        QMessageBox.critical(self, "加载失败", str(error))

    # ---------- 房间 tab ----------

    def _rebuild_tabs(self) -> None:
        # 只清标签，保留右侧的排序按钮（它不属于标签集合）
        while self._tab_flow.count():
            item = self._tab_flow.takeAt(0)
            if widget := item.widget():
                # deleteLater 是延迟删除，旧按钮会和新按钮同帧显示；
                # 先隐藏掉，重建过程中不会出现重影
                widget.hide()
                widget.deleteLater()
        self._tab_buttons.clear()

        for room in self._ordered_tabs(self._natural_tabs()):
            btn = themed_tab_button(room)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, r=room: self._select_room(r))
            self._tab_flow.addWidget(btn)
            self._tab_buttons[room] = btn
        self._sync_tab_style()

    def _preferred_room(self) -> str:
        """启动后打开的标签：设置里指定的优先，已不存在时退回全屋。"""
        saved = settings_store.get_default_tab()
        if saved and saved in self._natural_tabs():
            return saved
        return _ALL_ROOMS

    def _natural_tabs(self) -> list[str]:
        """标签的自然顺序：场景、全屋，其余房间按名称排列。

        这是「没有自定义顺序」时的默认排法，恢复默认与新增标签兜底
        都以它为准。
        """
        return ([_SCENE_TAB] if self._show_scene_tab else []) + [_ALL_ROOMS] + sorted({
            d.room_name for d in self._all_devices
            if (self._current_home == _ALL_HOMES or d.home_name == self._current_home)
            and d.room_name and d.room_name != "未知"
        })

    @staticmethod
    def _ordered_tabs(tabs: list[str]) -> list[str]:
        """按保存的顺序排标签，新出现的追加到末尾。

        保存的顺序只记名字；换家庭后房间集合会变，没出现在记录里的
        标签保持默认顺序接在后面，不会因为排序丢失入口。
        """
        saved = settings_store.get_room_order()
        if not saved:
            return list(tabs)
        index = {name: pos for pos, name in enumerate(saved)}
        known = sorted((t for t in tabs if t in index), key=lambda t: index[t])
        rest = [t for t in tabs if t not in index]
        return known + rest

    def _make_tab_sort_button(self) -> QPushButton:
        """标签行最右端的排序入口。"""
        btn = QPushButton()
        # 这个入口嵌在标签行右侧、周围没有别的控件衬托，做太小很难注意到
        btn.setFixedSize(34, 34)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip("调整标签顺序")
        btn.setIconSize(QSize(26, 26))
        btn.setIcon(qta.icon("mdi.sort-variant", color=SiColors.TEXT_SECONDARY))
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " border-radius: 8px; }"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}")
        btn.clicked.connect(self._open_room_order)
        return btn

    def _open_room_order(self) -> None:
        """打开标签排序对话框，确定后立即重建标签行。"""
        from app.ui.room_order_dialog import RoomOrderDialog

        current = list(self._tab_buttons.keys())
        defaults = self._natural_tabs()
        dlg = RoomOrderDialog(current, defaults, self)
        accepted = dlg.exec() == RoomOrderDialog.DialogCode.Accepted
        order = dlg.result_order()
        restored = dlg.restored_default()
        default_tab = dlg.default_tab()
        dlg.deleteLater()
        if not accepted:
            return
        # 恢复默认时写空表，让新出现的房间继续按自然顺序排在后面；
        # 若把当时的顺序固化下来，以后新增房间就不会自动归位
        settings_store.set_room_order([] if restored else order)
        settings_store.set_default_tab(default_tab)
        self._rebuild_tabs()

    def _select_room(self, room: str) -> None:
        self._current_room = room
        self._sync_tab_style()
        self._rebuild_grid()
        self._animate_grid_in()
        if room == _SCENE_TAB:
            # 场景视图：有缓存先显示，后台刷新；无缓存时状态栏提示加载中
            if self._scenes is None:
                self._show_status("正在加载场景…")
            self._load_scenes()
            self._update_count_label()
            return
        self._refresh_power_states(force=False)
        # 切回设备视图刷新计数：场景模式的「共 N 个场景」必须换回设备计数
        self._update_count_label()

    def _sync_tab_style(self) -> None:
        for room, btn in self._tab_buttons.items():
            # siui 切换按钮的选中观感由 checked 状态自绘驱动
            if btn.isChecked() != (room == self._current_room):
                btn.setChecked(room == self._current_room)

    # ---------- 卡片网格 ----------

    def _rebuild_grid(self) -> None:
        # 窗口不可见时跳过实际构建（尺寸也尚未确定），留待 showEvent
        if not self.isVisible():
            self._grid_dirty = True
            self._grid_columns = 0
            return
        self._grid_dirty = False
        # 设备与场景两个视图共用网格：切换视图时旧卡片已从布局移除并
        # 排队销毁，若不清引用，下次重建会对已销毁的 C++ 对象再次
        # deleteLater 抛 RuntimeError，界面卡在当前视图无法切换
        for card in self._cards.values():
            card.deleteLater()
        self._cards.clear()
        for card in self._scene_cards.values():
            card.deleteLater()
        self._scene_cards.clear()
        # takeAt 会移除条目，count 随之递减，循环必然终止
        while self._grid.count():
            if widget := self._grid.takeAt(0).widget():
                widget.deleteLater()

        if self._current_room == _SCENE_TAB:
            self._rebuild_scene_grid()
            return

        visible = sorted(
            self._visible_devices(),
            key=lambda d: (0 if d.online else 1, d.name),
        )
        cols = self._columns_for_width(self._scroll.viewport().width())
        self._grid_columns = cols
        rows = (len(visible) + cols - 1) // cols
        for index, device in enumerate(visible):
            card = DeviceCard(device)
            # 记忆里的开关状态与读数立即回显，重建卡片不丢状态
            known = self._known_power.get(device.did)
            if known is not None:
                card.set_power_state(known)
            if device.did in self._metrics:
                card.set_metrics(self._metrics.get(device.did))
            # 已下载的图标从本地文件回填：主题/tab 切换重建卡片后
            # 图标不消失；未下载的由 _load_card_icons 后台补齐
            path = icon_store.icon_path(device.model)
            if path.is_file():
                card.set_icon(path)
            card.power_clicked.connect(self._on_power_clicked)
            card.open_requested.connect(self._on_open_device)
            self._cards[device.did] = card
            self._grid.addWidget(card, index // cols, index % cols)
        self._finalize_grid(cols, rows)

    def _rebuild_scene_grid(self) -> None:
        """场景视图网格：卡片尺寸与设备卡一致，列数换算直接复用。"""
        scenes = sorted(self._scenes or [], key=lambda s: s.name)
        if not scenes:
            # 空状态：居中提示（含「确认无场景」与加载失败两种来源）
            hint = QLabel("暂无场景，可在米家 APP「智能」中添加")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setFont(QFont("Microsoft YaHei UI", 12))
            hint.setStyleSheet(
                f"color: {SiColors.TEXT_MUTED}; background: transparent;")
            self._grid.addWidget(hint, 0, 0)
            return

        cols = self._columns_for_width(self._scroll.viewport().width())
        self._grid_columns = cols
        rows = (len(scenes) + cols - 1) // cols
        for index, scene in enumerate(scenes):
            card = SceneCard(scene)
            card.executed.connect(self._on_scene_executed)
            self._scene_cards[scene.scene_id] = card
            self._grid.addWidget(card, index // cols, index % cols)
        self._finalize_grid(cols, rows)

    def _finalize_grid(self, cols: int, rows: int) -> None:
        # 列宽不参与剩余空间分配、布局整体左对齐：卡片宽度固定，窗口
        # 加宽只应增加列数（_columns_for_width 已按此语义计算），若把
        # 剩余宽度均分给各列，卡片间距会随窗口宽度漂移，远超设定的
        # spacing；清理到历史行列上限，避免行列数回缩后旧 stretch 残留
        for col in range(max(cols, self._grid.columnCount())):
            self._grid.setColumnStretch(col, 0)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # 多余垂直空间全部推到最后一行之后，避免行距被均分拉大
        for row in range(max(rows, self._grid.rowCount())):
            self._grid.setRowStretch(row, 0)
        self._grid.setRowStretch(rows, 1)

    def _columns_for_width(self, width: int) -> int:
        # 固定卡片宽度，列数随可视宽度动态计算，避免缩窄时卡片被裁切
        from app.ui.device_card import _CARD_FIXED_WIDTH
        spacing = 14
        right_margin = 12
        eff = max(0, width - right_margin)
        if eff <= 0:
            return 1
        cols = (eff + spacing) // (_CARD_FIXED_WIDTH + spacing)
        # 不设单排上限——宽屏下应自然补列而不是拉开卡片间距
        return max(1, cols)

    @staticmethod
    def _default_window_width() -> int:
        """默认窗口逻辑宽：首屏恰好一排 5 个卡片。

        与 _columns_for_width 同一套列宽/间距/边距口径反推，再留一个
        间距的余量吸收取整与滚动条宽度差异。卡片宽度或边距调整时
        自动跟随，避免两处数值各自漂移。
        """
        from app.ui.device_card import _CARD_FIXED_WIDTH
        spacing = 14
        right_margin = 12
        content_margins = 24 * 2  # content 布局左右边距（构造处设置）
        eff = 5 * (_CARD_FIXED_WIDTH + spacing) - spacing
        return eff + right_margin + content_margins + spacing

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        super().showEvent(event)
        # 首次显示：应用默认窗口尺寸（宽度按逻辑像素取首屏一排 5 个
        # 卡片所需值，高度按 1200 物理像素折算），并居中于所在屏幕的
        # 可用区域。首次启动时 DPR 读数不可靠（曾导致尺寸异常），故
        # 延迟到 showEvent——此时原生窗口已创建、读数准确。之后的
        # 托盘唤出保持上次位置
        if not self._default_size_applied:
            self._default_size_applied = True
            dpr = self.devicePixelRatioF() or 1.0
            self.resize(self._default_window_width(), round(1200 / dpr))
            from PySide6.QtGui import QGuiApplication
            screen = self.screen() or QGuiApplication.primaryScreen()
            avail = screen.availableGeometry()
            self.move(avail.center().x() - self.width() // 2,
                      avail.center().y() - self.height() // 2)
        # 托盘常驻启动/唤出：隐藏期间积压的网格重建在此执行
        if self._grid_dirty:
            self._rebuild_grid()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        super().resizeEvent(event)
        # 系统缩放切换时 Qt 对无边框窗口的尺寸重算可能失控（窗口被
        # 成倍放大超出屏幕），每次尺寸变化都钳制回所在屏幕的可用区域
        self._clamp_to_screen()
        # 缩放（DPR）变化：Qt 保持物理尺寸导致逻辑尺寸被重算——恢复
        # 变化前的逻辑尺寸，让窗口与界面元素同步缩放
        dpr = self.devicePixelRatioF() or 1.0
        if (self._last_dpr is not None and self._expected_logical is not None
                and abs(dpr - self._last_dpr) > 1e-9
                and not self.isMaximized() and not self.isMinimized()):
            w, h = self._expected_logical
            self._expected_logical = None
            QTimer.singleShot(0, lambda: (self.resize(w, h), self._clamp_to_screen()))
            self._last_dpr = dpr
        else:
            self._expected_logical = (self.width(), self.height())
            self._last_dpr = dpr
        # 拖动窗口时 resizeEvent 每秒触发几十次，全部重建卡片网格代价
        # 太高；防抖等布局稳定，列数真的变化时才重建
        self._resize_timer.start()
        self._voice_fab.reposition()

    def _clamp_to_screen(self) -> None:
        """把窗口尺寸/位置钳制回所在屏幕的可用工作区。

        覆盖两类异常：系统缩放切换后窗口异常放大；分辨率切换后窗口
        大半落在屏幕外。最大化/最小化状态不干预。
        """
        if self.isMaximized() or self.isMinimized():
            return
        screen = self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        w, h = self.width(), self.height()
        if w > avail.width() or h > avail.height():
            self.resize(min(w, avail.width()), min(h, avail.height()))
        # 位置 sanity：窗口完全落在屏幕外时拉回可见区域
        g = self.geometry()
        if (g.right() < avail.left() or g.left() > avail.right()
                or g.bottom() < avail.top() or g.top() > avail.bottom()):
            self.move(avail.left() + 12, avail.top() + 12)

    def _on_resize_settled(self) -> None:
        # 按当前视图取卡片集合：空态（无卡片）无需重排
        cards = self._scene_cards if self._current_room == _SCENE_TAB else self._cards
        if not cards:
            return
        if self._columns_for_width(self._scroll.viewport().width()) != self._grid_columns:
            self._rebuild_grid()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名约定)
        # 视口宽度变化的第二信号源（第一是窗口 resizeEvent）：滚动条
        # 显隐、布局瀑布等因素会让视口宽度晚于窗口稳定，此时复用同一
        # 防抖与列数校验，保证网格列数与最终视口宽度一致
        if (obj is self._scroll.viewport()
                and event.type() == QEvent.Type.Resize
                and self.isVisible()):
            self._resize_timer.start()
        return super().eventFilter(obj, event)

    # ---------- 快速开关 ----------

    def _on_power_clicked(self, did: str) -> None:
        card = self._cards.get(did)
        if card is None:
            return
        current = self._known_power.get(did)
        if current is None:
            # 状态未知：只能让服务端读一次再写，仍按用户操作优先级排队
            self._bump_power_gen(did)
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
        self._bump_power_gen(did)
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
        self._bump_power_gen(did)
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
            self._bump_power_gen(did)
            self._known_power[did] = revert_to
            if shiboken6.isValid(card):
                card.set_power_state(revert_to)
            if self._board is not None:
                self._board.apply_power_states({did: revert_to})
        if not shiboken6.isValid(card):
            return
        card.set_busy(False)
        QMessageBox.warning(self, "操作失败", str(error))

    # ---------- 场景（手动控制） ----------

    def _load_scenes(self) -> None:
        """拉取当前范围的场景列表；失败保留已有缓存。"""
        if self._loading_scenes:
            return
        self._loading_scenes = True
        self._jobs.submit(
            lambda: self._service.scene_list(
                None if self._current_home == _ALL_HOMES else self._current_home),
            on_success=self._on_scenes_loaded,
            on_error=self._on_scenes_failed,
        )

    def _on_scenes_loaded(self, scenes: list[SceneInfo]) -> None:
        self._loading_scenes = False
        self._scenes = scenes
        if self._current_room == _SCENE_TAB:
            self._rebuild_grid()
            self._update_count_label()

    def _on_scenes_failed(self, error: Exception) -> None:
        # 网络失败不打断界面：保留已有缓存，仅提示
        self._loading_scenes = False
        logger.warning("加载场景失败: %s", error)
        if self._current_room != _SCENE_TAB:
            # 预取失败且用户不在场景视图：静默；进场景页时会重新拉取
            return
        if self._scenes is None:
            self._show_status("场景加载失败", 4000)
        else:
            Toast.info(self, f"场景刷新失败：{error}", 4000)

    def _on_scene_executed(self, scene_id: str) -> None:
        scene = next((s for s in (self._scenes or []) if s.scene_id == scene_id), None)
        card = self._scene_cards.get(scene_id)
        if scene is None or card is None:
            return
        card.set_busy(True)
        self._jobs.submit(
            lambda s=scene: self._service.run_scene(s),
            on_success=lambda _, s=scene, c=card: self._on_scene_done(s, c),
            on_error=lambda e, s=scene, c=card: self._on_scene_failed(s, c, e),
        )

    def _on_scene_done(self, scene: SceneInfo, card: SceneCard) -> None:
        # 卡片可能在任务排队期间随网格重建被销毁，回调前先确认存活
        if not shiboken6.isValid(card):
            return
        card.set_busy(False)
        Toast.info(self, f"已执行场景「{scene.name}」", 2500)

    def _on_scene_failed(self, scene: SceneInfo, card: SceneCard, error: Exception) -> None:
        if not shiboken6.isValid(card):
            return
        card.set_busy(False)
        Toast.info(self, f"执行场景「{scene.name}」失败：{error}", 4000)

    # ---------- 详情 ----------

    def _on_open_device(self, did: str) -> None:
        device = next((d for d in self._all_devices if d.did == did), None)
        if device is None:
            return
        dialog = DeviceDetailDialog(self._service, self._jobs, device, self)
        # 详情内取到温湿度后直接回写卡片与缓存（手动刷新的即时反馈）
        dialog.panel.metrics_updated.connect(self._on_detail_metrics)
        dialog.panel.name_changed.connect(self._on_device_name_changed)
        dialog.panel.metrics_config_changed.connect(self._on_metrics_config_changed)
        dialog.load()
        dialog.exec()
        # 面板可能随关闭流程提前释放，断开信号前先确认对象还在
        if shiboken6.isValid(dialog.panel):
            dialog.panel.name_changed.disconnect(self._on_device_name_changed)
            dialog.panel.metrics_config_changed.disconnect(
                self._on_metrics_config_changed)
            dialog.panel.metrics_updated.disconnect(self._on_detail_metrics)
        dialog.deleteLater()
        # 详情期间可能在面板里改过开关，关闭后读一次回填卡片；
        # 无开关能力的设备返回 None 直接跳过
        if did in self._cards:
            self._jobs.submit(
                lambda: self._service.power_state(did),
                on_success=lambda state, d=did: self._apply_power_state(d, state),
                on_error=lambda exc: logger.warning("详情关闭后回读开关失败: %s", exc),
            )

    # ---------- 资源 ----------

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        # 托盘常驻时关闭窗口仅隐藏到托盘，不退出（除非走托盘"退出"）
        # 用户可在设置中关闭此行为，此时直接退出
        from app.core.settings_store import get_minimize_to_tray
        if get_minimize_to_tray() and not self._force_quit and self._tray is not None:
            # 隐藏窗口前确保托盘图标可见，常驻运行等待托盘重新呼出
            try:
                self._tray.set_tray_visible(True)
            except Exception:
                pass
            event.ignore()
            self.hide()
            # 常驻托盘态把物理页交还系统，任务管理器占用显著下降
            from app import trim_working_set
            trim_working_set()
            return
        # 真正退出
        if self._all_devices:
            device_cache.save(self._all_devices, self._known_power, self._metrics)
        if self._tray is not None:
            try:
                self._tray.hide_quick()
                self._tray._tray.hide()
            except Exception:
                pass
        # 桌面挂件：先把位置与透明度落盘再销毁，避免退出时丢摆放
        if self._board is not None:
            try:
                self._board.hide()
            except Exception:
                pass
        # 定时扫描随进程退出停止；规则本身已落盘，下次启动继续
        try:
            self._schedule_timer.stop()
        except Exception:
            pass
        self._jobs.shutdown()
        self._web_jobs.shutdown()
        super().closeEvent(event)
        # quitOnLastWindowClosed 为 False（托盘常驻需要），关闭窗口不会自动
        # 退出事件循环；关闭托盘设置时需显式退出让进程真正结束
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()

