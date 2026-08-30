# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用自重启：界面缩放等设置需重启进程才生效时的一键重启。

常驻托盘时「关窗口」只是隐藏到托盘，进程并未退出；若仅提示手动
重启，用户会以为改动没生效。这里先启动新进程（带 MIHOME_RESTARTED=1
标记），再退出旧进程：run.py 检测到该标记会跳过单实例唤起逻辑，
避免新进程被当成「唤起旧窗口」而提前退出。
"""

import os
import sys

from PySide6.QtCore import QProcess


def restart_app() -> None:
    """退出当前进程并启动新实例；开发/打包形态均支持。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return

    args = sys.argv[:]
    executable, argv = _launch_command(args)

    # 通过父进程环境传递重启标记：QProcess.startDetached 子进程继承
    # 父进程 os.environ，新进程 run.py 据此跳过单实例唤起
    os.environ["MIHOME_RESTARTED"] = "1"

    # 先起新进程（不受旧进程锁影响），旧进程随即退出释放资源
    QProcess.startDetached(executable, argv)
    app.quit()


def _launch_command(args: list[str]):
    """构造重启命令：开发态用解释器+脚本，打包态直接 exe。"""
    from app import is_packaged
    if is_packaged():
        return str(sys.argv[0]), args[1:]
    # 开发态：run.py 由 .venv 解释器运行，用 sys.executable 保证环境一致
    script = str(sys.argv[0])
    if script.endswith(".py"):
        from pathlib import Path
        script = str(Path(script).resolve())
        return sys.executable, [script] + args[1:]
    return sys.executable, args
