# SPDX-License-Identifier: GPL-3.0-or-later
"""任务投递回归测试（离屏）。

锁死一条真实事故：**回调里开模态窗口后，后续任务结果再也投递不到界面**。

事故现场（v0.3.0 打包版首次发给别人，对方机器上没有登录凭据）：

1. 启动后主窗口提交「检查登录状态」，回调里发现未登录；
2. 回调里直接弹扫码登录窗（`QDialog.exec()`），在投递定时器的栈上又起了
   一层事件循环；
3. 登录窗提交「取二维码」任务 —— worker 4 秒就跑完了，结果进的是同一个
   投递队列，可投递定时器正被自己的回调堵着；
4. 结果：界面永远停在「正在获取登录二维码…」，既没二维码也没有报错。
   开发机上因为一直有 auth.json，这条路径从来没被走过。

现在投递一律"再排一拍"执行（回调不在定时器栈上），所以回调里开多久的
模态窗口都不影响投递。
"""

import os
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from tests._isolate import isolate

isolate()

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.core.jobs import JobExecutor


def pump(seconds: float, until=None) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


def test_delivery_during_modal_loop() -> None:
    """回调里跑嵌套事件循环（等价于开模态窗）时，别的任务结果照样送达。"""
    jobs = JobExecutor()
    events: list[str] = []

    def _modal_like() -> None:
        events.append("modal-start")
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()          # 模拟 QDialog.exec()：回调里再跑一层事件循环
        events.append("modal-end")

    jobs.submit(lambda: 1, on_success=lambda _v: _modal_like())

    # 150ms 后（此时回调正卡在嵌套循环里）再排一个任务：
    # 它的结果必须在嵌套循环内部送达，而不是等模态窗关掉之后
    QTimer.singleShot(150, lambda: jobs.submit(
        lambda: 2, on_success=lambda _v: events.append("second")))

    pump(2.0, until=lambda: "modal-end" in events and "second" in events)
    jobs.shutdown(wait=1)

    assert events[:1] == ["modal-start"], f"回调未按预期进入嵌套循环: {events}"
    assert "second" in events, f"嵌套事件循环期间任务结果没有送达（旧 bug 复现）: {events}"
    assert events.index("second") < events.index("modal-end"), (
        f"结果被拖到嵌套循环结束后才送达，仍然会卡住扫码页: {events}")
    print("1. 模态窗（嵌套事件循环）期间结果仍能投递 OK")


def test_order_preserved() -> None:
    """推迟一拍执行后，同批结果仍严格按入队顺序回调。"""
    jobs = JobExecutor()
    seen: list[int] = []

    def _blocker() -> None:
        time.sleep(0.15)

    jobs.submit(_blocker)      # 让 results 队列一次攒下多条
    for i in range(6):
        jobs.submit(lambda n=i: n, on_success=lambda v: seen.append(v),
                    priority=1)

    pump(3.0, until=lambda: len(seen) >= 6)
    jobs.shutdown(wait=1)
    assert seen == list(range(6)), f"结果投递顺序被打乱: {seen}"
    print("2. 结果按入队顺序投递 OK")


def test_callback_error_is_contained() -> None:
    """某个回调抛异常，不影响后续结果投递。"""
    jobs = JobExecutor()
    seen: list[str] = []

    def _boom(_v) -> None:
        raise RuntimeError("回调故意失败")

    jobs.submit(lambda: 1, on_success=_boom)
    jobs.submit(lambda: 2, on_success=lambda _v: seen.append("after"))

    pump(2.0, until=lambda: "after" in seen)
    jobs.shutdown(wait=1)
    assert seen == ["after"], f"回调异常拖垮了后续投递: {seen}"
    print("3. 回调异常不拖垮后续投递 OK")


test_delivery_during_modal_loop()
test_order_preserved()
test_callback_error_is_contained()

print("\nDISPATCH REGRESSION ALL PASS")
