# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""系统托盘控制器：常驻图标 + 菜单 + 快捷窗口编排。"""

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.core.jobs import JobExecutor
from app.core.models import DeviceInfo
from app.core.service import MijiaService
from app.ui.tray.quick_window import TrayQuickWindow


class TrayController:
    """托盘图标控制器：常驻图标 + 快捷窗口 + 右键菜单。"""

    def __init__(self, service: MijiaService, jobs: JobExecutor, main_window):
        self._service = service
        self._jobs = jobs
        self._main = main_window
        self._pending_show = False
        # 快捷窗口设为独立顶层窗口，不随主窗口模态对话框被阻塞
        self._create_quick_window()

        # 使用自定义 tray_icon.png 作为托盘图标，提供多尺寸确保清晰
        from app import resource_path
        _icon_path = str(resource_path("app/ui/tray_icon.png"))
        _tray_icon = QIcon(_icon_path)
        _tray_icon.addFile(_icon_path, QSize(16, 16))
        _tray_icon.addFile(_icon_path, QSize(32, 32))
        _tray_icon.addFile(_icon_path, QSize(48, 48))
        self._tray = QSystemTrayIcon(_tray_icon, main_window)
        self._tray.setToolTip("米家 - MiHome for Windows")
        self._tray.activated.connect(self._on_activated)

        menu = QMenu()
        menu.setObjectName("appMenu")
        act_show = QAction("显示主窗口", menu)
        act_show.triggered.connect(self._show_main)
        menu.addAction(act_show)
        act_manage = QAction("管理托盘项目", menu)
        act_manage.triggered.connect(self._emit_manage)
        menu.addAction(act_manage)
        act_schedule = QAction("定时任务", menu)
        act_schedule.triggered.connect(self._on_schedules)
        menu.addAction(act_schedule)
        # 桌面挂件：显示/隐藏 + 管理 两条并列，入口放一起，
        # 用户不必先找到主窗口
        act_board = QAction("显示桌面挂件", menu)
        act_board.triggered.connect(self._on_toggle_board)
        menu.addAction(act_board)
        self._board_action = act_board
        act_board_manage = QAction("管理桌面挂件", menu)
        act_board_manage.triggered.connect(self._on_manage_board)
        menu.addAction(act_board_manage)
        act_board_lock = QAction("锁定桌面挂件", menu)
        act_board_lock.setCheckable(True)
        act_board_lock.triggered.connect(self._on_toggle_board_lock)
        menu.addAction(act_board_lock)
        self._board_lock_action = act_board_lock
        act_settings = QAction("设置", menu)
        act_settings.triggered.connect(self._on_settings)
        menu.addAction(act_settings)
        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        # 每次弹出前刷新挂件菜单文案：窗口可能被挂件自己的 × 按钮关掉，
        # 菜单若一直写「显示」会与实际状态不符
        menu.aboutToShow.connect(self._refresh_board_action)
        menu.aboutToShow.connect(self._refresh_board_lock_action)
        self._tray.setContextMenu(menu)

        # 快捷窗口的信号接线统一在 _create_quick_window 内做一次，
        # 此处重复连接曾导致每次触发弹两次对话框

        # 托盘图标常驻，无需可用性检查也尝试显示
        self._tray.show()
        # 托盘图标跟系统配色（任务栏底色由系统决定），与应用主题设置无关
        from PySide6.QtGui import QGuiApplication
        self.apply_system_icon_theme(
            QGuiApplication.styleHints().colorScheme())

    def apply_system_icon_theme(self, scheme) -> None:
        """按系统配色切换托盘图标：浅色任务栏用深色图形，反之亦然。

        scheme 接受 Qt.ColorScheme 或 \"dark\"/\"light\" 字符串。
        """
        from PySide6.QtCore import Qt

        if isinstance(scheme, str):
            is_light = scheme == "light"
        else:
            is_light = scheme == Qt.ColorScheme.Light
        if is_light:
            icon_file = "app/ui/tray_icon_light.png"
        else:
            icon_file = "app/ui/tray_icon.png"
        from app import resource_path
        path = str(resource_path(icon_file))
        icon = QIcon(path)
        icon.addFile(path, QSize(16, 16))
        icon.addFile(path, QSize(32, 32))
        icon.addFile(path, QSize(48, 48))
        self._tray.setIcon(icon)

    def _create_quick_window(self) -> None:
        """创建快捷窗口并接线；主题切换时整窗重建复用。"""
        self._quick = TrayQuickWindow(self._service, self._jobs, None)
        self._quick.manage_requested.connect(self._on_manage)
        self._quick.open_device_requested.connect(self._on_open_device)
        self._quick.open_main_requested.connect(self._show_main)

    def _emit_manage(self) -> None:
        # 经方法转发而非构造期绑定信号：retheme 重建窗口后菜单仍指向新窗口
        self._quick.manage_requested.emit()

    def _on_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:  # 仅左键单击
            return
        quick = self._quick
        # 正在播放呼出/隐藏动画时，终止旧动画并以带动画方式切换，避免直接 hide 丢失关闭动画
        if quick.is_animating():
            quick.abort_toggle_animation()
            if quick.isVisible() or quick.is_explicitly_visible():
                quick.hide_animated()
            else:
                quick.show_near_tray()
            return
        if quick.isVisible():
            quick.hide_animated()
        else:
            quick.show_near_tray()

    def _show_main(self) -> None:
        self._main.show()
        self._main.raise_()
        self._main.activateWindow()
        if self._main.isMinimized():
            self._main.showNormal()

    def _quit(self) -> None:
        self._tray.hide()
        # 标记为强制退出，避免 closeEvent 拦截为隐藏到托盘
        self._main.request_force_quit()
        self._main.close()
        # quitOnLastWindowClosed 为 False 时需显式退出事件循环
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_manage(self) -> None:
        self._main.show_tray_manager()

    def _on_schedules(self) -> None:
        self._main.show_schedules()

    # ---- 桌面挂件 ----

    def _on_toggle_board(self) -> None:
        """托盘菜单「桌面挂件」：已显示则隐藏，未显示则显示。"""
        if self._board_visible():
            self._main.hide_board()
        else:
            self._main.show_board()
        self._refresh_board_action()

    def _board_visible(self) -> bool:
        board = getattr(self._main, "_board", None)
        return board is not None and board.isVisible()

    def _refresh_board_action(self) -> None:
        """菜单文案跟随挂件实际状态，避免"显示"点了却是隐藏。"""
        if self._board_action is not None:
            self._board_action.setText(
                "隐藏桌面挂件" if self._board_visible() else "显示桌面挂件")

    def _on_manage_board(self) -> None:
        self._main.show_board_manager()

    def _on_toggle_board_lock(self, checked: bool) -> None:
        """托盘菜单锁定/解锁：锁定后挂件隐藏标题栏、不能拖动。"""
        board = getattr(self._main, "_board", None)
        if board is None:
            return
        board.set_locked(bool(checked))

    def _refresh_board_lock_action(self) -> None:
        board = getattr(self._main, "_board", None)
        if self._board_lock_action is not None and board is not None:
            locked = bool(getattr(board, "_locked", False))
            self._board_lock_action.setChecked(locked)
            self._board_lock_action.setText(
                "解锁桌面挂件" if locked else "锁定桌面挂件")

    def _on_settings(self) -> None:
        self._main.show_settings()

    def _on_open_device(self, did: str) -> None:
        # 仅打开设备详情，不呼出主窗口，避免主窗口隐藏时卡死
        self._quick.hide()
        # 直接创建详情对话框，parent 设为 None 使其独立于主窗口显隐
        dev = next((d for d in self._main.devices() if d.did == did), None)
        if dev is None:
            return
        from app.ui.device_dialog import DeviceDetailDialog
        dlg = DeviceDetailDialog(self._service, self._jobs, dev, None)
        dlg.load()
        dlg.exec()
        dlg.deleteLater()

    def set_devices(self, devices: list[DeviceInfo], known_power: dict[str, bool | None]) -> None:
        self._quick.set_devices(devices, known_power)

    def set_metrics(self, metrics: dict[str, str | None]) -> None:
        self._quick.set_metrics(metrics)
        if self._pending_show:
            # 重建前窗口正显示着：设备填充完毕后再呼出，避免空窗口闪现
            self._pending_show = False
            self._quick.show_near_tray()

    def retheme(self) -> None:
        """主题切换：整个快捷窗口重建。

        快捷窗口的内联样式散布在窗框/音频栏/语音条/设备行/工具条
        多处，逐控件补样式总有遗漏（曾反复出现新旧色混杂残留）；
        整窗重建让全部样式天然取新调色板。设备与开关状态由主窗口
        在本调用之后立刻 set_devices 推送，显示状态在此登记。
        """
        was_visible = self._quick.isVisible() or self._quick.is_explicitly_visible()
        self._quick.abort_toggle_animation()
        self._quick.hide()
        self._quick.deleteLater()
        self._create_quick_window()
        self._pending_show = was_visible

    def hide_quick(self) -> None:
        self._quick.hide()

    def set_tray_visible(self, visible: bool) -> None:
        """按设置开关托盘图标显隐：开启时常驻图标可见，关闭时隐藏。"""
        try:
            self._tray.setVisible(visible)
        except Exception:
            pass

    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

