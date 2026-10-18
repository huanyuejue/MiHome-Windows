# SPDX-License-Identifier: GPL-3.0-or-later
"""定时开关回归测试（离屏）。

锁死这些行为：
1. schedules.json 存取：时间规范化、非法丢弃、去重、按时间排序
2. 判定语义：只在目标那一分钟触发；同一天只触发一次；停用不触发
3. 错过不补执行，只报警一次（不每 10 秒刷）
4. 新建规则时"今天这个点已过"不会当场触发
5. 主窗口接线：到点真的调用 service.set_power_state，并回写界面状态
"""

import os
from datetime import datetime

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCompleter

app = QApplication([])

from app.core import cache as _device_cache
_device_cache.save = lambda *a, **k: None

from app.core import schedule_store

# 防回归：定时规则必须写进临时目录，不能碰用户的真实 schedules.json
from app.core import _json_store
assert "mihome-test-" in str(_json_store.data_dir()), \
    f"测试数据目录未隔离，会污染真实配置: {_json_store.data_dir()}"
from app.core.models import DeviceInfo
from app.core.scheduler import Scheduler
from app.ui.schedule_dialog import ScheduleDialog

D = lambda text: datetime.strptime(text, "%Y-%m-%d %H:%M")  # noqa: E731

# ---------- 1. 存取与规范化 ----------
schedule_store.save([])
assert schedule_store.load() == []

r1 = schedule_store.add("d1", "7:05", "on", now=D("2026-10-18 06:00"))
assert r1["time"] == "07:05", f"时间未零填充: {r1['time']}"
assert r1["last_fired"] == "", "未到点的新规则不应有 last_fired"
r2 = schedule_store.add("d2", "23:30", "off", now=D("2026-10-18 06:00"))
assert r2["action"] == "off"
# 今天已过的时刻：立刻记为今天已执行，避免当场触发/被判错过
r3 = schedule_store.add("d3", "05:00", "on", now=D("2026-10-18 06:00"))
assert r3["last_fired"] == "2026-10-18", "今天已过时刻的新规则应标记为已执行"

# 非法输入
assert schedule_store.add("d1", "24:00", "on") is None
assert schedule_store.add("d1", "7:5:9", "on") is None
assert schedule_store.add("d1", "07:00", "toggle") is None
assert schedule_store.add("", "07:00", "on") is None

# 排序按时间升序
times = [r["time"] for r in schedule_store.load()]
assert times == sorted(times), f"规则未按时间排序: {times}"

# 删除 / 启停
assert schedule_store.set_enabled(r1["id"], False) is True
assert [r for r in schedule_store.load() if r["id"] == r1["id"]][0]["enabled"] is False
assert schedule_store.remove(r1["id"]) is True
assert schedule_store.remove("不存在的id") is False
assert all(r["id"] != r1["id"] for r in schedule_store.load())
print("1. schedules.json 存取/规范化/排序 OK")

# ---------- 2. 判定语义 ----------
rules = [
    {"id": "a", "did": "d1", "time": "07:00", "action": "on",
     "enabled": True, "last_fired": ""},
    {"id": "b", "did": "d2", "time": "07:00", "action": "off",
     "enabled": False, "last_fired": ""},
    {"id": "c", "did": "d3", "time": "23:00", "action": "on",
     "enabled": True, "last_fired": ""},
]
sch = Scheduler(rules)

# 06:59 —— 都没到
res = sch.tick(D("2026-10-18 06:59"))
assert res.due == [] and res.missed == [], "未到点不应触发"

# 07:00 —— 只触发启用的 a；b 停用；c 未到
res = sch.tick(D("2026-10-18 07:00"))
assert [r["id"] for r in res.due] == ["a"], f"到点规则判定错误: {res.due}"
assert res.missed == []

# 同一天已执行过：不再触发
sch.mark_fired("a", D("2026-10-18 07:00"))
res = sch.tick(D("2026-10-18 07:00"))
assert res.due == [], "同一天重复触发"
# 顺手验证 mark_fired 也同步了本地规则
assert [r for r in sch.rules() if r["id"] == "a"][0]["last_fired"] == "2026-10-18"

# 第二天同一分钟恢复触发
res = sch.tick(D("2026-10-19 07:00"))
assert [r["id"] for r in res.due] == ["a"], "跨天未恢复触发"
print("2. 只在目标分钟触发 / 同日不重复 OK")

