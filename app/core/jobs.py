# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""串行任务执行器：所有米家网络调用都排进这一个后台线程。

不采用并发的原因：上游库的 requests.Session 没有线程安全承诺，且每次
操作自带防限流延时，并发只会带来竞态而不会更快。单线程队列天然节流；
结果回到主线程执行，界面永不因网络等待而卡顿。

**线程与投递方式的两个坑（都踩过）**

1. 曾把 Python 函数挂到 `QThread.run`，主线程 `QThread.wait()` 结束时会
   在 Windows 上偶发访问越界（崩在 `wait()` 内，worker 还在 `queue.get()`）。
   QThread 的析构要求线程由 Qt 自行退出，Python 侧保证不了；改用 Python
   守护线程后，退出由 `threading.Event` 控制、`join` 是纯 Python 语义，
   进程退出时守护线程随进程结束，不会再有
   "QThread: Destroyed while thread is still running" 的中止。
2. 曾从 worker 线程直接 `Signal.emit()` 回主线程，退出阶段偶发访问越界。
   现在 worker **只把结果塞进一个线程安全队列**，由主线程上的 QTimer
   每 16ms 取走并执行回调——不跨线程发信号，不依赖 Qt 的排队连接时序。

**队列带优先级**：同一个串行队列里既有"用户点了开关，在等结果"这种
必须快的活，也有"抓图标、查产品页中文名"这种可以慢的杂活。纯 FIFO
时后者会把前者堵在后面（实测产品页单次 2.4 秒，一批就是十几秒），
用户表现为"点了开关半天没反应"。因此按优先级出队：

    PRIORITY_CONTROL  用户直接操作（开关、动作、语音），最先执行
    PRIORITY_DATA     数据轮询（设备列表、开关状态、温湿度读数）
    PRIORITY_IDLE     后台杂活（图标、产品页名称、版本检查）

优先级只能让**排队中**的任务插队，不能打断**正在执行**的任务：
真正长耗时的杂活（外部网页抓取）请另起一个 JobExecutor 分线程跑，
见 MainWindow._web_jobs。
"""

import itertools
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, NamedTuple

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

# 优先级数值越小越先执行
PRIORITY_CONTROL = 0
PRIORITY_DATA = 1
PRIORITY_IDLE = 2

# 结果投递间隔：16ms ≈ 一帧，用户完全感知不到，同时避免空转
_DELIVER_INTERVAL_MS = 16


class _Task(NamedTuple):
    fn: Callable[[], Any]
    on_success: Callable[[Any], None] | None
    on_error: Callable[[Exception], None] | None


class JobExecutor(QObject):
    """提交同步函数到后台线程执行，回调自动回到主线程。

    用法:
        jobs.submit(service.list_devices,
                    on_success=self._fill_list,
                    on_error=self._show_error)
        # 用户操作要插队时显式给优先级
        jobs.submit(lambda: service.toggle_power(did),
                    on_success=..., priority=PRIORITY_CONTROL)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 任务队列条目：(优先级, 入队序号, _Task|None)
        # 同优先级按入队顺序先进先出
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop = threading.Event()
        # 结果队列：(回调, 值)；worker 只入队，主线程定时器负责执行
        self._results: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._work_loop, name="mihome-jobs", daemon=True)
        # 定时器必须属于主线程（本对象在界面层创建，即主线程）
        self._timer = QTimer(self)
        self._timer.setInterval(_DELIVER_INTERVAL_MS)
        self._timer.timeout.connect(self._deliver_results)
        self._timer.start()
        self._thread.start()

    def submit(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        priority: int = PRIORITY_DATA,
    ) -> None:
        self._queue.put((priority, next(self._seq), _Task(fn, on_success, on_error)))

    def shutdown(self, wait: float = 0.0) -> None:
        """请求退出；默认**不阻塞等待**。

        线程是 daemon：置标志 + 投毒丸后它会在下一个任务边界自行结束，
        进程退出时残留也无害。这里刻意不 join —— 在解释器关闭阶段
        等待后台线程（它可能正卡在网络调用里）实测会在 Windows 上偶发
        访问越界；不 join 反而稳定（连跑 3 次全绿）。
        需要"确保已停下"的场景（如单元测试）显式传 wait>0。
        """
        self._stop.set()
        self._queue.put((PRIORITY_CONTROL, next(self._seq), None))
        try:
            self._timer.stop()
        except Exception:
            pass
        if wait > 0 and self._thread.is_alive():
            self._thread.join(wait)

    def _work_loop(self) -> None:
        while True:
            _priority, _seq, task = self._queue.get()
            if task is None or self._stop.is_set():
                return
            try:
                result = task.fn()
            except Exception as exc:  # 后台线程的异常必须全部拦下转成回调
                # 单行警告即可：设备离线、spec 缺失这类失败是常态，
                # 全栈日志只会淹没真正需要关注的问题
                logger.warning("后台任务失败: %s", exc)
                self._results.put((task.on_error, exc))
            else:
                self._results.put((task.on_success, result))

    def _deliver_results(self) -> None:
        """主线程槽：取出 worker 攒下的结果并执行回调。

        **回调一律再排一拍执行**，不在这里直接调用。原因是一次真实事故：
        回调里开了模态窗口（扫码登录），`exec()` 在投递定时器的栈上又起了一层
        事件循环，而"取二维码"那个任务的结果还要靠同一个定时器送回来——
        定时器被自己的回调堵住，界面永远停在「正在获取登录二维码…」。
        推迟一拍后，回调不在定时器栈上，里面开多久的模态窗都不影响投递。
        """
        while True:
            try:
                callback, value = self._results.get_nowait()
            except queue.Empty:
                return
            if callback is None:
                continue
            # singleShot(0) 保序：先入队的先执行
            QTimer.singleShot(0, self, lambda cb=callback, v=value: self._invoke(cb, v))

    @staticmethod
    def _invoke(callback: Callable[[Any], None], value: Any) -> None:
        try:
            callback(value)
        except Exception:
            # 回调异常不能拖垮投递流程（一个坏了后面全不投）
            logger.exception("任务回调执行失败")
