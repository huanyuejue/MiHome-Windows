# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""桌面挂件的管理对话框：勾选设备 + 拖拽排序 + 列数 / 透明度 / 背景色 / 置顶。

设备勾选与拖拽排序整体复用托盘管理对话框（TrayManagerDialog）的
_DraggableRow 实现——两处管理交互完全一致，不重复造轮子；本文件
只额外承担 Board 独有的四项：每行列数、透明度、背景色、窗口置顶。

透明度、列数、背景色、置顶都是**拖动/点击即生效**（所见即所得），点「取消」
也会保留这些调整；点「保存」只决定设备清单与顺序是否写入。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core import board_store
from app.core.models import DeviceInfo
from app.ui.overlay_dialog import OverlayDialog
from app.ui.si_theme import SiColors
from app.ui.tray.draggable_row import _DraggableRow

_COL_MIN, _COL_MAX = 1, 6
_OPACITY_MIN, _OPACITY_MAX = 30, 100  # 滑块刻度用整数百分比

# 预设底色：黑 / 深灰 / 石墨 / 白 / 米白 / 墨蓝。
# 前三个是"常驻桌面不刺眼"的主流选择，白色给浅色桌面，米白/墨蓝给配色党。
_PRESET_BG = (
    ("纯黑", "#000000"),
    ("深夜灰", "#1e1f22"),
    ("石墨", "#2c2e33"),
    ("墨蓝", "#16202e"),
    ("白", "#ffffff"),
    ("米白", "#f5f1e8"),
)
_SWATCH = 26  # 色块边长


