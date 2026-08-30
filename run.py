# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""程序入口。

用法: .venv\\Scripts\\python.exe run.py
"""

import os
import sys
import tempfile

from PySide6.QtCore import QLockFile, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from app import __version__
from app.ui.main_window import MainWindow

_SERVER_NAME = "MiHome-Windows"
_LOCK_NAME = "MiHome-Windows.lock"


def _set_console_visible(visible: bool) -> None:
    """Nuitka onefile 通过控制台解压文件，解压完成后隐藏控制台窗口；
    崩溃时再恢复，保证 start.bat 承诺的「截屏报错」可兑现。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 4 if visible else 0)  # SW_SHOWNOACTIVATE / SW_HIDE
    except Exception:
        pass


# 基准界面缩放：整体 UI 在系统缩放之上再乘 1.25，作为软件的默认
# 观感基线（即此前系统里 QT_SCALE_FACTOR=1.25 时的样子）；设置页的
# 「界面缩放比例」是在此基准之上的个人微调乘数（默认 100%）。
_BASE_UI_SCALE = 1.25


def _apply_ui_scale_env() -> None:
    """按「基准 1.25 × 设置乘数」写入 QT_SCALE_FACTOR。

    必须在 QApplication 创建之前调用；Qt 只在初始化时读取该变量，
    更改后需重启生效。始终覆写：系统环境里可能残留外部设置的值，
    若不覆盖会与基准叠加导致界面异常巨大。
    """
    from app.core.settings_store import get_ui_scale
    os.environ["QT_SCALE_FACTOR"] = f"{_BASE_UI_SCALE * get_ui_scale():g}"


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _apply_ui_scale_env()
    app = QApplication(sys.argv)

    # 主题必须在创建任何控件之前生效：调色板决定全部内联样式的取值
    from app.core.settings_store import get_theme_mode
    from app.ui.theme_service import apply_theme
    # apply_theme 内部已设置全局 QSS，无需重复应用
    apply_theme(get_theme_mode())

    # 单实例：仅允许一个进程，二次启动唤起已有窗口。
    # 自重启（MIHOME_RESTARTED=1）时跳过：旧进程即将退出，
    # 此时锁/server 尚在，走正常单实例会被当成「唤起旧窗口」。
    server = None
    if os.environ.get("MIHOME_RESTARTED") != "1":
        lock_path = os.path.join(tempfile.gettempdir(), _LOCK_NAME)
        lock = QLockFile(lock_path)
        # 默认 30s 视为过期，若上次崩溃残留可自动接管
        if not lock.tryLock(0):
            # 尝试唤起已有实例
            sock = QLocalSocket()
            sock.connectToServer(_SERVER_NAME)
            if sock.waitForConnected(400):
                try:
                    sock.write(b"show")
                    sock.waitForBytesWritten(300)
                except Exception:
                    pass
                try:
                    sock.disconnectFromServer()
                except Exception:
                    pass
                return 0
            # 连接失败视为残留锁/服务，强制清理后重试一次
            try:
                QLocalServer.removeServer(_SERVER_NAME)
            except Exception:
                pass
            try:
                lock.unlock()
            except Exception:
                pass
            if not lock.tryLock(0):
                return 0
        # 首实例：持有锁并监听唤起请求
        QLocalServer.removeServer(_SERVER_NAME)
        server = QLocalServer()
        # 监听失败不影响主流程，仅失去二次唤起能力
        try:
            server.listen(_SERVER_NAME)
        except Exception:
            pass
        # 防止被 GC 回收
        app._single_instance_lock = lock  # type: ignore[attr-defined]
        app._single_instance_server = server  # type: ignore[attr-defined]

    # 字体抗锯齿：优先抗锯齿而非网格对齐，明显减少小字号锯齿
    font = QFont("Microsoft YaHei UI", 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)
    app.setApplicationName("MiHome-Windows")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()

    # 二次启动唤起：显示并置顶已有窗口
    def _on_show_request() -> None:
        try:
            while server.hasPendingConnections():  # type: ignore[union-attr]
                s = server.nextPendingConnection()
                if s is not None:
                    try:
                        s.waitForReadyRead(30)
                    except Exception:
                        pass
                    try:
                        s.deleteLater()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            window.show()
            window.raise_()
            window.activateWindow()
            if window.isMinimized():
                window.showNormal()
        except Exception:
            pass

    try:
        server.newConnection.connect(_on_show_request)  # type: ignore[union-attr]
    except Exception:
        pass

    from app.core.settings_store import get_start_minimized, get_minimize_to_tray
    # 以托盘方式静默启动：完全不显示主窗口（设备列表在后台加载，
    # 卡片网格延迟到首次唤出才构建，常驻内存显著更低）。曾用
    # 「透明 show 再隐藏」初始化原生窗口，但那会连带构建整页卡片
    _start_hidden = get_start_minimized() and get_minimize_to_tray()
    if not _start_hidden:
        window.show()
    # 登录检查放在 show 之后发起：回调经队列信号回到主线程，
    # 先启动事件循环可保证首次回调不被阻塞在窗口绘制之前
    window.start()
    if _start_hidden:
        # 事件循环转起来、后台加载完成后修剪工作集
        from app import trim_working_set

        QTimer.singleShot(500, trim_working_set)
    return app.exec()


if __name__ == "__main__":
    _set_console_visible(False)
    try:
        code = main()
    except BaseException:
        # 崩溃时恢复控制台并保留报错内容等用户确认，
        # 避免窗口一闪而过、用户按 start.bat 提示截屏时什么都看不到
        _set_console_visible(True)
        import traceback
        traceback.print_exc()
        try:
            input("程序异常退出，按回车键关闭…")
        except Exception:
            pass
        code = 1
    raise SystemExit(code)
