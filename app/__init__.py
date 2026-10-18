# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
#
# 本程序为自由软件，基于 GPL-3.0 或更高版本发布；在遵守许可证的前提下，
# 你可以自由使用、修改和再分发它。本程序不含任何担保，详见 LICENSE 文件。

__version__ = "0.3.4"

import logging
import sys
from pathlib import Path


def setup_logging(level: int = logging.INFO) -> Path | None:
    """把日志落到数据目录的 debug.log，并返回该路径。

    为什么必须有：`run.py` 在运行期会隐藏控制台，槽函数里抛的异常、
    后台任务的失败、云端返回的错误码，全都打到一个看不见的 stderr 上，
    排查问题时等于没有线索。写到文件里，用户只要把 log 发出来就能定位。

    打包形态与源码形态各自落在自己的数据目录（见 _json_store.data_dir）：
    源码模式在项目根，构建版在 %LOCALAPPDATA%\\MiHome-Windows\\。
    """
    from app.core import _json_store

    path = _json_store.data_file("debug.log")
    root = logging.getLogger()
    # 重复调用（如自重启）不叠加 handler，否则每条日志写两遍
    for handler in list(root.handlers):
        if getattr(handler, "_mihome_file", False):
            return path
    try:
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        return None
    handler._mihome_file = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(min(level, root.level or level))
    return path


def is_packaged() -> bool:
    """是否运行于构建产物（Nuitka standalone/onefile）。

    不能用 sys.frozen——那是 PyInstaller 的约定，Nuitka standalone
    不设置它；Nuitka 的官方标记是主模块上的 __compiled__ 属性。
    """
    if getattr(sys, "frozen", False):
        return True
    return hasattr(sys.modules.get("__main__"), "__compiled__")


def trim_working_set() -> None:
    """把当前进程物理内存页交还系统（任务管理器占用立降）。

    托盘常驻应用的常规手法：隐藏/启动后台时调用，页面在下次
    访问时按需自动换回。非 Windows 为空操作。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        # 伪句柄是 -1：restype 必须显式声明 c_void_p，默认 int 会把
        # 64 位句柄截断成无效句柄（err=6）导致修剪静默失败
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessWorkingSetSize.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        handle = k32.GetCurrentProcess()
        k32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


def resource_path(relative: str) -> Path:
    """定位打包资源文件，兼容开发环境与 Nuitka standalone/onefile。

    调用方一律使用相对于项目根的路径，如 ``app/ui/icon.png``。
    Nuitka 打包后数据文件位于 exe 同级目录（onefile 解压根），
    开发环境则位于本文件向上两级的仓库根。
    """
    exe_dir = Path(sys.executable).resolve().parent
    candidate = exe_dir / relative
    if candidate.exists():
        return candidate
    # 开发环境：app/__init__.py 向上两级到项目根
    return Path(__file__).resolve().parents[1] / relative