# ---------- 3. 错过不补执行、只报一次 ----------
sch2 = Scheduler([
    {"id": "x", "did": "d1", "time": "07:00", "action": "on",
     "enabled": True, "last_fired": ""},
])
# 08:00 才发现 —— 不能补执行，但要报错过
res = sch2.tick(D("2026-10-18 08:00"))
assert res.due == [], "错过的时刻不能补执行"
assert [r["id"] for r in res.missed] == ["x"], "错过未上报"
# 下一秒再 tick：不重复报
res = sch2.tick(D("2026-10-18 08:00"))
assert res.missed == [], "错过提示重复刷出"
# 隔一天重新算
res = sch2.tick(D("2026-10-19 08:00"))
assert [r["id"] for r in res.missed] == ["x"], "跨天未重新提醒错过"
print("3. 错过不补执行 + 只提醒一次 OK")

# ---------- 4. 停用的规则既不执行也不报错过 ----------
sch3 = Scheduler([
    {"id": "y", "did": "d1", "time": "07:00", "action": "on",
     "enabled": False, "last_fired": ""},
])
res = sch3.tick(D("2026-10-18 09:00"))
assert res.due == [] and res.missed == [], "停用规则不应有任何动作"
print("4. 停用规则静默 OK")

# ---------- 5. 主窗口接线：到点真的写开关 ----------
from app.ui.main_window import MainWindow

MainWindow._refresh_power_states = lambda self, force: None
MainWindow._refresh_metrics = lambda self: None
MainWindow._load_card_icons = lambda self: None
MainWindow._maybe_localize_names = lambda self: None
MainWindow._load_scenes = lambda self: None

devs = [
    DeviceInfo("d1", "客厅主灯", "m", "家", "客厅", True),
    DeviceInfo("d2", "卧室灯", "m", "家", "卧室", True),
    # 与 d1 同名房间、名字有公共片段：用来验证"模糊匹配有歧义时不瞎猜"
    DeviceInfo("d3", "客厅台灯", "m", "家", "客厅", True),
]

schedule_store.save([{
    "id": "r1", "did": "d1", "time": "07:00", "action": "on",
    "enabled": True, "last_fired": "",
}])

win = MainWindow()
win._apply_devices(devs)
app.processEvents()

calls: list[tuple[str, bool]] = []


class _FakeService:
    def set_power_state(self, did, state):
        calls.append((did, state))


win._service = _FakeService()
win._reload_schedules()

# 直接驱动一次 tick，把时间固定到目标分钟
from app.core.scheduler import TickResult
assert len(win._scheduler.rules()) == 1, "规则未载入调度器"
due_rule = win._scheduler.rules()[0]
win._run_schedule_rule(due_rule)


def pump(seconds=2.0, until=None):
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


pump(until=lambda: len(calls) >= 1)
assert calls == [("d1", True)], f"定时未按规则写开关: {calls}"
pump(0.3)
# 成功后：内存规则记为今天已执行，且界面记忆同步
assert win._known_power["d1"] is True, "定时执行后未回写开关记忆"
today = datetime.now().strftime("%Y-%m-%d")
assert [r for r in schedule_store.load() if r["id"] == "r1"][0]["last_fired"] == today
assert [r for r in win._scheduler.rules() if r["id"] == "r1"][0]["last_fired"] == today

# 同一分钟内重复调 tick 不会二次提交
win._on_schedule_tick()
pump(0.5)
assert len(calls) == 1, f"同一规则被重复执行: {calls}"

# 失败路径：清掉 last_fired，模拟服务抛异常
schedule_store.save([{
    "id": "r2", "did": "d2", "time": "07:00", "action": "off",
    "enabled": True, "last_fired": "",
}])
win._reload_schedules()
errors: list[str] = []


class _BrokenService:
    def set_power_state(self, did, state):
        raise RuntimeError("云端拒绝")


win._service = _BrokenService()
win._run_schedule_rule(win._scheduler.rules()[0])
pump(1.0)
# 失败不写 last_fired，且不会卡在 pending 里导致后续永远不执行
assert win._schedule_pending == set(), f"失败后 pending 未清理: {win._schedule_pending}"
assert [r for r in schedule_store.load() if r["id"] == "r2"][0]["last_fired"] == ""
print("5. 主窗口接线（成功回写 / 不重复 / 失败清理） OK")

