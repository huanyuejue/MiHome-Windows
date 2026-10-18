# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""无边框遮罩对话框基类。

统一四类弹出界面（添加抽屉 / 设备详情 / 设置 / 托盘管理）的公共
骨架：半透明遮罩、圆角面板、28x28 关闭按钮、淡入淡出开关动画。
子类只需通过 self._panel 摆放内容并在 resizeEvent/showEvent 中
定位面板。
"""

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QFrame, QPushButton
from app.ui.si_theme import SiColors

class OverlayDialog(QDialog):
    """遮罩 + 面板 + 淡入淡出的无边框对话框。"""

    def __init__(self, parent=None, *, overlay: bool = True,
                 modal: Qt.WindowModality = Qt.WindowModality.WindowModal):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(modal)
        self._closing = False

        if overlay:
            self._overlay = QFrame(self)
            self._overlay.setObjectName("overlay")
            self._overlay.setStyleSheet(
                "QFrame#overlay { background: rgba(0,0,0,140); border: none; }")
            self._overlay.mousePressEvent = lambda e: self.reject()
        else:
            self._overlay = None

        self._panel = QFrame(self)
        self._panel.setObjectName("overlayPanel")
        self._panel.setStyleSheet(
            f"QFrame#overlayPanel {{ background: {SiColors.WINDOW_BG}; border: 1px solid {SiColors.LINE}; border-radius: 16px; }}")

    def _place_overlay(self) -> None:
        """resizeEvent 中调用：遮罩铺满窗口。"""
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())

    def _close_button_qss(self) -> str:
        return (
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            f" border-radius: 14px; color: {SiColors.TEXT_PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SiColors.DANGER}; color: {SiColors.WHITE}; }}")

    def _make_close_button(self) -> QPushButton:
        button = QPushButton("✕")
        button.setFixedSize(28, 28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(self._close_button_qss())
        button.clicked.connect(self.reject)
        self._close_btn = button
        return button

    def _fill_parent_window(self) -> bool:
        """把对话框几何设为父窗口客户区（父窗口可见时）。"""
        parent = self.parentWidget()
        if parent is None:
            return False
        win = parent.window()
        if not win.isVisible():
            return False
        pos = win.mapToGlobal(win.rect().topLeft())
        self.setGeometry(pos.x(), pos.y(), win.width(), win.height())
        return True

    def _center_on_screen(self, w: int, h: int, margin: int = 40) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w = min(w, geo.width() - margin)
        h = min(h, geo.height() - margin)
        self.setGeometry(
            geo.center().x() - w // 2, geo.center().y() - h // 2, w, h)

    def _fade_in(self) -> None:
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = anim
        # 兜底：淡入依赖动画真正跑起来。若动画因窗口管理器/合成器原因
        # 没执行，窗口会永远停在全透明状态——表现为"对话框打不开"。
        # 400ms 后强制复位为不透明（正在淡出时不动，避免打架）。
        QTimer.singleShot(400, self._ensure_visible_opacity)

    def _ensure_visible_opacity(self) -> None:
        import shiboken6

        if not shiboken6.isValid(self) or self._closing:
            return
        if self.windowOpacity() < 0.99:
            self.setWindowOpacity(1.0)

    def retheme(self) -> None:
        """主题切换：重设面板底色（子类覆写以扩展各自内联样式刷新）。

        面板样式是构造时求值的内联样式，子控件自身样式表优先于
        窗口级，主题切换必须对控件自身重设。
        """
        from app.ui.si_theme import SiColors
        self._panel.setStyleSheet(
            f"QFrame#overlayPanel {{ background: {SiColors.WINDOW_BG};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 16px; }}")
        # 关闭按钮配色随主题（曾为导入期冻结的深色字面量）
        if getattr(self, "_close_btn", None) is not None:
            self._close_btn.setStyleSheet(self._close_button_qss())

    def done(self, result) -> None:  # noqa: N802
        # 退场淡出动画：动画结束后再真正关闭。
        # 注意不能写 lambda: super().done(...)——lambda 经 Qt 回调时
        # 拿不到 __class__ 闭包会抛 "super(): no arguments"，导致
        # 对话框停在禁用+模态状态永不关闭（整个界面被卡死）
        if self.windowOpacity() > 0.5 and not self._closing:
            self._closing = True
            self.setEnabled(False)
            anim = QPropertyAnimation(self, b"windowOpacity")
            anim.setDuration(100)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(0.0)
            anim.finished.connect(lambda: QDialog.done(self, result))
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._fade_out_anim = anim
            return
        super().done(result)


