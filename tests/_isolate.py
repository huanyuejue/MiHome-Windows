"""测试用的数据目录隔离。

所有回归测试必须在导入 app 之前调用 `isolate()`，把配置写入临时目录。
否则测试会直接改写项目根下的真实配置（board.json / schedules.json /
tray.json / settings.json），表现为"用户每次跑完测试设置就没了"——
这曾真实发生过。
"""

import atexit
import os
import shutil
import tempfile


def isolate() -> str:
    """把数据目录指向临时目录并返回路径；进程退出时清理。"""
    path = tempfile.mkdtemp(prefix="mihome-test-")
    os.environ["MIHOME_DATA_DIR"] = path
    atexit.register(shutil.rmtree, path, True)
    return path
