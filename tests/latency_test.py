# SPDX-License-Identifier: GPL-3.0-or-later
"""开关响应速度回归测试（离屏）。

针对「设备开启关闭后响应慢」这条反馈，锁死四项行为：

1. 队列优先级：控制命令能插到排队中的后台杂活之前
2. 同优先级内仍是先入先出（不能因为改优先级打乱顺序）
3. 已知状态时开关只发一次写请求，不再先读后写（2 次往返 → 1 次）
4. 乐观 UI：点下立刻翻转按钮，失败回滚，不留下假状态

另外覆盖挂件的就地状态更新（轮询不应整窗重建卡片）。
"""

import os
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.core import board_store
from app.core.jobs import (
    PRIORITY_CONTROL,
    PRIORITY_DATA,
    PRIORITY_IDLE,
    JobExecutor,
)
from app.core.models import DeviceInfo
from app.ui.widgets_board import WidgetsBoard


def pump(seconds: float, until=None) -> None:
    """让主线程跑事件循环，等待后台任务回调回来。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


# ---------- 1. 控制命令插到后台杂活之前 ----------
jobs = JobExecutor()
gate = threading.Event()
entered = threading.Event()
order: list[str] = []


def _block_worker() -> None:
    entered.set()
    gate.wait(3)


jobs.submit(_block_worker)
assert entered.wait(2), "工人线程没有开始执行占位任务"

# 工人被占住时，先排一个"杂活"，再排一个"控制命令"
jobs.submit(lambda: order.append("idle"), priority=PRIORITY_IDLE)
jobs.submit(lambda: order.append("control"), priority=PRIORITY_CONTROL)
gate.set()
pump(3, until=lambda: len(order) >= 2)
assert order == ["control", "idle"], f"优先级未生效，实际顺序: {order}"
print("1. 控制命令插队优先 OK")

# ---------- 2. 同优先级先入先出 ----------
gate2 = threading.Event()
entered2 = threading.Event()


def _block2() -> None:
    entered2.set()
    gate2.wait(3)


fifo: list[int] = []
jobs.submit(_block2)
assert entered2.wait(2)
for i in range(5):
    jobs.submit(lambda n=i: fifo.append(n), priority=PRIORITY_DATA)
gate2.set()
pump(3, until=lambda: len(fifo) >= 5)
assert fifo == [0, 1, 2, 3, 4], f"同优先级顺序被打乱: {fifo}"
print("2. 同优先级 FIFO OK")
jobs.shutdown()

# ---------- 3 & 4. 已知状态 = 单次写请求 + 乐观翻转 ----------
class FakeService:
    """记录调用次数，可切换为失败，用于验证往返次数与回滚。"""

    def __init__(self, fail: bool = False, write_delay: float = 0.0):
        self.calls: list[tuple[str, tuple]] = []
        self.fail = fail
        self.write_delay = write_delay

    def _record(self, name, *args):
        self.calls.append((name, args))

    def set_power_state(self, did, state):
        self._record("set_power_state", did, state)
        if self.write_delay:
            time.sleep(self.write_delay)
        if self.fail:
            raise RuntimeError("云端拒绝")

    def power_state(self, did):
        self._record("power_state", did)
        return False

    def toggle_power(self, did, current=None):
        self._record("toggle_power", did)
        self.set_power_state(did, not current)
        return not current

    def power_states(self, dids):
        self._record("power_states", tuple(dids))
        return {d: False for d in dids}


def build_board(service):
    board_store.save_devices(["d0", "d1"])
    board_store.set_columns(2)
    # 必须显式复位折叠态：折叠时挂件只画状态点、不建卡片，
    # 上次运行残留的 collapsed=true 会让本测试拿不到 _cards
    board_store.set_collapsed(False)
    board = WidgetsBoard(service, jobs2, None)
    board.refresh_power = lambda dids: None  # 测试离线
    board.set_devices(
        [DeviceInfo("d0", "客厅灯", "m", "家", "客厅", True),
         DeviceInfo("d1", "卧室灯", "m", "家", "卧室", True)],
        {"d0": False, "d1": True},
    )
    return board


# 3a. 已知状态：只发一次写，不再先读
jobs2 = JobExecutor()
svc = FakeService(write_delay=0.3)
board = build_board(svc)
btn = board._cards["d0"]._power_btn
assert btn.state() is False
board._toggle_power("d0", btn)
# 点下立刻翻转（还不等网络）
assert btn.state() is True, "点了没有立刻翻转，感知延迟依旧"
assert board._known_power["d0"] is True
pump(3, until=lambda: not btn.isEnabled())  # 等回调把 busy 解掉
names = [c[0] for c in svc.calls]
assert names.count("set_power_state") == 1, f"写请求次数不对: {names}"
assert "power_state" not in names, f"已知状态不该再读一次: {names}"
assert "toggle_power" not in names, f"不该走读后写路径: {names}"
assert btn.state() is True
print("3. 已知状态单次写请求 + 乐观翻转 OK")

# 3b. 状态未知：退回读后写，但仍按控制优先级排队
svc2 = FakeService()
board2 = build_board(svc2)
btn2 = board2._cards["d1"]._power_btn
board2._known_power["d1"] = None
board2._toggle_power("d1", btn2)
pump(3, until=lambda: any(c[0] == "toggle_power" for c in svc2.calls))
assert any(c[0] == "toggle_power" for c in svc2.calls), "未知状态应走读后写"
print("3b. 状态未知回退读后写 OK")

# 4. 写失败：按钮回滚，不留假状态
svc3 = FakeService(fail=True)
board3 = build_board(svc3)
btn3 = board3._cards["d0"]._power_btn
changed: list[tuple] = []
board3.power_changed.connect(lambda did, st: changed.append((did, st)))
board3._toggle_power("d0", btn3)
assert btn3.state() is True  # 乐观翻转
pump(3, until=lambda: changed)
assert btn3.state() is False, f"失败后未回滚: {btn3.state()}"
assert board3._known_power["d0"] is False
assert changed and changed[-1] == ("d0", False), f"未回传回滚状态: {changed}"
print("4. 写失败回滚 OK")

# ---------- 5. 轮询就地更新，不整窗重建 ----------
svc4 = FakeService()
board4 = build_board(svc4)
cards_before = dict(board4._cards)
board4.apply_power_states({"d0": True, "d1": False})
assert board4._cards is not cards_before or all(
    board4._cards[k] is cards_before[k] for k in cards_before), "轮询不应重建卡片"
assert board4._cards["d0"] is cards_before["d0"], "轮询重建了卡片对象"
assert board4._cards["d0"]._power_btn.state() is True
assert board4._cards["d1"]._power_btn.state() is False
print("5. 轮询就地更新 OK")

jobs2.shutdown()
board_store.save_devices([])
board_store.set_columns(3)
print("LATENCY REGRESSION ALL PASS")
