# SPDX-License-Identifier: GPL-3.0-or-later
"""开关状态"回弹"回归测试：在途轮询不得覆盖本地操作。

现象（用户反馈）：点开灯 → 按钮变绿 → 不到 1 秒又变灰 → 过一会儿才真的绿。
原因：5 秒轮询在点击之前就发出去了，回来时带的是旧值，直接覆盖了刚翻转
的状态。子设备写入要两次请求（子 did 失败→父 did 重试），窗口期更长，
所以特别明显。

本测试用"记录版本号"的机制锁死：轮询发起后、返回前发生过本地操作的
设备，其轮询结果必须被丢弃。
"""

import os
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.core import cache as _device_cache
_device_cache.save = lambda *a, **k: None

from app.core import board_store
from app.core.jobs import JobExecutor
from app.core.models import DeviceInfo
from app.ui.main_window import MainWindow
from app.ui.widgets_board import WidgetsBoard

MainWindow._refresh_power_states = lambda self, force: None
MainWindow._refresh_metrics = lambda self: None
MainWindow._load_card_icons = lambda self: None
MainWindow._maybe_localize_names = lambda self: None
MainWindow._load_scenes = lambda self: None


def pump(seconds=1.0, until=None):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


# ---------- 1. 主窗口：过期轮询结果被丢弃 ----------
devs = [
    DeviceInfo("d1", "厨房灯", "m", "家", "厨房", True),
    DeviceInfo("d2", "餐厅灯", "m", "家", "餐厅", True),
]
win = MainWindow()
win.show()
app.processEvents()
win._apply_devices(devs)
app.processEvents()
assert win._cards, "卡片网格未构建（需先 show）"
win._known_power.update({"d1": False, "d2": False})

# 模拟"轮询在点击之前发出"：此时快照的版本号是 0
stale_snapshot = dict(win._power_gen)

# 用户点了 d1：乐观翻转 + 版本号 +1
win._on_power_clicked("d1")
assert win._known_power["d1"] is True, "点击后应乐观翻转为开"
card_d1 = win._cards["d1"]
assert card_d1._power_btn.state() is True, "按钮应立即显示为开"

# 那条旧轮询现在才回来，携带点击前的旧值 False
win._apply_power_states({"d1": False, "d2": False}, stale_snapshot)
assert win._known_power["d1"] is True, \
    f"过期轮询覆盖了本地操作（回弹）: {win._known_power['d1']}"
assert card_d1._power_btn.state() is True, "按钮被打回灰色（用户看到的回弹）"
# 没被操作过的设备正常接受轮询结果
assert win._known_power["d2"] is False
print("1. 主窗口：过期轮询不覆盖本地操作 OK")

# 新一轮轮询（快照包含最新版本号）应当正常生效
fresh_snapshot = dict(win._power_gen)
win._apply_power_states({"d1": True}, fresh_snapshot)
assert win._known_power["d1"] is True
print("2. 新的轮询结果仍能正常更新 OK")

# 没有快照的调用（内部推送，如挂件回传）保持原行为
win._apply_power_states({"d2": True})
assert win._known_power["d2"] is True
print("3. 无快照的推送不受影响 OK")

win._all_devices = []
win._force_quit = True
win.close()
pump(0.2)

# ---------- 4. 挂件：同样的过期结果也要丢弃 ----------
jobs = JobExecutor()
board_store.save_devices(["d1"])
board_store.set_columns(1)
board_store.set_collapsed(False)
board = WidgetsBoard(None, jobs, None)
board.refresh_power = lambda dids: None
board.set_devices([DeviceInfo("d1", "厨房灯", "m", "家", "厨房", True)], {"d1": False})

stale = dict(board._power_gen)
board._toggle_power("d1", board._cards["d1"]._power_btn)
assert board._known_power["d1"] is True
# 旧结果回来
board._apply_power({"d1": False}, stale)
assert board._known_power["d1"] is True, "挂件按钮被过期轮询打回"
assert board._cards["d1"]._power_btn.state() is True
print("4. 挂件：过期轮询不覆盖本地操作 OK")

jobs.shutdown()
board_store.save_devices([])
print("RACE REGRESSION ALL PASS")
