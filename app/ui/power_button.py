# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""三态圆形电源按钮：设备卡片、托盘快捷行、挂件卡片、详情电源行共用。

开=米家绿、关=中灰、未知=描边空心；另有离线与忙碌两个覆盖态。
配色在每次 _apply() 时从当前调色板取值，主题切换后重建控件即生效。

**忙碌态保留目标状态色**：界面层用"乐观翻转 + 失败回滚"，点下即把
按钮切到目标状态再发请求。若忙碌态把颜色盖成灰底，用户看不到这次
翻转，又会觉得"点了没反应"；因此忙碌时只把配色降一档
（THEME_DIM / STATE_OFF），而不是抹掉状态。
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton

import qtawesome as qta

from app.ui.si_theme import SiColors


class PowerButton(QPushButton):
    """state: True 开 / False 关 / None 未知（或未确认能力）。"""

    def __init__(self, size: int, icon_size: int | None = None, parent=None):
        super().__init__(parent)
        self._size = size
        self._state: bool | None = None
        self._online = True
        self._busy = False
        self.setObjectName("powerBtn")
        side = icon_size if icon_size is not None else int(size * 0.7)
        self.setIconSize(QSize(side, side))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self._apply()

    def set_state(self, state: bool | None) -> None:
        self._state = state
        self._apply()

    def state(self) -> bool | None:
        return self._state

    def set_online(self, online: bool) -> None:
        self._online = online
        self.setEnabled(online and not self._busy)
        self._apply()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.setEnabled(self._online and not busy)
        self._apply()

    def _apply(self) -> None:
        radius = self._size // 2
        if not self._online:
            self.setIcon(qta.icon('mdi.power', color=SiColors.ICON_DIM))
            self.setStyleSheet(
                f"QPushButton#powerBtn {{ background: {SiColors.SURFACE}; border: none;"
                f" border-radius: {radius}px; }}"
            )
            return
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
        if self._state is None:
            self.setIcon(qta.icon('mdi.circle-outline', color=SiColors.ICON_DIM))
            self.setStyleSheet(
                f"QPushButton#powerBtn {{ background: {SiColors.STATE_UNKNOWN_BG};"
                f" border: 2px solid {SiColors.STATE_UNKNOWN_BORDER}; border-radius: {radius}px; }}"
                f"QPushButton#powerBtn:hover {{ background: {SiColors.STATE_UNKNOWN_HOVER}; }}"
            )
            return
        color = SiColors.THEME if self._state else SiColors.STATE_OFF
        self.setIcon(qta.icon('mdi.power', color=SiColors.WHITE))
        self.setStyleSheet(
            f"QPushButton#powerBtn {{ background: {color}; border: none;"
            f" border-radius: {radius}px; }}"
            f"QPushButton#powerBtn:pressed {{ background: {SiColors.PRESSED}; }}"
        )

