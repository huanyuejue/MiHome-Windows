# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""程序入口。

用法: .venv\\Scripts\\python.exe run.py
"""

import os
import sys
import tempfile


def _enable_per_monitor_dpi() -> None:
    """在导入 PySide6 之前把进程 DPI 感知设为 PerMonitorV2。

    Qt 库一加载就会确定进程的 DPI 感知级别，之后再调用
    SetProcessDpiAwarenessContext 会被系统拒绝（日志里表现为一条
    「拒绝访问」的警告）。因此这一步必须抢在导入之前完成。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 的句柄值为 -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


_enable_per_monitor_dpi()

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


def _enable_qt_accessibility() -> None:
    """强制激活 Qt 的 Windows UIA 无障碍桥接。

    ``qwindows.dll`` 里已静态编入 QWindowsUia，但 Qt 默认只在检测到 UIA
    客户端时才惰性激活；读屏软件晚于应用启动时，首个窗口的可访问树可能
    来不及建立。设为 1 让桥接从一开始就就绪。对不使用辅助技术的用户
    没有任何视觉或交互影响，也不额外加载 DLL。若用户显式设了值（含 0）
    则尊重用户选择。
    """
    os.environ.setdefault("QT_ACCESSIBILITY", "1")


def _install_qt_translator(app) -> None:
    """加载 Qt 自带控件的中文翻译，兼容源码与 Nuitka 打包两种布局。

    源码模式直接取 QLibraryInfo 指向的 PySide6/translations；打包后该
    目录被复制到 exe 旁边的 PySide6/translations，QLibraryInfo 不一定
    指得准，这里按候选路径逐个找，找到就装。
    """
    from pathlib import Path

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    name = f"qtbase_{QLocale.system().name()}.qm"
    candidates: list[Path] = []
    try:
        candidates.append(
            Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / name)
    except Exception:
        pass
    exe_dir = Path(sys.executable).parent
    candidates.extend((
        exe_dir / "translations" / name,
        exe_dir / "PySide6" / "translations" / name,
    ))
    for path in candidates:
        if not path.is_file():
            continue
        translator = QTranslator(app)
        if translator.load(str(path)):
            app.installTranslator(translator)
            # 挂到 app 上保活：局部变量被回收后翻译会失效
            app._qt_translator = translator  # type: ignore[attr-defined]
            return


def main() -> int:
    # 日志先落地：控制台在运行期是隐藏的，异常与失败只能靠文件留存
    from app import is_packaged, setup_logging
    setup_logging()
    # 源码模式额外把控制台输出与崩溃堆栈同步写进 app.log：崩溃时
    # 控制台窗口会一起消失，事后只剩这个文件能提供线索
    if not is_packaged():
        from app.core import _json_store
        from app.diagnostics import setup_console_log
        setup_console_log(_json_store.data_file("app.log"))
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    _apply_ui_scale_env()
    _enable_qt_accessibility()
    app = QApplication(sys.argv)

    # 无障碍桥接必须在任何控件创建之前安装：全局事件过滤器要能捕获
    # 后续所有 widget 的 Show / ChildAdded，才能补上名称、焦点与键盘入口。
    # 纯加法，不改变既有绘制与鼠标交互；详见 app/ui/a11y.py。
    from app.ui.a11y import install_accessibility
    app._a11y_bridge = install_accessibility(app)  # type: ignore[attr-defined]

    # Qt 自带控件的文案（取色板、消息框按钮等）按系统语言加载翻译：
    # 不加载时 QColorDialog 会整屏英文（"Basic colors"/"OK"/"Cancel"）
    _install_qt_translator(app)

    # 主题必须在创建任何控件之前生效：调色板决定全部内联样式的取值
    from app.core.settings_store import get_theme_mode
    from app.ui.theme_service import apply_theme
    # apply_theme 内部已设置全局 QSS，无需重复应用
    apply_theme(get_theme_mode())

    # 原生 QToolTip 在系统深色模式下始终是黑底，QSS 覆盖不稳定；
    # 统一换成自绘提示窗，配色跟随应用主题
    from app.ui.tooltip import TooltipController
    app._tooltip_controller = TooltipController(app)  # type: ignore[attr-defined]

    # 滚轮滚动统一走缓动动画，避免原生逐条跳变的抽帧感
    from app.ui.smooth_scroll import SmoothScrollFilter
    app._smooth_scroll = SmoothScrollFilter(app)  # type: ignore[attr-defined]

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