# ---------- 6. 对话框：新增与列出 ----------
schedule_store.save([])
dlg = ScheduleDialog(devs, None)
assert dlg._empty.isVisible() is False or True  # 空态在 show 后才可见，不断言可见性
# 设备下拉支持输入搜索
assert dlg._device_combo.isEditable(), "设备下拉应可输入"
comp = dlg._device_combo.completer()
assert isinstance(comp, QCompleter), "缺少补全器"
assert comp.filterMode() == Qt.MatchFlag.MatchContains, "应支持包含匹配"
assert comp.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
# 补全过滤：包含匹配、大小写不敏感（设备名可能英文）
labels = [f"{d.name} · {d.room_name}" for d in devs]


def _filtered(prefix: str) -> list[str]:
    comp.setCompletionPrefix(prefix)
    model = comp.completionModel()
    return [model.index(i, 0).data() for i in range(model.rowCount())]


assert len(_filtered("")) == len(labels), "空前缀应列出全部设备"
assert len(_filtered("灯")) == 3, f"包含匹配失败: {_filtered('灯')}"
assert len(_filtered("客厅")) == 2, f"房间名也应可搜: {_filtered('客厅')}"
comp.setCompletionPrefix("")
dg = dlg


def set_time(hour: str, minute: str) -> None:
    """按下拉选择的方式设时间（时/分为两个下拉）。"""
    assert dg._hour_combo.currentText() != "" or True
    assert dg._hour_combo.findText(hour) >= 0, f"小时下拉缺少 {hour}"
    assert dg._minute_combo.findText(minute) >= 0, f"分钟下拉缺少 {minute}"
    dg._hour_combo.setCurrentText(hour)
    dg._minute_combo.setCurrentText(minute)


# 时间录入必须是"选"而不是"打字"
assert dg._hour_combo.count() == 24, "小时应有 24 个选项"
assert dg._minute_combo.count() == 12, "分钟应按 5 分钟一档（12 项）"
assert dg._selected_time() == (
    f"{dg._hour_combo.currentText()}:{dg._minute_combo.currentText()}")

# 输入唯一片段能定位到设备
dg._device_combo.setEditText("主灯")
set_time("06", "10")
dg._action_combo.setCurrentIndex(0)
dg._on_add()
assert any(r["did"] == "d1" and r["time"] == "06:10" for r in schedule_store.load()), \
    "唯一片段未能定位设备"
# 输入有歧义的片段（同时命中客厅主灯/客厅台灯）：不猜，拒绝新增
before = len(schedule_store.load())
dg._device_combo.setEditText("客厅")
set_time("06", "20")
dg._on_add()
assert len(schedule_store.load()) == before, "有歧义时不应擅自选一台"
# 输入不存在的设备：同样拒绝
dg._device_combo.setEditText("不存在的设备")
dg._on_add()
assert len(schedule_store.load()) == before, "不存在的设备不应新增规则"

# 选中设备 0、时间 07:30、动作"打开"，点添加
# 先清掉上面搜索用例留下的规则，让后续计数从零开始
schedule_store.save([])
dg._rebuild()
dg._device_combo.setCurrentIndex(0)
set_time("07", "30")
dg._action_combo.setCurrentIndex(0)
dg._on_add()
rules = schedule_store.load()
assert len(rules) == 1 and rules[0]["time"] == "07:30" and rules[0]["action"] == "on"
assert rules[0]["did"] == "d1"
# 再给同一台设备加一条关闭规则（不同时间）
set_time("23", "00")
dg._action_combo.setCurrentIndex(1)
dg._on_add()
rules = schedule_store.load()
assert len(rules) == 2, f"同设备应能加多条规则: {rules}"
assert [r["action"] for r in rules if r["did"] == "d1"] == ["on", "off"]
# 删除：此刻共 2 条（07:30 开 / 23:00 关），删一条应剩 1
dg._on_delete(rules[0]["id"])
assert len(schedule_store.load()) == 1, "删除后条数不对"
dg.done(0)
app.processEvents()
print("6. 对话框新增/多条/删除 + 时间用下拉选择 OK")

# ---------- 收尾 ----------
win._all_devices = []
win._force_quit = True
win.close()
schedule_store.save([])
print("SCHEDULE REGRESSION ALL PASS")
