# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""设置窗口：无边框可拖拽面板 + 背部暗色遮罩，与设备详情页同款观感。

设置项按「主题界面 / 应用功能」两分类展示：标题下方的横向 tab 切换
分类，内容区切页时做整体淡入过渡（与主页切换房间同款），避免直接
替换带来的生硬变化。
"""

from PySide6.QtCore import QEvent, QPropertyAnimation, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core import settings_store
from app.core.models import is_speaker
from app.ui.overlay_dialog import OverlayDialog
from app.ui.si_theme import (
    SiColors,
    apply_combo_qss,
    themed_combo,
    themed_switch,
    themed_tab_button,
)

# 下拉文案 -> 设置值
_THEME_MODE_LABELS = {"system": "跟随系统", "light": "浅色模式", "dark": "深色模式"}
_THEME_LABEL_TO_MODE = {v: k for k, v in _THEME_MODE_LABELS.items()}

_TAB_APPEARANCE, _TAB_FEATURES = 0, 1
_TAB_TITLES = ("主题界面", "应用功能")
# 切页淡入时长：与主页房间切换同款
_FADE_MS = 160


def _sync_switch(switch) -> None:
    """siui 开关进度同步：setChecked 后须手动对齐动画内部 current。"""
    switch.progress = 1 if switch.isChecked() else 0
    switch.progress_ani.fromProperty()


class _PagesHost(QWidget):
    """叠放两页的容器：尺寸变化时把两页同步铺满，保持完全重叠。

    两页刻意不进布局而是绝对定位，同一时刻只有一页可见，切换即
    hide/show + 新页淡入。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        self._pages: list[QWidget] = []

    def add_page(self, page: QWidget) -> None:
        page.setParent(self)
        self._pages.append(page)
        page.setGeometry(self.rect())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        for page in self._pages:
            page.setGeometry(self.rect())


