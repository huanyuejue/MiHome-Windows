# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""米家手动场景卡片（主页「场景」选项卡）。

视觉与设备卡片一致：左侧场景名与家庭副标题，右侧圆形运行按钮。
点击右侧运行按钮执行场景（与米家 APP 交互一致，无二次确认）；
执行中按钮转忙碌灰置。
"""

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta

from app.core.models import SceneInfo
from app.siui.components.container import SiRowCard
from app.ui.device_card import (
    _CARD_FIXED_HEIGHT,
    _CARD_FIXED_WIDTH,
    _DragHandle,
    _LONG_PRESS_MS,
    _LONG_PRESS_TOLERANCE,
)
from app.ui.si_theme import SiColors

_RUN_BTN_SIZE = 36


class SceneCard(SiRowCard):
    """场景卡片：点击右侧运行按钮触发 executed 信号。"""

    executed = Signal(str)  # scene_id
    # 排序相关：与设备卡片同一套长按/拖拽语义
    sort_mode_requested = Signal(str)  # noqa: N815 scene_id
    drag_requested = Signal(str)  # noqa: N815 scene_id

    def __init__(self, scene: SceneInfo, parent=None):
        super().__init__(parent, self.LeftToRight)
        self.scene = scene
        self._hovered = False
        self._busy = False

        self.style_data.background_color = QColor(SiColors.CARD)
        self.style_data.border_radius = 14.0
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # 与设备卡片同尺寸：网格列数计算直接复用主窗口的换算
        self.setFixedSize(_CARD_FIXED_WIDTH, _CARD_FIXED_HEIGHT)
        self.muteStretchWidget()

        self._run_btn = QPushButton()
        self._run_btn.setObjectName("sceneRunBtn")
        self._run_btn.setFixedSize(_RUN_BTN_SIZE, _RUN_BTN_SIZE)
        self._run_btn.setIconSize(QSize(22, 22))
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.clicked.connect(lambda: self.executed.emit(scene.scene_id))
        self._apply_run_btn()

        self._name_label = QLabel(scene.name)
        self._name_label.setFont(QFont("Microsoft YaHei UI", 15, QFont.Weight.DemiBold))
        # 允许换行：长场景名不再把所在列撑宽
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")

        self._sub_label = QLabel(scene.home_name or "手动场景")
        self._sub_label.setFont(QFont("Microsoft YaHei UI", 11))
        self._sub_label.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent;")

        # 左侧文字列整体垂直居中，与右侧运行按钮同一水平轴
        text_col = QWidget()
        text_col.setAttribute(Qt.WA_TranslucentBackground)
        text_lay = QVBoxLayout(text_col)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(4)
        text_lay.addStretch(1)
        text_lay.addWidget(self._name_label)
        text_lay.addWidget(self._sub_label)
        text_lay.addStretch(1)

        lay = self.layout()
        lay.setContentsMargins(18, 10, 18, 10)
        lay.addWidget(text_col)
        lay.addStretch(1)
        lay.addWidget(self._run_btn, alignment=Qt.AlignVCenter)

        # 排序状态：与设备卡片一致，长按进排序模式后可直接拖动
        self._sort_mode = False
        self._drop_target = False
        self._press_pos: QPoint | None = None
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(_LONG_PRESS_MS)
        self._long_press_timer.timeout.connect(self._on_long_press)
        # 拖拽手柄同样绝对定位在右上角，不参与布局
        self._drag_handle = _DragHandle(self)
        self._drag_handle.setFixedSize(26, 20)
        self._drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_handle.setToolTip("按住拖动可调整顺序")
        self._drag_handle.hide()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._run_btn.setEnabled(not busy)
        self._apply_run_btn()

    def _apply_run_btn(self) -> None:
        radius = _RUN_BTN_SIZE // 2
        if self._busy:
            self._run_btn.setIcon(qta.icon('mdi.play', color=SiColors.ICON_DIM))
            self._run_btn.setStyleSheet(
                f"QPushButton#sceneRunBtn {{ background: {SiColors.SURFACE_PRESSED};"
                f" border: none; border-radius: {radius}px; }}"
            )
            return
        self._run_btn.setIcon(qta.icon('mdi.play', color=SiColors.WHITE))
        self._run_btn.setStyleSheet(
            f"QPushButton#sceneRunBtn {{ background: {SiColors.THEME}; border: none;"
            f" border-radius: {radius}px; }}"
            f"QPushButton#sceneRunBtn:hover {{ background: {SiColors.THEME_HOVER}; }}"
            f"QPushButton#sceneRunBtn:pressed {{ background: {SiColors.THEME_DIM}; }}"
        )

    # ---------- 排序 ----------

    def set_sort_mode(self, active: bool) -> None:
        """进入/退出排序模式：右上角显示或隐藏拖拽手柄。"""
        if self._sort_mode == active:
            return
        self._sort_mode = active
        self._drag_handle.setVisible(active)
        if active:
            self._place_drag_handle()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_drop_target(self, active: bool) -> None:
        """拖拽落点高亮：底色提亮一档。"""
        if self._drop_target == active:
            return
        self._drop_target = active
        self._apply_card_color()

    def _place_drag_handle(self) -> None:
        self._drag_handle.move(self.width() - self._drag_handle.width() - 8, 6)

    def _on_long_press(self) -> None:
        """按住够久：进入排序模式，之后拖动才真正换位。"""
        if self._press_pos is None or self._sort_mode:
            return
        self._press_pos = None
        self.sort_mode_requested.emit(self.scene.scene_id)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            # 排序模式下按下即可拖动，长按只用来进入排序模式
            if not self._sort_mode:
                self._long_press_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is not None:
            moved = (event.pos() - self._press_pos).manhattanLength()
            if moved > _LONG_PRESS_TOLERANCE:
                self._press_pos = None
                if self._sort_mode:
                    self.drag_requested.emit(self.scene.scene_id)
                    return
                self._long_press_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._long_press_timer.stop()
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._sort_mode:
            self._place_drag_handle()

    # ---------- hover 观感 ----------

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        self._hovered = True
        self._apply_card_color()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名约定)
        self._hovered = False
        self._apply_card_color()
        super().leaveEvent(event)

    def _apply_card_color(self) -> None:
        # 落点高亮优先于 hover：拖拽时用户要看的是"会换到哪一格"
        self.style_data.background_color = QColor(
            SiColors.CARD_HOVER if (self._hovered or self._drop_target)
            else SiColors.CARD
        )
        self.update()

    # 刻意不重写 mouseReleaseEvent：只有右侧运行按钮触发 executed，
    # 点击卡片空白处不执行任何操作（场景无详情页可开，误触成本高）