class BoardManagerDialog(OverlayDialog):
    """挂件设备管理：勾选 + 拖拽排序 + 每行列数 + 透明度 + 背景色 + 置顶。"""

    # 实时预览：拖动/点击过程中直接改 Board 窗口
    opacity_preview = Signal(float)
    columns_preview = Signal(int)
    # None = 跟随主题；其它为 "#rrggbb"
    background_preview = Signal(object)
    topmost_preview = Signal(bool)
    lock_preview = Signal(bool)

    def __init__(self, devices: list[DeviceInfo], parent=None, scenes=None):
        # 与托盘管理同款观感：铺满窗口的可拖拽面板，无遮罩
        super().__init__(parent, overlay=False)
        self.setWindowTitle("管理桌面挂件")
        self._drag_did: str | None = None
        # 当前选中的底色：None=跟随主题。色块与取色器共用这一个状态
        self._bg: str | None = board_store.get_bg()
        # 可添加的场景：调用方传入（主窗口缓存/在线拉的）；None 表示暂无
        self._scenes_available: list[dict] = list(scenes or [])
        # 场景行在 _rows/_checks 里的 key 前缀，避免与设备 did 冲突
        self._SCENE_PREFIX = "scene:"
        # 先建空的勾选/行表：列数分区在设备列表之前构建，其提示文案
        # 需要按已勾选数量计算，构造期必须能安全查空表
        self._checks: dict[str, QPushButton] = {}
        self._rows: dict[str, _DraggableRow] = {}
        self._scene_tags: list[QLabel] = []
        self.setAcceptDrops(True)

        lay = QVBoxLayout(self._panel)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(12)

        # ---- 标题栏（整条可拖动窗口） ----
        title_bar = QFrame(self._panel)
        title_bar.setObjectName("boardTitleBar")
        title_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        title_bar.setStyleSheet("QFrame#boardTitleBar { background: transparent; }")
        title_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        self._title_bar = title_bar
        self._header_drag_pos = None
        title_bar.installEventFilter(self)
        header = QHBoxLayout(title_bar)
        header.setContentsMargins(4, 2, 4, 2)
        header.setSpacing(8)
        header_title = QLabel("管理桌面挂件")
        header_title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.DemiBold))
        header_title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        header.addWidget(header_title)
        header.addStretch(1)
        header.addWidget(self._make_close_button())
        lay.addWidget(title_bar)

        # ---- 两个页签：设备（主任务）/ 外观（设置）----
        # 之前四个设置分区把 660px 高的面板吃掉一大半，设备列表只剩两行可看。
        # 拆页后设备页把整块空间让给列表（十来行），外观页也不用挤在小空间里。
        lay.addWidget(self._make_tab_bar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._make_device_page())
        self._stack.addWidget(self._make_style_page())
        lay.addWidget(self._stack, stretch=1)
        self._set_page(0)

        selected = board_store.load_devices()
        selected_scenes = board_store.load_scenes()
        order = board_store.load_order()
        dev_map = {d.did: d for d in devices}
        scene_map = {s["scene_id"]: s for s in selected_scenes}
        placed: set[tuple[str, str]] = set()
        # ① 已选项按 board.json 的混排顺序（场景/设备混在一起）
        for entry in order:
            t, i = entry.get("type"), entry.get("id")
            if t == "device" and i in dev_map and i in selected:
                self._add_row(dev_map[i], True)
                placed.add(("device", i))
            elif t == "scene" and i in scene_map:
                self._add_scene_row(scene_map[i], True)
                placed.add(("scene", i))
        # ② 顺序表遗漏的已选设备（旧数据兜底），离线也保留：
        #    否则保存时会被静默丢掉
        for did in selected:
            if ("device", did) not in placed and did in dev_map:
                self._add_row(dev_map[did], True)
                placed.add(("device", did))
        # ③ 未选的在线设备（离线的不进候选，纯噪音）
        for dev in sorted([d for d in devices
                           if d.did not in selected and d.online], key=lambda d: d.name):
            self._add_row(dev, False)
        # ④ 未选的场景（全部列出：场景没有在线概念）
        for scene in sorted(self._scenes_available, key=lambda s: s.get("name", "")):
            if ("scene", scene["scene_id"]) not in placed:
                self._add_scene_row(scene, False)
        self._list_lay.addStretch(1)
        # 行已建好，重算列数提示（构造期构建列数分区时列表还是空的）
        self._refresh_columns_ui()

        lay.addWidget(self._make_button_row())

    # ---------- 页签与两个页面 ----------

    def _make_tab_bar(self) -> QWidget:
        """设备 / 外观 两个分段按钮，切换 QStackedWidget。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._tab_buttons: list[QPushButton] = []
        for idx, text in enumerate(("设备", "外观")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _c=False, i=idx: self._set_page(i))
            self._tab_buttons.append(btn)
            row.addWidget(btn)
        # 页面在 _stack 建好后才切换（_set_page 要用到 _stack 与 _subtitle）
        return holder

    def _set_page(self, index: int) -> None:
        index = 0 if index < 0 else min(index, len(self._tab_buttons) - 1)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)
            btn.setStyleSheet(self._tab_qss(i == index))
        self._stack.setCurrentIndex(index)
        # subtitle 只在设备页有意义；外观页不留空行
        self._subtitle.setVisible(index == 0)

    def _tab_qss(self, active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: {SiColors.THEME}; border: none;"
                " border-radius: 8px; color: #0b0b0e; font-weight: 600;"
                " font-size: 9pt; }")
        return (
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 8px; color: {SiColors.TEXT_PRIMARY}; font-size: 9pt; }}"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}")

    def _make_device_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        self._subtitle = QLabel("勾选设备/场景；按住 ⠿ 拖动排序")
        self._subtitle.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 9pt;")
        box.addWidget(self._subtitle)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索设备 / 场景 / 房间…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(28)
        self._search.setStyleSheet(self._search_qss())
        self._search.textChanged.connect(self._apply_filter)
        box.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._host = host
        self._list_lay = QVBoxLayout(host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(8)
        scroll.setWidget(host)
        box.addWidget(scroll, stretch=1)
        host.setAcceptDrops(True)
        scroll.viewport().setAcceptDrops(True)
        self._scroll = scroll
        return page

    def _make_style_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        box.addWidget(self._make_columns_section())
        box.addWidget(self._make_opacity_section())
        box.addWidget(self._make_background_section())
        box.addWidget(self._make_topmost_section())
        box.addWidget(self._make_lock_section())
        # 本页所有改动都即时生效，容易让人以为要按「保存」——
        # 一句话说清「保存」只管设备清单
        self._style_hint = QLabel("以上改动点击即生效，无需点「保存」；「保存」只保存挂件内容")
        self._style_hint.setWordWrap(True)
        self._style_hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        box.addWidget(self._style_hint)
        box.addStretch(1)
        return page

    @staticmethod
    def _search_qss() -> str:
        return (
            f"QLineEdit {{ background: {SiColors.SURFACE}; border: 1px solid {SiColors.LINE};"
            f" border-radius: 8px; padding: 2px 10px; color: {SiColors.TEXT_PRIMARY};"
            " font-size: 9pt; }"
            f"QLineEdit:focus {{ border-color: {SiColors.THEME}; }}")

    # ---------- 列表构建 ----------

    def _add_row(self, dev: DeviceInfo, checked: bool) -> None:
        row = _DraggableRow(dev.did)
        row.setObjectName("boardRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setFixedHeight(48)
        row.setMaximumWidth(290)
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._reset_row_style(row)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 8, 16, 8)
        self._add_grip(rl)
        name = QLabel(f"{dev.name}  ·  {dev.room_name}")
        name.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY if dev.online else SiColors.OFFLINE_TEXT};"
            " background: transparent;")
        rl.addWidget(name, stretch=1)
        btn = QPushButton("✓" if checked else "+")
        btn.setFixedSize(28, 28)
        btn.setCheckable(True)
        btn.setChecked(checked)
        # 文字必须随勾选态同步：QSS 只管背景色，固定文字会绿底加号/暗底对勾
        btn.toggled.connect(lambda c, b=btn: b.setText("✓" if c else "+"))
        # 勾选数量变了，「N 个设备 → M 行」的提示要跟着变
        btn.toggled.connect(lambda _c: self._refresh_columns_ui())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 14px; color: {SiColors.TEXT_PRIMARY}; }}"
            "QPushButton:checked { background: #3dbba4; color: #0b0b0e; }"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}"
            "QPushButton:checked:hover { background: #4ccdb5; }")
        self._checks[dev.did] = btn
        rl.addWidget(btn)
        self._rows[dev.did] = row
        # 搜索匹配用：设备名 + 房间名，大小写不敏感
        row._search_text = f"{dev.name} {dev.room_name}".lower()
        # 已选但因离线只出现在列表里的设备，标出来免得以为它在线
        row._is_offline = not dev.online
        row.dragStarted.connect(self._on_drag_start)
        row.dragFinished.connect(self._on_drag_finish)
        self._list_lay.addWidget(row)

    def _add_scene_row(self, scene: dict, checked: bool) -> None:
        """场景行：与设备行同构，标题后带「场景」标记，key 用 scene: 前缀。"""
        sid = str(scene.get("scene_id") or "")
        key = self._SCENE_PREFIX + sid
        row = _DraggableRow(key)
        row.setObjectName("boardRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setFixedHeight(48)
        row.setMaximumWidth(290)
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._reset_row_style(row)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 8, 16, 8)
        self._add_grip(rl)
        home = str(scene.get("home_name") or "")
        label = QLabel(f"{scene.get('name') or '场景'}  ·  {home or '手动场景'}")
        label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        rl.addWidget(label, stretch=1)
        tag = QLabel("场景")
        tag.setStyleSheet(
            f"color: {SiColors.THEME}; background: transparent;"
            " border: 1px solid " + SiColors.THEME + "; border-radius: 6px;"
            " padding: 0 4px; font-size: 8pt;")
        rl.addWidget(tag)
        self._scene_tags.append(tag)
        btn = QPushButton("✓" if checked else "+")
        btn.setFixedSize(28, 28)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.toggled.connect(lambda c, b=btn: b.setText("✓" if c else "+"))
        btn.toggled.connect(lambda _c: self._refresh_columns_ui())
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 14px; color: {SiColors.TEXT_PRIMARY}; }}"
            "QPushButton:checked { background: #3dbba4; color: #0b0b0e; }"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}"
            "QPushButton:checked:hover { background: #4ccdb5; }")
        self._checks[key] = btn
        rl.addWidget(btn)
        self._rows[key] = row
        row._search_text = f"{scene.get('name') or ''} {home} 场景".lower()
        row._is_offline = False
        row._is_scene = True
        row._scene = scene
        row.dragStarted.connect(self._on_drag_start)
        row.dragFinished.connect(self._on_drag_finish)
        self._list_lay.addWidget(row)

    def _add_grip(self, layout) -> None:
        """行首拖拽把手：明确告诉用户"这一行可以拖"。"""
        grip = QLabel("⠿")
        grip.setFixedWidth(10)
        grip.setStyleSheet(
            f"color: {SiColors.TEXT_MUTED}; background: transparent;"
            " font-size: 9pt;")
        grip.setToolTip("按住这一行上下拖动可调整顺序")
        grip.setCursor(Qt.CursorShape.OpenHandCursor)
        layout.addWidget(grip)

    def _apply_filter(self, text: str) -> None:
        """按关键字过滤设备行。

        只隐藏行、不动勾选状态：过滤期间取消勾选别的设备不会误伤；
        拖拽命中也一并跳过隐藏行（见 _hit_test）。
        """
        keyword = (text or "").strip().lower()
        for row in self._rows.values():
            row.setVisible(not keyword or keyword in row._search_text)
        # 拖拽中的源行被过滤掉时，别留下半截拖拽状态
        if self._drag_did is not None and not self._rows[self._drag_did].isVisible():
            self._drag_did = None

    # ---------- 列数与透明度 ----------

    def _make_columns_section(self) -> QWidget:
        """每行列数：分段按钮组（1–6），选中即预览。"""
        box = QFrame()
        box.setObjectName("boardSection")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QFrame#boardSection {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        # 标题行右侧放「N 个设备 → M 行」：按钮行只有 6 个按钮就占满宽度，
        # 提示放同一行会被挤断（实测在 340px 面板下只剩「5 个设备 →」）
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel("一行放多少个")
        title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent; font-size: 9pt;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._columns_hint = QLabel()
        self._columns_hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        title_row.addWidget(self._columns_hint)
        lay.addLayout(title_row)
        row = QHBoxLayout()
        row.setSpacing(4)
        self._col_buttons: list[QPushButton] = []
        self._columns = board_store.get_columns()
        for n in range(_COL_MIN, _COL_MAX + 1):
            btn = QPushButton(str(n))
            btn.setFixedSize(34, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, v=n: self._set_columns(v))
            self._col_buttons.append(btn)
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        self._refresh_columns_ui()
        return box

    def _set_columns(self, value: int) -> None:
        self._columns = value
        board_store.set_columns(value)
        self._refresh_columns_ui()
        self.columns_preview.emit(self._columns)

    def _refresh_columns_ui(self) -> None:
        # 注意：非 f-string 的字面段里不能写 {{ }}，否则样式表括号不配平
        active = (
            f"QPushButton {{ background: {SiColors.THEME}; border: none;"
            " border-radius: 8px; color: #0b0b0e; font-weight: 600; }")
        idle = (
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 8px; color: {SiColors.TEXT_PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}")
        for i, btn in enumerate(self._col_buttons):
            btn.setStyleSheet(active if i + _COL_MIN == self._columns else idle)
        n = len(self.selected_dids()) + len(self.selected_scenes())
        if n == 0:
            self._columns_hint.setText("尚未添加内容")
        else:
            rows = -(-n // max(1, self._columns))
            self._columns_hint.setText(f"{n} 项 → {rows} 行")

    def _make_opacity_section(self) -> QWidget:
        """透明度：滑块拖动即实时改 Board 窗口（所见即所得）。"""
        box = QFrame()
        box.setObjectName("boardSection")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QFrame#boardSection {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("透明度")
        title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent; font-size: 9pt;")
        head.addWidget(title)
        self._opacity_hint = QLabel()
        self._opacity_hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        head.addStretch(1)
        head.addWidget(self._opacity_hint)
        lay.addLayout(head)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(_OPACITY_MIN, _OPACITY_MAX)
        value = board_store.get_opacity()
        self._opacity_slider.setValue(round(value * 100))
        self._opacity_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        lay.addWidget(self._opacity_slider)
        self._refresh_opacity_hint(self._opacity_slider.value())
        return box

    def _on_opacity_changed(self, value: int) -> None:
        self._refresh_opacity_hint(value)
        # 落盘由对话框负责（与列数一致），预览信号只驱动挂件窗口，
        # 这样即使挂件创建失败/已隐藏，透明度的设置也不会丢
        board_store.set_opacity(value / 100.0)
        self.opacity_preview.emit(value / 100.0)

    def _refresh_opacity_hint(self, value: int) -> None:
        self._opacity_hint.setText(
            f"{value}%　鼠标移上去自动恢复不透明" if value < 100 else "100%")

    def opacity(self) -> float:
        return self._opacity_slider.value() / 100.0

    def columns(self) -> int:
        return self._columns

    # ---------- 背景色 ----------

    def _make_section_box(self) -> tuple[QFrame, QVBoxLayout]:
        """分区外框（与列数/透明度两个分区同款），返回框与内部布局。"""
        box = QFrame()
        box.setObjectName("boardSection")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QFrame#boardSection {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        return box, lay

    def _make_background_section(self) -> QWidget:
        """背景色：标题行放「跟随主题 / 自定义颜色…」，下面一行预设色块。

        这样只占两行：之前三种控件挤一行会把「自定义颜色…」压成「定义」，
        拆成两行又白白多出一行高度。
        """
        box, lay = self._make_section_box()
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("背景色")
        title.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent; font-size: 9pt;")
        head.addWidget(title)

        self._bg_chips: list[QPushButton] = []
        # 索引 0 固定是「跟随主题」：refresh 时按它判断当前是否为自动模式
        auto = self._make_text_chip("跟随主题", "深浅色主题切换时挂件自动适配")
        auto.clicked.connect(lambda: self._choose_bg(None))
        self._bg_chips.append(auto)
        head.addWidget(auto)
        custom = self._make_text_chip("自定义…", "打开取色器，选任意颜色")
        custom.clicked.connect(self._choose_custom_bg)
        head.addWidget(custom)
        head.addStretch(1)
        self._bg_hint = QLabel()
        self._bg_hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        head.addWidget(self._bg_hint)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        for label, color in _PRESET_BG:
            chip = self._make_color_chip(color, label)
            chip.clicked.connect(lambda _c=False, v=color: self._choose_bg(v))
            row.addWidget(chip)
            self._bg_chips.append(chip)
        row.addStretch(1)
        lay.addLayout(row)
        self._refresh_bg_ui()
        return box

    def _make_color_chip(self, color: str, tip: str) -> QPushButton:
        chip = QPushButton()
        chip.setCheckable(True)
        chip.setFixedSize(_SWATCH, _SWATCH)
        chip.setToolTip(tip)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setStyleSheet(
            f"QPushButton {{ background: {color}; border: 2px solid {SiColors.LINE};"
            " border-radius: 13px; }"
            f"QPushButton:checked {{ border: 2px solid {SiColors.THEME}; }}")
        return chip

    def _make_text_chip(self, text: str, tip: str) -> QPushButton:
        chip = QPushButton(text)
        chip.setCheckable(True)
        chip.setFixedHeight(_SWATCH)
        chip.setToolTip(tip)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setStyleSheet(self._text_chip_qss())
        return chip

    @staticmethod
    def _text_chip_qss() -> str:
        return (
            f"QPushButton {{ background: {SiColors.SURFACE}; border: 2px solid transparent;"
            " border-radius: 13px; padding: 0 10px; font-size: 8pt;"
            f" color: {SiColors.TEXT_PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}"
            f"QPushButton:checked {{ border: 2px solid {SiColors.THEME}; }}")

    def _choose_bg(self, color: str | None) -> None:
        """选中某个底色：立刻落盘并预览到真实挂件窗口。"""
        self._bg = color
        board_store.set_bg(color)
        self._refresh_bg_ui()
        self.background_preview.emit(color)

    def _choose_custom_bg(self) -> None:
        initial = QColor(self._bg) if self._bg else QColor(SiColors.WINDOW_BG)
        picked = QColorDialog.getColor(initial, self, "选择挂件背景色")
        if picked.isValid():
            self._choose_bg(picked.name())

    def _refresh_bg_ui(self) -> None:
        # 自动 + 预设色块按当前值勾选；自定义颜色时所有色块都不选中，
        # 提示里显示具体色号（用户自己挑的颜色不在预设里）
        current = (self._bg or "").lower()
        for chip in self._bg_chips:
            chip.setChecked(False)
        if self._bg is None:
            self._bg_chips[0].setChecked(True)
            self._bg_hint.setText("跟随主题")
        else:
            for idx, (_label, color) in enumerate(_PRESET_BG, start=1):
                if color.lower() == current:
                    self._bg_chips[idx].setChecked(True)
                    break
            self._bg_hint.setText(current)

    # ---------- 窗口置顶 ----------

    def _make_topmost_section(self) -> QWidget:
        """置顶勾选框：不写解释文字（用户都懂"置顶"），说明放进 tooltip。"""
        box, lay = self._make_section_box()
        self._topmost_check = QCheckBox("窗口置顶")
        self._topmost_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._topmost_check.setChecked(board_store.get_topmost())
        self._topmost_check.setToolTip(
            "关闭（默认）：会被其他窗口盖住；开启：始终浮在最上层")
        self._topmost_check.setStyleSheet(
            f"QCheckBox {{ color: {SiColors.TEXT_PRIMARY}; background: transparent;"
            " font-size: 9pt; }"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;"
            f" border: 1px solid {SiColors.LINE}; background: {SiColors.SURFACE}; }}"
            f"QCheckBox::indicator:checked {{ background: {SiColors.THEME};"
            f" border-color: {SiColors.THEME}; }}")
        self._topmost_check.toggled.connect(self._on_topmost_toggled)
        lay.addWidget(self._topmost_check)
        return box

    def _make_lock_section(self) -> QWidget:
        """锁定：隐藏标题栏、禁止拖动、形态固定（解锁入口在挂件上/右键/托盘）。"""
        box, lay = self._make_section_box()
        self._lock_check = QCheckBox("锁定挂件（隐藏标题栏、固定位置）")
        self._lock_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_check.setChecked(board_store.get_locked())
        self._lock_check.setToolTip(
            "锁定后不能拖动、不能收起/展开；鼠标移到挂件上会浮出小锁，"
            "右键菜单与托盘菜单里也能解锁")
        self._lock_check.setStyleSheet(
            f"QCheckBox {{ color: {SiColors.TEXT_PRIMARY}; background: transparent;"
            " font-size: 9pt; }"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;"
            f" border: 1px solid {SiColors.LINE}; background: {SiColors.SURFACE}; }}"
            f"QCheckBox::indicator:checked {{ background: {SiColors.THEME};"
            f" border-color: {SiColors.THEME}; }}")
        self._lock_check.toggled.connect(self._on_lock_toggled)
        lay.addWidget(self._lock_check)
        return box

    def _on_lock_toggled(self, checked: bool) -> None:
        board_store.set_locked(checked)
        self.lock_preview.emit(bool(checked))

    def locked(self) -> bool:
        return self._lock_check.isChecked()

    def _on_topmost_toggled(self, checked: bool) -> None:
        board_store.set_topmost(checked)
        self.topmost_preview.emit(bool(checked))

    def topmost(self) -> bool:
        return self._topmost_check.isChecked()

    # ---------- 底部按钮 ----------

    @staticmethod
    def _plain_btn_qss(primary: bool) -> str:
        if primary:
            return (
                f"QPushButton {{ background: {SiColors.THEME}; border: none;"
                " border-radius: 8px; padding: 6px 16px; color: #0b0b0e; font-weight: 600; }"
                f"QPushButton:hover {{ background: {SiColors.THEME_HOVER}; }}")
        return (
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 8px; padding: 6px 16px; color: {SiColors.TEXT_PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}")

    def _make_button_row(self) -> QWidget:
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(self._plain_btn_qss(False))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(self._plain_btn_qss(True))
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self._cancel_btn = cancel
        self._ok_btn = ok
        holder = QWidget()
        holder.setLayout(row)
        return holder

    # ---------- 拖拽排序（逻辑与托盘管理一致） ----------

    def _reset_row_style(self, row) -> None:
        row.setStyleSheet(
            f"QFrame#boardRow {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}"
            f"QFrame#boardRow:hover {{ background: {SiColors.CARD_HOVER}; }}")

    def _on_drag_start(self, did: str) -> None:
        self._drag_did = did

    def _on_drag_finish(self) -> None:
        self._drag_did = None
        self._reset_all_rows()

    def _reset_all_rows(self) -> None:
        for row in self._rows.values():
            self._reset_row_style(row)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if (e.mimeData().hasText() and e.mimeData().text() in self._rows) or self._drag_did:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasText() and e.mimeData().text() in self._rows:
            self._drag_did = e.mimeData().text()
        if not self._drag_did:
            e.ignore()
            return
        hovered = self._hit_test(self._map_to_host(e.position().toPoint()))
        for did, row in self._rows.items():
            if did == hovered and did != self._drag_did:
                row.setStyleSheet(
                    f"QFrame#boardRow {{ background: {SiColors.CARD_HOVER};"
                    " border: 2px solid #3dbba4; border-radius: 10px; }}")
            else:
                self._reset_row_style(row)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e) -> None:  # noqa: N802
        self._reset_all_rows()
        super().dragLeaveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        self._reset_all_rows()
        did_text = e.mimeData().text() if e.mimeData().hasText() else self._drag_did
        if not did_text or did_text not in self._rows:
            e.ignore()
            return
        self._drag_did = did_text
        self._apply_drop(did_text, self._map_to_host(e.position().toPoint()))
        self._drag_did = None
        e.acceptProposedAction()

    def _map_to_host(self, pos):
        mapped = self._host.mapFrom(self, pos)
        if self._host.rect().contains(mapped):
            return mapped
        viewport = self._scroll.viewport()
        return self._host.mapFrom(viewport, viewport.mapFrom(self, pos))

    def _apply_drop(self, did: str, pos) -> None:
        src = self._rows[did]
        target_did = self._hit_test(pos)
        if target_did == did:
            # 原地放下：hit_test 会命中自身，不动才不会误判为拖到末尾
            return
        if not target_did:
            self._list_lay.removeWidget(src)
            src.show()
            self._list_lay.insertWidget(max(0, self._list_lay.count() - 1), src)
            return
        dst = self._rows[target_did]
        # 先移除源行再取目标索引：removeWidget 会让后续条目前移
        self._list_lay.removeWidget(src)
        dst_idx = self._list_lay.indexOf(dst)
        if dst_idx < 0:
            return
        src.show()
        self._list_lay.insertWidget(dst_idx, src)

    def _hit_test(self, pos) -> str | None:
        for did, row in self._rows.items():
            # 隐藏行（被搜索过滤掉）不参与命中，否则会拖到看不见的设备上
            if row.isVisible() and row.geometry().contains(pos):
                return did
        return None

    # ---------- 结果 ----------

    def _selected_keys(self) -> list[str]:
        """按当前布局顺序返回已勾选的 key（设备 did / scene:<sid>）。"""
        result = []
        layout = getattr(self, "_list_lay", None)
        checks = getattr(self, "_checks", None)
        if layout is None or checks is None:
            return result
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item else None
            if w is None:
                continue
            key = getattr(w, "_did", None)
            if key and checks.get(key) is not None and checks[key].isChecked():
                result.append(key)
        return result

    def selected_dids(self) -> list[str]:
        """按布局顺序返回已勾选的设备 did（场景排除在外）。"""
        return [k for k in self._selected_keys() if not k.startswith(self._SCENE_PREFIX)]

    def selected_scenes(self) -> list[dict]:
        """按布局顺序返回已勾选的场景快照（与 board.json 同结构）。"""
        result = []
        for key in self._selected_keys():
            if key.startswith(self._SCENE_PREFIX):
                row = self._rows.get(key)
                scene = getattr(row, "_scene", None)
                if scene:
                    result.append(dict(scene))
        return result

    def ordered_items(self) -> list[dict]:
        """设备与场景的混排顺序（写入 board.json 的 order）。"""
        out = []
        for key in self._selected_keys():
            if key.startswith(self._SCENE_PREFIX):
                out.append({"type": "scene", "id": key[len(self._SCENE_PREFIX):]})
            else:
                out.append({"type": "device", "id": key})
        return out

    # ---------- 窗口与拖拽 ----------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._title_bar and event.type() in (
            event.Type.MouseButtonPress, event.Type.MouseMove,
            event.Type.MouseButtonRelease,
        ):
            if event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._header_drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == event.Type.MouseMove:
                if self._header_drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._header_drag_pos)
                return True
            if event.type() == event.Type.MouseButtonRelease:
                self._header_drag_pos = None
                return False
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._header_drag_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._header_drag_pos = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._header_drag_pos = None
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._panel.setGeometry(self.rect())

    def retheme(self) -> None:
        """主题切换：面板内所有内联样式都是构造期求值，须逐控件重设。

        分组重设而非遍历 findChildren：勾选钮、列数钮、底部按钮三套
        观感不同，统一套一种会把列数高亮和保存按钮刷错。
        """
        super().retheme()
        self._reset_all_rows()
        self._search.setStyleSheet(self._search_qss())
        for btn in self._checks.values():
            btn.setText("✓" if btn.isChecked() else "+")
            btn.setStyleSheet(
                f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
                f" border-radius: 14px; color: {SiColors.TEXT_PRIMARY}; }}"
                "QPushButton:checked { background: #3dbba4; color: #0b0b0e; }"
                f"QPushButton:hover {{ background: {SiColors.BTN_HOVER}; }}"
                "QPushButton:checked:hover { background: #4ccdb5; }")
        self._refresh_columns_ui()
        for label in (self._columns_hint, self._opacity_hint, self._bg_hint,
                      self._style_hint):
            label.setStyleSheet(
                f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        self._subtitle.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 9pt;")
        for i, btn in enumerate(self._tab_buttons):
            btn.setStyleSheet(self._tab_qss(i == self._stack.currentIndex()))
        for box in self.findChildren(QFrame):
            if box.objectName() == "boardSection":
                box.setStyleSheet(
                    f"QFrame#boardSection {{ background: {SiColors.CARD};"
                    f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}")
        # 背景色块与置顶勾选框的内联样式同样要重刷（色块内容色不变）
        for chip in self._bg_chips:
            if chip.text():
                chip.setStyleSheet(self._text_chip_qss())
        for tag in self._scene_tags:
            tag.setStyleSheet(
                f"color: {SiColors.THEME}; background: transparent;"
                f" border: 1px solid {SiColors.THEME}; border-radius: 6px;"
                " padding: 0 4px; font-size: 8pt;")
        self._lock_check.setStyleSheet(
            f"QCheckBox {{ color: {SiColors.TEXT_PRIMARY}; background: transparent;"
            " font-size: 9pt; }"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;"
            f" border: 1px solid {SiColors.LINE}; background: {SiColors.SURFACE}; }}"
            f"QCheckBox::indicator:checked {{ background: {SiColors.THEME};"
            f" border-color: {SiColors.THEME}; }}")
        self._topmost_check.setStyleSheet(
            f"QCheckBox {{ color: {SiColors.TEXT_PRIMARY}; background: transparent;"
            " font-size: 9pt; }"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px;"
            f" border: 1px solid {SiColors.LINE}; background: {SiColors.SURFACE}; }}"
            f"QCheckBox::indicator:checked {{ background: {SiColors.THEME};"
            f" border-color: {SiColors.THEME}; }}")
        for btn in (self._cancel_btn, self._ok_btn):
            btn.setStyleSheet(self._plain_btn_qss(btn is self._ok_btn))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 设置项挪进「外观」页后，设备页可以把整块高度让给列表
        self._center_on_screen(360, 620)
        self.raise_()
        self._fade_in()