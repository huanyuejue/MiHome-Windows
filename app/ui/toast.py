# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""轻量通知浮层：右下角滑现、停留后淡出。

用于"刷新完成，新增 X 台"这类无需用户确认的结果反馈，不打断
当前操作；同一时刻只保留最新一条，新消息直接替换旧消息。
"""

import shiboken6
from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from app.ui.si_theme import SiColors


class Toast(QFrame):
    _current: "Toast | None" = None

    @classmethod
    def info(cls, parent: QWidget, text: str, duration_ms: int = 4000) -> None:
        if cls._current is not None and shiboken6.isValid(cls._current):
            cls._current.deleteLater()
        toast = cls(parent, text)
        cls._current = toast
        toast._popup(duration_ms)

    @classmethod
    def screen_info(cls, text: str, duration_ms: int = 4000) -> None:
        """屏幕级通知：挂在主屏右下角，不依赖应用窗口可见。

        语音控制的反馈（识别结果/错误提示）用这个——主窗口可能隐藏在
        托盘或最小化，挂在窗口上的 Toast 用户根本看不见（提示"出现在
        输入指令框位置"就是这个原因：宿主窗口不可见时 Toast 被裁掉了）。
        """
        if cls._current is not None and shiboken6.isValid(cls._current):
            cls._current.deleteLater()
        toast = cls(None, text)
        toast.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.ToolTip
            | Qt.WindowType.WindowTransparentForInput)
        toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        toast.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        cls._current = toast
        toast._popup_screen(duration_ms)

    def __init__(self, parent: QWidget | None, text: str):
        super().__init__(parent)
        self.setObjectName("toastCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        label = QLabel(text)
        label.setFont(QFont("Microsoft YaHei UI", 10))
        label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        lay.addWidget(label)

    def _popup(self, duration_ms: int) -> None:
        self.adjustSize()
        host = self.parentWidget()
        x = max(16, host.width() - self.width() - 20)
        y = max(16, host.height() - self.height() - 16)
        # 宿主右下角有语音悬浮球时通知上移，间距与输入框保持一致
        fab = getattr(host, "_voice_fab", None)
        if fab is not None and shiboken6.isValid(fab) and fab.isVisible():
            y -= fab.height() + 12 + 10
        self.move(x, y)
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self._fade_out)

    def _popup_screen(self, duration_ms: int) -> None:
        self.adjustSize()
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 24,
                      geo.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self._fade_out)

    def _fade_out(self) -> None:
        if not shiboken6.isValid(self):
            return
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self.deleteLater)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