class SettingsDialog(OverlayDialog):
    """设置对话框：暗色遮罩 + 居中圆角面板，可拖拽。"""

    def __init__(self, parent=None, devices=None):
        super().__init__(parent)
        self._devices = devices or []
        self.setWindowTitle("设置")
        # 尺寸由 showEvent 按主窗口显隐决定：可见时覆盖主窗口，隐藏时铺满屏幕
        self._header_drag_pos = None
        self._current_tab = _TAB_APPEARANCE

        # ---- 圆角面板：居中显示 ----
        # 直接用基类的 overlayPanel 对象名与配套样式；改名会让
        # 基类样式表的选择器对不上，面板退化为透明底
        panel = self._panel
        # 分类展示后单项行更少，面板放大到主窗口约八成，减少滚动
        if parent is not None:
            pw = max(720, int(parent.width() * 0.78))
            ph = max(520, int(parent.height() * 0.82))
        else:
            pw, ph = 720, 520
        panel.setFixedSize(pw, ph)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(20, 14, 20, 18)
        lay.setSpacing(12)

        # ---- 标题栏：可拖拽 ----
        title_bar = QFrame(panel)
        title_bar.setObjectName("settingsTitleBar")
        title_bar.setStyleSheet("QFrame#settingsTitleBar { background: transparent; }")
        title_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        self._title_bar = title_bar
        title_bar.installEventFilter(self)

        header = QHBoxLayout(title_bar)
        header.setContentsMargins(4, 2, 4, 2)
        header.setSpacing(8)
        self._title_label = QLabel("设置")
        self._title_label.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.DemiBold))
        header.addWidget(self._title_label)
        header.addStretch(1)
        lay.addWidget(title_bar)

        # ---- 分类 tab：标题下方靠左横向排布 ----
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(4, 0, 4, 0)
        tab_row.setSpacing(8)
        self._tab_buttons: list = []
        for index, title in enumerate(_TAB_TITLES):
            btn = themed_tab_button(title)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.clicked.connect(lambda _, i=index: self._switch_tab(i))
            tab_row.addWidget(btn)
            self._tab_buttons.append(btn)
        tab_row.addStretch(1)
        lay.addLayout(tab_row)

        # ---- 内容区：两页叠放，靠动画切隐 ----
        self._pages_host = _PagesHost(panel)
        lay.addWidget(self._pages_host, stretch=1)

        self._appearance_scroll = self._build_appearance_page()
        self._features_scroll = self._build_features_page()
        self._pages_host.add_page(self._appearance_scroll)
        self._pages_host.add_page(self._features_scroll)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._done_btn = QPushButton("完成")
        self._done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._done_btn.clicked.connect(self._save_and_accept)
        btn_row.addWidget(self._done_btn)
        lay.addLayout(btn_row)

        # 全部内联样式集中一处：构造与主题切换（retheme）共用
        self._apply_styles()
        self._on_tray_toggled(self._tray_toggle.isChecked())
        self._apply_voice_fab_state(self._has_speaker)
        self._apply_speaker_state(self._has_speaker)
        self._apply_autostart_state(self._autostart_supported)
        self._sync_tab_style()
        self._show_tab(_TAB_APPEARANCE, animated=False)

    # ---------- 页面构建 ----------

    def _make_item(self, title: str, desc: str):
        """设置项卡片外壳：返回 (卡片, 标题, 描述, 行布局)，控件由调用方塞入行。"""
        item = QFrame()
        row = QHBoxLayout(item)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(4)
        texts.addStretch(1)
        label = QLabel(title)
        texts.addWidget(label)
        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        texts.addWidget(desc_label)
        texts.addStretch(1)
        row.addLayout(texts, stretch=1)
        return item, label, desc_label, row

    def _build_scroll(self, items: list[QFrame]) -> QScrollArea:
        """一组设置项卡片装进滚动区：项数增多/高缩放时滚动代替裁切。"""
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        body = QVBoxLayout(host)
        body.setContentsMargins(8, 6, 8, 0)
        body.setSpacing(8)
        for item in items:
            body.addWidget(item)
        body.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #35363f; border-radius: 3px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: none; }")
        scroll.setWidget(host)
        return scroll

    def _build_appearance_page(self) -> QScrollArea:
        """主题界面：主题配色、界面缩放比例、小爱悬浮对话按钮。"""
        # ── 主题配色下拉（跟随系统 / 浅色 / 深色） ──
        self._theme_item, self._theme_label, self._theme_desc, theme_row = self._make_item(
            "主题配色",
            "切换界面明暗配色；「跟随系统」随 Windows 深浅色模式自动变化")
        self._original_mode = settings_store.get_theme_mode()
        self._pending_mode = self._original_mode
        self._theme_combo = themed_combo(
            [_THEME_MODE_LABELS[m] for m in ("system", "light", "dark")],
            current=_THEME_MODE_LABELS[self._pending_mode])
        self._theme_combo.currentTextChanged.connect(self._on_theme_selected)
        theme_row.addWidget(self._theme_combo)

        # ── 界面缩放比例（高 DPI 屏幕微调；更改后重启生效） ──
        self._scale_item, self._scale_label, self._scale_desc, scale_row = self._make_item(
            "界面缩放比例",
            "调整界面整体元素大小（在系统缩放之上叠加），适用于高 DPI 屏幕使用"
            "较高系统缩放时觉得界面偏大的情况；可从下拉选档位或直接键入任意"
            "百分比，更改后需重启应用生效")
        self._original_ui_scale = settings_store.get_ui_scale()
        self._pending_ui_scale = self._original_ui_scale
        # 可编辑下拉：预设档位 + 直接键入任意百分比（无级调整，50-200）
        scale_labels = [f"{round(s * 100):d}%" for s in sorted(settings_store.UI_SCALES)]
        current_pct = f"{round(self._pending_ui_scale * 100):g}%"
        self._scale_combo = themed_combo(scale_labels, current=current_pct, editable=True)
        # 输入校验：不设 validator——任何 validator 都会在校验中间态时
        # 干扰增量输入（QDoubleValidator 范围校验拒绝"1"、"12"这类中间值，
        # 正是「打字没反应」的根因）。输入完全自由，非法值在提交时
        # _on_scale_edited 用 float() 解析并回显兜底。
        # 聚焦自动全选已由 themed_combo 内部 _SelectAllLineEdit 处理。
        # 回车/失焦提交解析；选档位用 activated（仅用户从弹出列表点选时
        # 触发，不会因输入过程中 currentText 变化而误触发覆盖输入）
        self._scale_combo.lineEdit().returnPressed.connect(self._on_scale_edited)
        self._scale_combo.lineEdit().editingFinished.connect(self._on_scale_edited)
        self._scale_combo.activated.connect(self._on_scale_selected)
        scale_row.addWidget(self._scale_combo)

        # ── 小爱同学悬浮对话按钮 ──
        self._has_speaker = any(is_speaker(d) and d.online for d in self._devices)
        self._fab_item, self._fab_label, self._fab_desc, fab_row = self._make_item(
            "小爱同学悬浮对话按钮",
            "启用位于主界面右下角的小爱同学对话悬浮按钮（需设备里有小爱音箱）")
        self._voice_fab_toggle = themed_switch()
        self._voice_fab_toggle.setChecked(settings_store.get_voice_fab_enabled())
        _sync_switch(self._voice_fab_toggle)
        fab_row.addWidget(self._voice_fab_toggle)

        return self._build_scroll([self._theme_item, self._scale_item, self._fab_item])

    def _build_features_page(self) -> QScrollArea:
        """应用功能：开机自启动、默认指挥的音箱、系统托盘、托盘方式启动、隐藏无功能设备。"""
        # ── 开机自启动（写注册表 HKCU Run，默认关闭） ──
        self._autostart_item, self._autostart_label, self._autostart_desc, autostart_row = \
            self._make_item("开机自启动", "")
        self._autostart_toggle = themed_switch()
        # 自启动仅构建版支持：开发模式置灰并提示，保存时清理残留注册项
        self._autostart_supported = settings_store.autostart_supported()
        if self._autostart_supported:
            self._autostart_desc.setText(
                "开启后，Windows 登录时自动启动米家（可与「以系统托盘方式启动」搭配静默运行）")
            self._autostart_toggle.setChecked(settings_store.get_autostart())
        else:
            self._autostart_desc.setText(
                "仅构建版（build.ps1 产物 dist/MiHome-Windows.exe）支持；当前为源码运行模式，"
                "保存设置时将清除残留的自启动注册项")
        _sync_switch(self._autostart_toggle)
        self._autostart_toggle.setEnabled(self._autostart_supported)
        autostart_row.addWidget(self._autostart_toggle)

        # ── 默认输出音箱（小爱指令发往哪台音箱） ──
        self._speaker_item, self._speaker_label, self._speaker_desc, speaker_row = self._make_item(
            "默认指挥的音箱",
            "小爱语音指令默认发往的音箱；选择「自动」时使用设备列表中第一个在线音箱")
        # 全部音箱（含离线，离线时运行中自动回退）；在线优先排序
        speakers = sorted(
            (d for d in self._devices if is_speaker(d)),
            key=lambda d: (0 if d.online else 1, d.name, d.did),
        )
        self._speaker_options: list[tuple[str, str]] = [
            ("", "自动（第一个在线音箱）"),
        ] + [(d.did, f"{d.name}（{d.room_name}）") for d in speakers]
        current_did = settings_store.get_default_speaker_did()
        cur_label = next(
            (label for did, label in self._speaker_options if did == current_did),
            self._speaker_options[0][1],
        )
        self._speaker_combo = themed_combo(
            [label for _, label in self._speaker_options], current=cur_label)
        # 音箱名含房间号可能较长：按最长选项文本自适应宽度
        # （字体度量 + 箭头/内边距余量），避免选项被裁切
        fm = self._speaker_combo.fontMetrics()
        option_labels = [label for _, label in self._speaker_options]
        text_w = max((fm.horizontalAdvance(t) for t in option_labels), default=120)
        self._speaker_combo.setFixedWidth(min(max(text_w + 48, 150), 300))
        speaker_row.addWidget(self._speaker_combo)

        # ── 带快捷操作面板的系统托盘 ──
        self._tray_item, self._tray_label, self._tray_desc, tray_row = self._make_item(
            "带快捷操作面板的系统托盘",
            "开启后，关闭主窗口时将最小化到系统托盘并启用托盘快捷操作面板")
        self._tray_toggle = themed_switch()
        self._tray_toggle.setChecked(settings_store.get_minimize_to_tray())
        _sync_switch(self._tray_toggle)
        tray_row.addWidget(self._tray_toggle)
        self._tray_toggle.toggled.connect(self._on_tray_toggled)

        # ── 以系统托盘方式启动（子设置，依赖父开关） ──
        self._start_min_item, self._start_min_label, self._start_min_desc, start_min_row = \
            self._make_item(
                "以系统托盘方式启动",
                "开启后，启动软件时将以系统托盘的方式静默启动，不唤出主界面（该功能需开启系统托盘功能可选）")
        self._start_min_toggle = themed_switch()
        self._start_min_toggle.setChecked(settings_store.get_start_minimized())
        _sync_switch(self._start_min_toggle)
        start_min_row.addWidget(self._start_min_toggle)

        # ── 隐藏无可控制功能的设备 ──
        self._hide_item, self._hide_label, self._hide_desc, hide_row = self._make_item(
            "隐藏无可控制功能的设备",
            "无公开功能规格或规格无属性的设备（如部分蓝牙类产品）将不在主页显示")
        self._hide_toggle = themed_switch()
        self._hide_toggle.setChecked(settings_store.get_hide_no_func_devices())
        _sync_switch(self._hide_toggle)
        hide_row.addWidget(self._hide_toggle)

        # ── 自动检测新版本 ──
        self._update_item, self._update_label, self._update_desc, update_row = self._make_item(
            "自动检测新版本",
            "开启时，会在每次启动应用时自动从 Github 仓库获取新版本信息，"
            "有则提示更新")
        self._update_toggle = themed_switch()
        self._update_toggle.setChecked(settings_store.get_check_update_enabled())
        _sync_switch(self._update_toggle)
        update_row.addWidget(self._update_toggle)

        return self._build_scroll([
            self._autostart_item, self._speaker_item, self._tray_item,
            self._start_min_item, self._hide_item, self._update_item,
        ])

    # ---------- 分类切换 ----------

    def _sync_tab_style(self) -> None:
        for index, btn in enumerate(self._tab_buttons):
            # siui 切换按钮的选中观感由 checked 状态自绘驱动
            if btn.isChecked() != (index == self._current_tab):
                btn.setChecked(index == self._current_tab)

    def _switch_tab(self, index: int) -> None:
        same = (index == self._current_tab)
        self._current_tab = index
        # 先纠正 checked：SiToggleButtonRefactor 可点击取消选中，
        # 反复点同一 tab 会让高亮丢失，这里无条件重新同步
        self._sync_tab_style()
        if same:
            return
        self._show_tab(index, animated=True)

    def _show_tab(self, index: int, animated: bool) -> None:
        """切页：直接替换后新页整体淡入（与主页房间切换同款观感）。"""
        pages = (self._appearance_scroll, self._features_scroll)
        pages[1 - index].hide()
        incoming = pages[index]
        # 清掉可能残留的旧效果，避免快速连续切换时叠在新一轮动画上
        incoming.setGraphicsEffect(None)
        incoming.show()
        if not animated:
            return
        effect = QGraphicsOpacityEffect(incoming)
        effect.setOpacity(0.0)
        incoming.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(_FADE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _cleanup() -> None:
            # 快速连续切换时旧动画的 finished 会迟到，只清理仍属
            # 自己的效果，避免误删新一轮动画的透明度效果
            if incoming.graphicsEffect() is effect:
                incoming.setGraphicsEffect(None)

        anim.finished.connect(_cleanup)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    # ---------- 样式 ----------

    def _apply_styles(self) -> None:
        """主题相关内联样式：构造与 retheme 共用。"""
        panel_card = f"QFrame {{ background: {SiColors.CARD}; border-radius: 10px; }}"
        for item in (self._tray_item, self._start_min_item, self._fab_item,
                     self._theme_item, self._autostart_item, self._speaker_item,
                     self._hide_item, self._scale_item, self._update_item):
            item.setStyleSheet(panel_card)
            # 高度按内容自适应（不固定）：长描述换行后行自然变高，
            # 不会被固定 64px 裁掉；短描述保持紧凑。QScrollArea 负责
            # 项数多/高缩放时的整体滚动。
            item.setMinimumHeight(0)
        self._title_label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        for label in (self._tray_label, self._start_min_label, self._fab_label,
                      self._theme_label, self._autostart_label, self._speaker_label,
                      self._hide_label, self._scale_label, self._update_label):
            label.setStyleSheet(
                f"color: {SiColors.TEXT_PRIMARY}; background: transparent; font-size: 10pt;")
        for desc in (self._tray_desc, self._start_min_desc, self._fab_desc,
                     self._theme_desc, self._autostart_desc, self._speaker_desc,
                     self._hide_desc, self._scale_desc, self._update_desc):
            desc.setStyleSheet(
                f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 7pt;")
        self._done_btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.THEME}; border: none; border-radius: 8px; "
            f"padding: 7px 18px; color: {SiColors.ON_THEME_TEXT}; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {SiColors.THEME_HOVER}; }}")
        apply_combo_qss(self._theme_combo)
        self._theme_combo.set_arrow_color(SiColors.TEXT_SECONDARY)
        # 界面缩放下拉同样随主题刷新（遗漏曾致切主题后仍保持旧深色样式）
        apply_combo_qss(self._scale_combo, editable=True)
        self._scale_combo.set_arrow_color(SiColors.TEXT_SECONDARY)
        # 分类 tab 颜色随主题刷新（style_data 是构造期求值的内联色）
        for btn in self._tab_buttons:
            from PySide6.QtGui import QColor
            btn.style_data.button_color = QColor("#00" + SiColors.CARD[1:])
            btn.style_data.text_color = QColor(SiColors.TEXT_SECONDARY)
            btn.style_data.toggled_button_color = QColor(SiColors.THEME)
            btn.style_data.toggled_text_color = QColor(SiColors.ON_THEME_TEXT)
            btn.style_data.hover_color = QColor("#1a" + SiColors.THEME[1:])
            btn.style_data.idle_color = QColor("#00" + SiColors.THEME[1:])
            btn.style_data.click_color = QColor("#40" + SiColors.THEME[1:])
            btn.reloadStyleData()

    def _apply_autostart_state(self, supported: bool) -> None:
        """开发模式下灰置自启动行（开关透明度 + 文字变灰）。"""
        color = SiColors.TEXT_PRIMARY if supported else SiColors.TEXT_DISABLED
        desc_color = SiColors.TEXT_SECONDARY if supported else SiColors.TEXT_FAINT
        self._autostart_label.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 10pt;")
        self._autostart_desc.setStyleSheet(
            f"color: {desc_color}; background: transparent; font-size: 7pt;")
        if supported:
            self._autostart_toggle.setGraphicsEffect(None)
        else:
            eff = QGraphicsOpacityEffect(self._autostart_toggle)
            eff.setOpacity(0.35)
            self._autostart_toggle.setGraphicsEffect(eff)
            self._autostart_toggle.setChecked(False)

    def retheme(self) -> None:
        """主题切换：重设面板底色、全部内联样式与联动灰置态。"""
        super().retheme()
        self._apply_styles()
        self._on_tray_toggled(self._tray_toggle.isChecked())
        self._apply_voice_fab_state(self._has_speaker)
        self._apply_speaker_state(self._has_speaker)
        self._apply_autostart_state(self._autostart_supported)

    def _on_theme_selected(self, text: str) -> None:
        """下拉选择即预览生效（宿主主窗口负责应用与重建）。"""
        mode = _THEME_LABEL_TO_MODE.get(text, "system")
        if mode == self._pending_mode:
            return
        self._pending_mode = mode
        win = self.parent()
        if hasattr(win, "apply_theme_mode"):
            win.apply_theme_mode(mode)

    def _on_scale_selected(self, index: int) -> None:
        """用户从下拉点选预设档位（activated 信号）：更新值并回显。"""
        combo = self._scale_combo
        if index < 0 or index >= combo.count():
            return
        text = combo.itemText(index)
        try:
            value = float(text.strip().rstrip("%")) / 100.0
        except ValueError:
            return
        self._pending_ui_scale = value
        # blockSignals：避免 setCurrentText 再触发信号递归
        combo.blockSignals(True)
        combo.setCurrentText(self._scale_pct_text(value))
        combo.blockSignals(False)

    def _scale_pct_text(self, value: float) -> str:
        """缩放值(小数) → 百分比显示文本，保留小数但去尾零（137.5→137.5%, 1.0→100%）。"""
        return f"{value * 100:g}%"

    def _on_scale_edited(self) -> None:
        """键入完成后规范化显示：非法值回显、超范围钳制。"""
        combo = self._scale_combo
        raw = combo.currentText().strip().rstrip("%")
        if not raw:
            return
        try:
            value = float(raw) / 100.0
        except ValueError:
            # 非法输入回显当前值
            combo.blockSignals(True)
            combo.setCurrentText(self._scale_pct_text(self._pending_ui_scale))
            combo.blockSignals(False)
            return
        # 钳制到允许范围（设置页 50%-200%；超出按边界）
        low, high = settings_store._UI_SCALE_MIN, settings_store._UI_SCALE_MAX
        value = min(max(value, low), high)
        # 值未变（如回显规范化）直接返回，避免 setCurrentText 递归
        if abs(value - self._pending_ui_scale) < 1e-9 and \
                combo.currentText() == self._scale_pct_text(value):
            return
        self._pending_ui_scale = value
        # 回显规范化文本；blockSignals 避免 setCurrentText 再触发
        # _on_scale_selected 用舍入值覆盖刚保存的精确值（如 137.5→138）
        combo.blockSignals(True)
        combo.setCurrentText(self._scale_pct_text(value))
        combo.blockSignals(False)

    def _save_and_accept(self) -> None:
        settings_store.set_minimize_to_tray(self._tray_toggle.isChecked())
        # 子开关仅在父开关开启时有效，关闭时强制写入 False
        if self._tray_toggle.isChecked():
            settings_store.set_start_minimized(self._start_min_toggle.isChecked())
        else:
            settings_store.set_start_minimized(False)
        # 默认输出音箱：下拉文案反查 did；无音箱时被灰置为「自动」存空串
        idx = self._speaker_combo.currentIndex()
        if 0 <= idx < len(self._speaker_options):
            settings_store.set_default_speaker_did(self._speaker_options[idx][0])
        # 语音浮浮球仅在带设备上下文时落盘：设备列表为空（如启动早期
        # 打开设置）时 has_speaker 恒 False 会强制取消勾选，若照常
        # 落盘会把用户已开启的设置静默抹成关闭
        if self._devices:
            settings_store.set_voice_fab_enabled(self._voice_fab_toggle.isChecked())
        settings_store.set_hide_no_func_devices(self._hide_toggle.isChecked())
        settings_store.set_check_update_enabled(self._update_toggle.isChecked())
        settings_store.set_theme_mode(self._pending_mode)
        # 界面缩放：记录是否变化，供保存后提示重启
        old_scale = self._original_ui_scale
        self._scale_changed = abs(self._pending_ui_scale - old_scale) >= 1e-6
        settings_store.set_ui_scale(self._pending_ui_scale)
        # 开机自启动写注册表：失败不阻断其余设置保存。
        # 开发模式开关已置灰为关，此处顺带清掉历史残留的无效注册项
        try:
            settings_store.set_autostart(self._autostart_toggle.isChecked())
        except OSError:
            pass
        self.accept()

    def done(self, result) -> None:  # noqa: N802
        # 取消时还原为打开前的主题（选择时已即时预览）
        if result == QDialog.DialogCode.Rejected and self._pending_mode != self._original_mode:
            self._pending_mode = self._original_mode
            win = self.parent()
            if hasattr(win, "apply_theme_mode"):
                win.apply_theme_mode(self._original_mode)
        super().done(result)

    def _apply_speaker_state(self, has_speaker: bool) -> None:
        """有在线音箱时可选择；无则灰置下拉并强制「自动」。"""
        self._speaker_combo.setEnabled(has_speaker)
        color = SiColors.TEXT_PRIMARY if has_speaker else SiColors.TEXT_DISABLED
        desc_color = SiColors.TEXT_SECONDARY if has_speaker else SiColors.TEXT_FAINT
        self._speaker_label.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 10pt;")
        self._speaker_desc.setStyleSheet(
            f"color: {desc_color}; background: transparent; font-size: 7pt;")
        if has_speaker:
            self._speaker_combo.setGraphicsEffect(None)
        else:
            eff = QGraphicsOpacityEffect(self._speaker_combo)
            eff.setOpacity(0.35)
            self._speaker_combo.setGraphicsEffect(eff)
            self._speaker_combo.setCurrentIndex(0)

    def _apply_voice_fab_state(self, has_speaker: bool) -> None:
        """有音箱时可交互，无音箱时灰置且强制关闭。"""
        self._voice_fab_toggle.setEnabled(has_speaker)
        label_color = SiColors.TEXT_PRIMARY if has_speaker else SiColors.TEXT_DISABLED
        desc_color = SiColors.TEXT_SECONDARY if has_speaker else SiColors.TEXT_FAINT
        self._fab_label.setStyleSheet(
            f"color: {label_color}; background: transparent; font-size: 10pt;")
        self._fab_desc.setStyleSheet(
            f"color: {desc_color}; background: transparent; font-size: 7pt;")
        if has_speaker:
            self._voice_fab_toggle.setGraphicsEffect(None)
        else:
            eff = QGraphicsOpacityEffect(self._voice_fab_toggle)
            eff.setOpacity(0.35)
            self._voice_fab_toggle.setGraphicsEffect(eff)
            self._voice_fab_toggle.setChecked(False)

    def _on_tray_toggled(self, enabled: bool) -> None:
        """父开关变化时启用/禁用子设置项。"""
        self._start_min_toggle.setEnabled(enabled)
        label_color = SiColors.TEXT_PRIMARY if enabled else SiColors.TEXT_DISABLED
        desc_color = SiColors.TEXT_SECONDARY if enabled else SiColors.TEXT_FAINT
        self._start_min_label.setStyleSheet(
            f"color: {label_color}; background: transparent; font-size: 10pt;")
        self._start_min_desc.setStyleSheet(
            f"color: {desc_color}; background: transparent; font-size: 7pt;")
        # SiSwitchRefactor 自绘不响应 setEnabled，用透明度灰化开关
        if enabled:
            self._start_min_toggle.setGraphicsEffect(None)
        else:
            eff = QGraphicsOpacityEffect(self._start_min_toggle)
            eff.setOpacity(0.35)
            self._start_min_toggle.setGraphicsEffect(eff)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fill_parent_window():
            # 主界面可见：覆盖主窗口，遮罩只遮住主窗口区域
            self.raise_()
            self._fade_in()
            return
        # 托盘触发且主窗口隐藏：铺满可用屏幕，面板可在整个屏幕内拖动
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        self.raise_()
        self._fade_in()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 遮罩层铺满整个窗口
        self._place_overlay()
        # 面板居中放置
        pw, ph = self._panel.width(), self._panel.height()
        x = (self.width() - pw) // 2
        y = (self.height() - ph) // 2
        self._panel.move(x, y)

    # ---------- 拖拽 ----------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._header_drag_pos = None
        super().mousePressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._title_bar and event.type() in (
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    # 拖拽移动面板（非整个对话框），对话框铺满屏幕不动
                    self._header_drag_pos = event.globalPosition().toPoint() - self._panel.pos()
                return True
            if event.type() == QEvent.Type.MouseMove:
                if self._header_drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    self._panel.move(event.globalPosition().toPoint() - self._header_drag_pos)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._header_drag_pos = None
                return True
        return super().eventFilter(obj, event)
