# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""改坏 DPI 自动回滚：重启后检测缩放变化，倒计时确认保留或恢复。

用户把界面缩放改得过大/过小导致界面无法操作时，倒计时结束后
自动回滚到上一次确认的缩放并重启，避免软件「锁死」。

流程：
1. 设置里改缩放 → 旧值记入 settings.last_good_scale，新值写入 ui_scale；
2. 重启后 check_scale_after_start 检测 ui_scale != last_good_scale；
3. 弹 20 秒倒计时确认框 + 系统托盘通知；
4. 点「保留」→ 更新 last_good_scale，继续使用新缩放；
   点「恢复」或超时 → 写回 last_good_scale，自动重启。
"""

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app.core import settings_store

logger = logging.getLogger(__name__)

# 倒计时秒数：超时未确认则自动回滚
_CONFIRM_SECONDS = 20


class ScaleConfirmDialog(QMessageBox):
    """带倒计时的缩放确认框。

    普通 QMessageBox 风格：主窗口若因缩放异常无法交互，20 秒后
    自动走「恢复」分支兜底，因此按钮点不到也不至于锁死。
    """

    def __init__(self, new_pct: float, old_pct: float, parent=None):
        super().__init__(parent)
        self._remaining = _CONFIRM_SECONDS
        self._new_pct_val = new_pct
        self._old_pct_val = old_pct
        self.setWindowTitle("界面缩放已修改")
        self.setIcon(QMessageBox.Icon.Question)
        self._refresh_text()
        self.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        self.button(QMessageBox.StandardButton.Yes).setText("保留此次缩放")
        self.button(QMessageBox.StandardButton.No).setText("恢复上次缩放")
        self.setDefaultButton(QMessageBox.StandardButton.Yes)
        # 倒计时每秒刷新
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _refresh_text(self) -> None:
        self.setText(
            f"检测到界面缩放已从 {round(self._old_pct_val)}% 调整为 "
            f"{round(self._new_pct_val)}%。\n\n"
            f"若界面显示正常，请选择「保留此次缩放」；"
            f"若显示异常（过大/过小），选择「恢复上次缩放」"
            f"或不操作，{self._remaining} 秒后自动恢复为 "
            f"{round(self._old_pct_val)}% 并重启。")

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            # 超时：视同「恢复」，回滚
            self._timer.stop()
            self.done(QMessageBox.StandardButton.No)
            return
        self._refresh_text()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)


def _pct(value: float) -> int:
    return round(value * 100)


def check_scale_after_start() -> bool:
    """应用启动后调用：若缩放与上次确认值不一致，进入倒计时确认流程。

    返回 True 表示应用应继续运行（无变化 / 用户保留）；
    返回 False 表示已回滚并触发重启，调用方应终止本进程。
    """
    ui_scale = settings_store.get_ui_scale()
    last_good = settings_store.get_last_good_scale()
    # 首次启动无历史记录，或缩放未变化：无需确认
    if last_good <= 0 or abs(ui_scale - last_good) < 1e-9:
        return True
    # 弹确认框的同时发系统通知，提醒用户若不确认会自动回滚
    # （确认框可能因缩放异常点不到，通知是唯一的提示渠道；文案从简，
    # 太长用户看不完就收回）
    _notify(
        "界面缩放已修改",
        f"若显示正常请点「保留」；否则 {_CONFIRM_SECONDS} 秒后自动回滚 "
        f"{round(last_good * 100)}%。",
    )
    # 进入确认流程
    dialog = ScaleConfirmDialog(_pct(ui_scale), _pct(last_good))
    result = dialog.exec()
    dialog.deleteLater()
    if result == QMessageBox.StandardButton.Yes:
        # 用户确认保留：把当前值记为「上一次确认」，不再回滚
        settings_store.set_last_good_scale(ui_scale)
        logger.info("用户保留界面缩放 %s%%", _pct(ui_scale))
        return True
    # 用户恢复或超时：写回旧值并自动重启
    settings_store.set_ui_scale(last_good)
    settings_store.set_last_good_scale(last_good)
    logger.warning("界面缩放回滚为 %s%%", _pct(last_good))
    # 再发一条通知说明已回滚，随后自动重启
    _notify(
        "界面缩放已自动回滚",
        f"已恢复为 {round(last_good * 100)}%，正在重启。",
    )
    from app.ui.restart import restart_app
    restart_app()
    return False


def _notify(title: str, message: str) -> None:
    """系统托盘通知（仅提示，不影响流程）。"""
    try:
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app = QApplication.instance()
        icon = None
        if app is not None:
            from app import resource_path
            from PySide6.QtGui import QIcon
            icon = QIcon(str(resource_path("app/ui/tray_icon.png")))
        notifier = QSystemTrayIcon(icon)
        notifier.setVisible(True)
        notifier.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )
        # 通知对象需要存活到消息展示完
        app._scale_rollback_notifier = notifier  # type: ignore[attr-defined]
        QTimer.singleShot(8000, notifier.deleteLater)
    except Exception as exc:  # 通知失败不影响主流程
        logger.warning("系统托盘通知失败: %s", exc)
