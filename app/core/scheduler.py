# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""定时开关的判定逻辑（纯函数，不依赖 Qt，便于离屏测试）。

设计取舍：**只认"正点那一分钟"，不补执行**。

应用关掉或电脑休眠时错过的定时，醒来后不会补开灯——白天突然亮灯
比不亮更烦人。错过会单独报出来，让用户知道"今天这条没执行"，而不是
悄悄跳过。

因此判定条件是「当前 HH:MM 等于规则时刻」而不是「已过时刻」：
- 应用在 6:59 启动，7:00 的规则会在 7:00 那一分钟内触发；
- 应用在 8:30 启动，7:00 的规则今天已经错过 → 只报"错过"，不执行。

同一天只执行一次由规则里的 last_fired 保证（落盘，重启也不会重复）。
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TickResult:
    """一次判定的结果。"""

    due: list[dict]      # 现在该执行的规则
    missed: list[dict]   # 今天已错过、且本次首次报出的规则


class Scheduler:
    """持有规则并按分钟判定，不做任何 IO（执行与落盘由调用方负责）。"""

    def __init__(self, rules: list[dict] | None = None):
        self._rules: list[dict] = list(rules or [])
        # 已报过"错过"的 (规则 id, 日期)：同一天只提醒一次，避免每 10 秒刷一条
        self._reported_miss: set[tuple[str, str]] = set()

    def set_rules(self, rules: list[dict]) -> None:
        self._rules = list(rules or [])

    def rules(self) -> list[dict]:
        return list(self._rules)

    def tick(self, now: datetime | None = None) -> TickResult:
        """按当前时刻判定该执行 / 该报错过的规则。"""
        moment = now or datetime.now()
        today = moment.strftime("%Y-%m-%d")
        hhmm = moment.strftime("%H:%M")
        due: list[dict] = []
        missed: list[dict] = []
        for rule in self._rules:
            if not rule.get("enabled", True):
                continue
            if rule.get("last_fired") == today:
                continue  # 今天已经执行过
            rule_time = rule.get("time", "")
            if rule_time == hhmm:
                due.append(rule)
            elif rule_time < hhmm:
                key = (str(rule.get("id", "")), today)
                if key not in self._reported_miss:
                    self._reported_miss.add(key)
                    missed.append(rule)
        return TickResult(due=due, missed=missed)

    def mark_fired(self, rule_id: str, now: datetime | None = None) -> None:
        """本地同步 last_fired：调用方成功执行后立刻调，避免下一次 tick 重复触发。"""
        today = (now or datetime.now()).strftime("%Y-%m-%d")
        for rule in self._rules:
            if str(rule.get("id")) == str(rule_id):
                rule["last_fired"] = today
                return
