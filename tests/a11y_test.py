# SPDX-License-Identifier: GPL-3.0-or-later
"""无障碍桥接层（app/ui/a11y.py）离屏回归测试。

覆盖九件事：
1. 读屏能读到：图标按钮、设备/场景卡片、电源三态、设置开关、房间选项卡、
   状态胶囊、对话框标题都有可访问名称，且状态变化会同步。
2. 读屏能操作：自绘卡片可聚焦，空格/回车转发到**既有**信号或方法。
3. 一台设备在 Tab 链里只占一站：卡片内电源钮、场景运行钮、电源行圆钮
   不重复获焦；托盘/挂件里没有可聚焦父卡片的电源钮仍保留原焦点；
   所有页面（含主界面、调整标签顺序）的无名滚动容器都不占只读
   「分组」的空停靠点，有可读名称的容器保留原焦点。
4. 普通用户不受影响：样式表、尺寸、光标、鼠标事件与既有信号连接全部
   保持原样，鼠标点击路径（含点击电源钮）仍与改造前一致；托盘行忙碌
   期间的重复点击与原 disabled 状态等价地无效。
5. 文案只补缺失的文字：名称里不重复控件类型、选中态和操作提示，
   accessibleDescription（UIA HelpText）恒为空。
6. 焦点按钮不再被 Qt 报成 Enter 快捷键（读屏不再多念「回车」），
   且回车 / 空格 / 鼠标三条激活路径与改造前完全一致。
7. 「添加设备功能」抽屉里 + / ✓ 按钮带上功能标题与当前动作，工作台的
   ↑ / ↓ / ✕ 与桌面挂件勾选钮也不再只念一个符号；「调整标签顺序」里
   每行的「设为默认」按钮补上所属房间名；托盘快捷窗口的电源钮补上
   设备名与房间、场景钮补上场景名与家庭；显示文字与信号都不变。
   托盘快捷窗口还要稳住 Tab 焦点：设备轮询不再无谓整窗重建、旧行退出
   Tab 链、重建后焦点回到同一台设备；忙碌时让焦点一步都不漂移（读屏
   不再先念下一项再跳回来），空格与回车不误切设备、也不重复激活。
   托盘小爱音频栏的「上一首 / 播放 / 下一首」与音量条同样可 Tab 聚焦、
   空格回车激活、方向键调音量并去抖下发一次；忙碌禁用不抢焦点，鼠标
   路径的业务禁用照旧，音量数值由原生 RangeValue 提供故不写进名称。
8. 设定温度 / 亮度 / 色温等数值滑块能用键盘操作：可 Tab 聚焦、方向键
   改值、停止按键后按原有「松手才提交」语义下发一次，当前值补进名称；
   滚动条不被误伤，鼠标路径不变。
9. 定时任务窗口不再把 tooltip 追加朗读（Qt 会把它回退成 UIA
   FullDescription）：只在该窗口内设空白描述占位，视觉 tooltip 与
   HelpText 都不变；四个无名下拉框补上缺失的用途名。下拉框末尾的
   「下方向」也不再读：代理接口只把 Accelerator 报空，名称、当前值、
   描述、子节点、角色与状态全部委托给 Qt 原生接口。

与项目其它 GUI 测试一样：离屏运行、数据目录隔离、不联网、不需要米家账号。

    .venv\\Scripts\\python.exe -m tests.a11y_test
"""

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollBar,
    QWidget,
)

app = QApplication([])

from app.core import cache as _device_cache

_device_cache.save = lambda *a, **k: None

from app.core import _json_store

assert "mihome-test-" in str(_json_store.data_dir()), \
    f"测试数据目录未隔离，会污染真实配置: {_json_store.data_dir()}"

from app.core.models import DeviceInfo, PropInfo, SceneInfo
from app.ui.a11y import (
    _TITLE_BAR_GLYPHS,
    _power_state,
    _readable,
    install_accessibility,
)
from app.ui.device_card import DeviceCard
from app.ui.power_button import PowerButton
from app.ui.prop_widgets import EnumGridSection, PowerRowSection, SliderSection
from app.ui.scene_card import SceneCard
from app.ui.si_theme import themed_switch, themed_tab_button


class MainWindow(QMainWindow):
    """模拟主窗口：给 live region 与全局快捷键提供宿主。"""


host = MainWindow()
host.resize(900, 600)
host.show()

bridge = install_accessibility(app)
app._a11y_bridge = bridge  # announce_static 依赖此引用


def key_press(widget: QWidget, key: int) -> None:
    """走真实的 QApplication.sendEvent，确保全局事件过滤器确实生效。"""
    event = QKeyEvent(QEvent.Type.KeyPress, key,
                      Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def key_tap(widget: QWidget, key: int) -> None:
    """完整按下 + 松开；Qt 按钮在 keyReleaseEvent 里处理空格。"""
    for etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(
            widget, QKeyEvent(etype, key, Qt.KeyboardModifier.NoModifier))


def mouse_click(widget: QWidget) -> None:
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, press)
    QApplication.sendEvent(widget, release)


def find_live_region():
    for w in QApplication.allWidgets():
        if w.objectName() == "a11yLiveRegion":
            return w
    return None


# ---------- 1. 纯函数：字形与三态文案 ----------
assert _readable("客厅灯")
assert not _readable("   ")
assert not _readable("\ue921"), "PUA 图标字形不应被当成可读文字"
assert not _readable("+") and not _readable("✓") and not _readable("↑"), \
    "纯装饰符号不是可读文字，读屏只会念「+ 按钮」"
assert _readable("+ 添加功能"), "符号后面带文字时仍算可读"
assert _TITLE_BAR_GLYPHS["\ue8bb"] == "关闭窗口"
assert _TITLE_BAR_GLYPHS["\ue921"] == "最小化窗口"

probe = PowerButton(36)
assert _power_state(probe) == "状态未知"
probe.set_state(True)
assert _power_state(probe) == "已开启"
probe.set_state(False)
assert _power_state(probe) == "已关闭"
probe.set_busy(True)
assert _power_state(probe) == "正在操作"
probe.set_busy(False)
probe.set_online(False)
assert _power_state(probe) == "离线"
print("[1/9] 电源三态与图标字形文案 OK")


# ---------- 2. 图标按钮：tooltip 变成可访问名称 ----------
icon_btn = QPushButton()
icon_btn.setToolTip("刷新设备")
glyph_btn = QPushButton("\ue8bb")
pill = QPushButton()
pill.setToolTip("当前范围的温度")
pill.setText("26℃")
bridge.scan()
assert icon_btn.accessibleName() == "刷新设备", icon_btn.accessibleName()
assert glyph_btn.accessibleName() == "关闭窗口", glyph_btn.accessibleName()
assert "26℃" in pill.accessibleName(), pill.accessibleName()
assert "当前范围的温度" in pill.accessibleName()
# QScrollBar 也是 QAbstractSlider，不能被误当成数值滑块命名
scroll_bar = QScrollBar()
bridge.scan()
assert scroll_bar.accessibleName() == "", \
    f"滚动条被误命名: {scroll_bar.accessibleName()}"
print("[2/9] 图标按钮 / 字形按钮 / 状态胶囊名称 OK")


# ---------- 3. 设备卡片：名称、状态同步、焦点策略 ----------
device = DeviceInfo(
    did="d1", name="客厅灯", model="demo.light",
    home_name="我的家", room_name="客厅", online=True,
)
card = DeviceCard(device)
card.show()
card.set_power_state(True)

before = {
    "stylesheet": card.styleSheet(),
    "size": (card.width(), card.height()),
    "radius": card.style_data.border_radius,
    "cursor": card.cursor().shape(),
    "mouse_patched": "mouseReleaseEvent" in card.__dict__,
    "press_patched": "mousePressEvent" in card.__dict__,
}
bridge.scan()

name = card.accessibleName()
assert "客厅灯" in name and "客厅" in name and "在线" in name, name
assert "已开启" in name, name
assert "按回车" not in name and "按空格" not in name, name
assert card.focusPolicy() != Qt.FocusPolicy.NoFocus, "卡片必须能进 Tab 链"
assert card.property("_a11y_activatable") is True
assert not card.accessibleDescription(), "卡片不该写 HelpText"

power = card._power_btn
power_name = power.accessibleName()
assert "客厅灯" in power_name and "电源开关" in power_name, power_name
assert "已开启" in power_name, power_name

# 用户实测：卡片和卡片内电源钮原本是两个功能重复的 Tab 焦点。
# 现在只保留卡片，电源钮退出 Tab 链，但名称仍在 UIA 树里、鼠标仍可点。
assert power.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    f"卡片内电源钮不应再占一个 Tab 焦点: {power.focusPolicy()}"
assert not power.accessibleDescription(), power.accessibleDescription()

# 托盘快捷行 / 桌面挂件里的电源钮没有可聚焦父卡片，必须原样保留
standalone = PowerButton(36)
standalone.show()
bridge.scan()
assert standalone.focusPolicy() != Qt.FocusPolicy.NoFocus, \
    "独立电源钮（托盘/挂件）是当地唯一键盘入口，不能被误改"
assert not standalone.accessibleDescription(), standalone.accessibleDescription()

card.set_power_state(False)
bridge.scan()
assert "已关闭" in card.accessibleName(), card.accessibleName()
assert "已关闭" in power.accessibleName(), power.accessibleName()
card.set_power_state(True)
bridge.scan()
print("[3/9] 设备卡片名称 / 状态同步 / 焦点策略 OK")


# ---------- 4. 零侵入：普通用户的视觉与鼠标路径逐项不变 ----------
assert card.styleSheet() == before["stylesheet"], "样式表被改动"
assert (card.width(), card.height()) == before["size"], "尺寸被改动"
assert card.style_data.border_radius == before["radius"], "自绘样式被改动"
assert card.cursor().shape() == before["cursor"], "鼠标光标被改动"
assert ("mouseReleaseEvent" in card.__dict__) == before["mouse_patched"], \
    "桥接层不应替换鼠标事件"
assert ("mousePressEvent" in card.__dict__) == before["press_patched"]
assert "mouseReleaseEvent" not in card.__dict__, \
    "桥接层给卡片打了鼠标事件猴补丁"
assert "mousePressEvent" not in card.__dict__
print("[4/9] 样式 / 尺寸 / 光标 / 鼠标事件零改动 OK")


# ---------- 5. 键盘等价与鼠标路径共存 ----------
opened: list[str] = []
switched: list[str] = []
card.open_requested.connect(lambda did: opened.append(did))
card.power_clicked.connect(lambda did: switched.append(did))

key_press(card, Qt.Key.Key_Return)
assert opened == ["d1"], f"回车应打开详情: {opened}"
assert switched == [], "回车不应触发电源"

key_press(card, Qt.Key.Key_Space)
assert switched == ["d1"], f"空格应切换电源: {switched}"

# 鼠标路径：按下 + 松手仍然走原来的 mouseReleaseEvent → open_requested
opened.clear()
mouse_click(card)
assert opened == ["d1"], f"鼠标点击打开详情的路径被破坏: {opened}"

# NoFocus 只影响 Tab 分配：直接用鼠标点电源钮，clicked → power_clicked
# 必须照常触发，和键盘按空格走的是同一个信号
switched.clear()
mouse_click(power)
assert switched == ["d1"], f"NoFocus 后鼠标点电源钮失效: {switched}"
print("[5/9] 键盘激活与鼠标路径（含点击电源钮）各自正常 OK")


# ---------- 6. 场景卡片与电源属性行 ----------
scene = SceneInfo(scene_id="s1", name="晚安模式", home_id="h1",
                  home_name="我的家")
scene_card = SceneCard(scene)
scene_card.show()
bridge.scan()
assert "晚安模式" in scene_card.accessibleName()
assert "按回车" not in scene_card.accessibleName()
assert scene_card.focusPolicy() != Qt.FocusPolicy.NoFocus
assert scene_card._run_btn.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "场景运行钮与场景卡片功能重复，不应再占一个 Tab 焦点"
assert "执行场景" in scene_card._run_btn.accessibleName()
executed: list[str] = []
scene_card.executed.connect(lambda sid: executed.append(sid))
key_press(scene_card, Qt.Key.Key_Return)
assert executed == ["s1"], f"场景回车应执行: {executed}"

prop = PropInfo(name="power", desc="开关", type="bool",
                readable=True, writable=True)
writes: list[bool] = []
section = PowerRowSection(prop, lambda v: writes.append(v))
section.show()
bridge.scan()
assert "开关" in section.accessibleName(), section.accessibleName()
assert "已关闭" in section.accessibleName()
assert section.focusPolicy() != Qt.FocusPolicy.NoFocus
assert section._circle.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "详情页电源行的圆钮与整行功能重复，不应再占一个 Tab 焦点"
key_press(section, Qt.Key.Key_Space)
assert writes == [True], f"电源行空格应切换: {writes}"
bridge.scan()
assert "已开启" in section.accessibleName(), section.accessibleName()

# 数值滑块与枚举模式：区块标题、滑块值、模式按钮都要可读
slider_prop = PropInfo(name="brightness", desc="亮度", type="int",
                       readable=True, writable=True, range=(0, 100, 1))
slider_section = SliderSection(slider_prop, lambda v: None)
slider_section.show()
enum_prop = PropInfo(
    name="mode", desc="运行模式", type="int", readable=True, writable=True,
    value_list=[{"value": 1, "desc_zh_cn": "制冷"},
                {"value": 2, "desc_zh_cn": "制热"}])
enum_writes = []
enum_section = EnumGridSection(enum_prop, enum_writes.append)
enum_section.show()
enum_styles_before = [b.styleSheet() for b in enum_section._buttons]
bridge.scan()
assert "亮度" in slider_section.accessibleName()
assert "亮度" in slider_section._slider.accessibleName(), \
    slider_section._slider.accessibleName()
assert "运行模式" in enum_section.accessibleName()
assert any("制冷" in b.accessibleName() and "模式选项" not in b.accessibleName()
           for b in enum_section._buttons), \
    [b.accessibleName() for b in enum_section._buttons]

# 枚举模式选中态：业务只把它画在背景色里，Qt 的 UIA 原本拿不到，
# 四个按钮读起来一模一样。现在按钮是原生 checkable，读屏自己念
# 「已选中 / 未选中」；名称里仍只有模式名，一个状态词都不加。
from PySide6.QtGui import QAccessible  # noqa: E402

assert all(b.isCheckable() for b in enum_section._buttons), \
    "模式按钮没有 checkable，UIA 报不出选中态"
assert not any(b.isChecked() for b in enum_section._buttons), \
    "设备尚未回读时不应有选中项"
assert [b.styleSheet() for b in enum_section._buttons] == enum_styles_before, \
    "设置 checkable 改动了业务样式表"
assert all(b.accessibleName() in ("制冷", "制热")
           for b in enum_section._buttons), \
    [b.accessibleName() for b in enum_section._buttons]

# 用户实机反馈：设成 checkable 后「Tab 只能切换到一个模式」。业务把这些
# 按钮放进 QButtonGroup，Qt 一旦发现组里的按钮变成可选中，就按单选组处理，
# 未选中项不再进入 Tab 链，setChecked 还会清掉 TabFocus 位。桥接层在设
# checkable 之前先把按钮从组里摘出，四个模式必须重新都能逐个 Tab 到。
assert all(b.group() is None for b in enum_section._buttons), \
    "模式按钮仍在 QButtonGroup 里，Tab 只会停在一个模式上"
assert all(int(b.focusPolicy()) & int(Qt.FocusPolicy.TabFocus)
           for b in enum_section._buttons), \
    "模式按钮丢了 TabFocus 位"


def enum_tab_order(section, count=3):
    """沿 Qt 的 Tab 焦点链（focusNextPrevChild）依次记录停靠的控件。"""
    previous = QApplication.activeWindow()
    QApplication.setActiveWindow(section)
    try:
        section._buttons[0].setFocus()
        stops = []
        for _ in range(count):
            cur = QApplication.focusWidget()
            assert cur is not None, "焦点链里没有控件"
            stops.append(cur)
            cur.focusNextPrevChild(True)
        return stops
    finally:
        QApplication.setActiveWindow(previous)


expected_tab = [enum_section._buttons[0], enum_section._buttons[1],
                enum_section._buttons[0]]
assert enum_tab_order(enum_section) == expected_tab, \
    "模式按钮没有全部留在 Tab 链里"

# 枚举选中态原本只靠背景色，点击后必须有语音反馈
enum_section._buttons[0].click()
live = find_live_region()
assert live is not None and "制冷" in live.accessibleName(), \
    "枚举模式选择后没有播报"
assert enum_writes == [1], f"点击模式仍应只写入一次: {enum_writes}"
assert enum_section._buttons[0].isChecked() and not enum_section._buttons[1].isChecked(), \
    "点击制冷后选中态没有同步"
iface = QAccessible.queryAccessibleInterface(enum_section._buttons[0])
assert iface is not None and iface.state().checkable and iface.state().checked, \
    "Qt 无障碍接口没有把选中态报给 UIA"
iface_other = QAccessible.queryAccessibleInterface(enum_section._buttons[1])
assert iface_other is not None and iface_other.state().checkable \
    and not iface_other.state().checked, "未选中项仍被报成已选中"

# 换一个模式、以及设备回读刷新，都要走同一条同步路径
enum_section._buttons[1].click()
assert (not enum_section._buttons[0].isChecked()
        and enum_section._buttons[1].isChecked()), "换模式后选中态没有跟着走"
enum_section.refresh_value(1)
assert (enum_section._buttons[0].isChecked()
        and not enum_section._buttons[1].isChecked()), "回读刷新没有同步选中态"
assert enum_writes == [1, 2], f"写入路径被改动: {enum_writes}"
# 点选、换模式、回读刷新之后 Tab 链都不能退化
assert enum_tab_order(enum_section) == expected_tab, \
    "切换模式后 Tab 链又只剩一个模式"

# 扫描晚于业务首次高亮：也要能从现有样式反推出当前项
late_enum = EnumGridSection(enum_prop, lambda v: None)
late_enum.show()
late_enum.refresh_value(2)
bridge.scan()
assert all(b.isCheckable() for b in late_enum._buttons)
assert (late_enum._buttons[1].isChecked()
        and not late_enum._buttons[0].isChecked()), \
    "扫描晚于回读时选中态丢失"

# ---------- 6b. 数值滑块：键盘真的能改值并下发到设备 ----------
# 用户实测：空调「设定温度」、台灯「亮度 / 色温」在设备详情里 Tab 不到，
# 即使按键送进去也只更新预览文字，设备收不到新值。
from PySide6.QtTest import QTest  # noqa: E402
from app.ui.prop_widgets import GroupSection  # noqa: E402
from app.siui.components.slider_ import SiScrollBar  # noqa: E402


def key_tap_mod(widget, key,
                modifiers=Qt.KeyboardModifier.NoModifier):
    for etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(etype, key, modifiers))


temp_prop = PropInfo(name="target-temperature", desc="设定温度", type="int",
                     readable=True, writable=True, range=(16, 30, 1))
temp_writes: list[int] = []
temp_section = SliderSection(temp_prop, lambda v: temp_writes.append(v))
temp_section.show()
bridge.scan()
temp_slider = temp_section._slider
assert temp_slider.focusPolicy() != Qt.FocusPolicy.NoFocus, \
    "属性滑块必须能进 Tab 链（SiSlider 原本是 NoFocus）"
assert temp_slider.property("_a11y_slider") is True
# Qt 的 UIA 不给自绘滑块报 RangeValue，当前值必须由名称承载
assert temp_slider.accessibleName() == "设定温度，16", \
    temp_slider.accessibleName()
assert not temp_slider.accessibleDescription(), \
    temp_slider.accessibleDescription()

temp_slider.setFocus()
key_tap_mod(temp_slider, Qt.Key.Key_Right)
assert temp_slider.value() == 17 and temp_section._value_label.text() == "17"
key_tap_mod(temp_slider, Qt.Key.Key_Up)
assert temp_slider.value() == 18
key_tap_mod(temp_slider, Qt.Key.Key_PageUp)
assert temp_slider.value() == 28
key_tap_mod(temp_slider, Qt.Key.Key_Home)
assert temp_slider.value() == 16
key_tap_mod(temp_slider, Qt.Key.Key_End)
assert temp_slider.value() == 30
key_tap_mod(temp_slider, Qt.Key.Key_Left,
            Qt.KeyboardModifier.ShiftModifier)
assert temp_slider.value() == 20
# 到边界再按不越界；连续调整期间只预览，不下发
key_tap_mod(temp_slider, Qt.Key.Key_Home)
key_tap_mod(temp_slider, Qt.Key.Key_Left)
assert temp_slider.value() == 16
assert temp_writes == [], "连续按键期间不应连发指令"
QTest.qWait(500)
assert temp_writes == [16], f"停止按键后应只下发一次最终值: {temp_writes}"
assert temp_slider.accessibleName() == "设定温度，16", \
    temp_slider.accessibleName()

# 连按 5 次只提交一次，沿用鼠标「松手才下发」的既有语义
temp_writes.clear()
for _ in range(5):
    key_tap_mod(temp_slider, Qt.Key.Key_Right)
QTest.qWait(500)
assert temp_writes == [21], f"连按应去抖成一次提交: {temp_writes}"
assert temp_slider.accessibleName() == "设定温度，21"

# 鼠标路径照旧：松手仍走原来的 sliderReleased → _commit
temp_writes.clear()
mouse_click(temp_slider)
assert len(temp_writes) == 1, "鼠标松手提交被破坏"

# 组合卡片里的子滑块同样可用；滚动条不能被误当成属性滑块
master = PropInfo(name="ac", desc="空调开关", type="bool",
                  readable=True, writable=True)
group = GroupSection("ac", [master, temp_prop], master, lambda p, v: None)
group.show()
bridge.scan()
child = group._children[0]._slider
assert child.focusPolicy() != Qt.FocusPolicy.NoFocus
assert child.property("_a11y_slider") is True
assert "设定温度" in child.accessibleName(), child.accessibleName()
scroll = SiScrollBar()
bridge.scan()
assert scroll.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "滚动条不该被误设成可聚焦滑块"
assert not scroll.accessibleName(), scroll.accessibleName()

# 详情滚动容器是 Qt 默认 StrongFocus 的无名 QScrollArea，UIA 报成空
# GroupControl，读屏只会念「分组」。移出 Tab 链即可，不必给它编名字；
# 滚轮 / 滚动条 / 内部控件的焦点与键盘操作全部照旧。
from unittest.mock import MagicMock  # noqa: E402
from PySide6.QtWidgets import QScrollArea  # noqa: E402
from app.ui.a11y import _is_plain_scroll_area  # noqa: E402
from app.ui.room_order_dialog import RoomOrderDialog  # noqa: E402
from app.ui.workbench_panel import WorkbenchPanel  # noqa: E402

detail = WorkbenchPanel(MagicMock(), MagicMock())
detail.resize(400, 300)
detail.show()
for i in range(4):
    p = PropInfo(name=f"scroll{i}", desc=f"滚动测试{i}", type="int",
                 readable=True, writable=True, range=(1, 100, 1))
    detail._left_col.addWidget(SliderSection(p, lambda v: None))
for _ in range(10):
    app.processEvents()
bridge.scan()
detail_scroll = detail.findChild(QScrollArea)
assert detail_scroll is not None
assert _is_plain_scroll_area(detail_scroll)
assert detail_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "详情滚动容器仍在 Tab 链里，读屏会停在一个空的「分组」"
assert not detail_scroll.accessibleName(), \
    "滚动容器不需要补名称，直接移出 Tab 链即可"
sb = detail_scroll.verticalScrollBar()
assert sb.maximum() > 0, "测试用的滚动区应当真的可滚动"
sb.setValue(sb.maximum())
assert sb.value() == sb.maximum(), "滚动条被破坏"
assert temp_slider.focusPolicy() != Qt.FocusPolicy.NoFocus, \
    "不能把滚动容器内部的控件一起改成 NoFocus"

# 主界面设备网格、调整标签顺序用的是同一种无名滚动容器，同样要移出；
# 用户在这两处也听到了「分组」，规则不能只按祖先类名限定在设备详情。
room_dlg = RoomOrderDialog(["客厅", "卧室", "厨房"], parent=host)
bridge.scan()
assert _is_plain_scroll_area(room_dlg._scroll)
assert room_dlg._scroll.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "调整标签顺序的滚动容器仍在 Tab 链里"
assert room_dlg._ok_btn.focusPolicy() != Qt.FocusPolicy.NoFocus, \
    "移出滚动容器不能连对话框按钮一起改掉"
plain_scroll = QScrollArea()
plain_scroll.show()
bridge.scan()
assert _is_plain_scroll_area(plain_scroll)
assert plain_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "主界面等页面的无名滚动容器仍会读成空的「分组」"
# 有可读名称的容器说明承载语义，不受本规则影响
named_scroll = QScrollArea()
named_scroll.setAccessibleName("房间列表")
named_scroll.show()
bridge.scan()
assert not _is_plain_scroll_area(named_scroll)
assert named_scroll.focusPolicy() == Qt.FocusPolicy.StrongFocus
print("[6/9] 场景卡片 / 属性电源行 / 滑块键盘调节 / 无名滚动容器 OK")


# ---------- 7. 设置开关、房间选项卡、对话框标题 ----------
row = QWidget()
lay = QHBoxLayout(row)
lay.addWidget(QLabel("开机自启动"))
switch = themed_switch()
lay.addWidget(switch)
row.show()
bridge.scan()
assert switch.accessibleName() == "开机自启动", switch.accessibleName()
switch.setChecked(True)
bridge.scan()
# checkable 按钮的选中态由读屏朗读，名称必须保持稳定不抖动
assert switch.accessibleName() == "开机自启动", switch.accessibleName()

tab = themed_tab_button("客厅")
tab.show()
bridge.scan()
assert tab.accessibleName() == "客厅", tab.accessibleName()
tab.setChecked(True)
bridge.scan()
# 用户实测：原本「客厅 复选框 未选中」就够用，只需补上文字
assert tab.accessibleName() == "客厅", tab.accessibleName()


class SettingsDialog(QDialog):
    """与业务同名，验证无边框对话框的窗口标题补全。"""


dlg = SettingsDialog()
bridge.scan()
assert dlg.windowTitle() == "设置", dlg.windowTitle()

# 「管理托盘项目」：勾选按钮文字只有 + / ✓，读屏原本念「+ 复选框 未选中」，
# 真正的设备名在同行的 QLabel 里。用真实对话框验证名称补全与状态同步。
from app.ui.tray.manager_dialog import TrayManagerDialog  # noqa: E402

tray_devs = [DeviceInfo(did="t1", name="储藏室灯", model="demo.light",
                        home_name="我的家", room_name="储藏室", online=True)]
tray_dlg = TrayManagerDialog(tray_devs, scenes=[])
tray_dlg.show()
bridge.scan()
tray_btn = tray_dlg._checks["t1"]
tray_name = tray_btn.accessibleName()
assert "储藏室灯" in tray_name and "储藏室" in tray_name, tray_name
for noise in ("未加入托盘", "已加入托盘", "按空格", "按回车", "复选框",
              "勾选框", "+"):
    assert noise not in tray_name, f"勾选框名称含冗余文案「{noise}」: {tray_name}"
assert not tray_btn.accessibleDescription(), tray_btn.accessibleDescription()

# 同一对话框里另外两个原本 Name 为空的原生控件
assert "搜索" in tray_dlg._search.accessibleName(), \
    tray_dlg._search.accessibleName()
assert "按房间筛选" in tray_dlg._room_combo.accessibleName(), \
    tray_dlg._room_combo.accessibleName()

tray_btn.setChecked(True)
bridge.scan()
# 只把「+」换成设备名，其余（控件类型、选中态、按键提示）交给读屏
assert tray_btn.accessibleName() == tray_name, tray_btn.accessibleName()
assert tray_btn.text() == "✓", "原有 toggled 文字同步不能被改动"

# 鼠标点击路径照旧：可勾选按钮仍能被点选
tray_btn.setChecked(False)
mouse_click(tray_btn)
assert tray_btn.isChecked() is True, "托盘勾选按钮的鼠标点击被破坏"

# Qt 的 autoDefault 会让焦点按钮在 UIA 里报 Enter 快捷键（读屏念「回车」）。
# 关闭它之后，回车 / 空格 / 鼠标三条激活路径必须全部保持原样。
assert tray_btn.autoDefault() is False, "autoDefault 未关闭，读屏仍会念「回车」"
assert tray_btn.property("_a11y_enter_proxy") is True
tray_btn.setChecked(False)
key_press(tray_btn, Qt.Key.Key_Return)
assert tray_btn.isChecked() is True, "回车激活焦点按钮的行为没有被等价补回"
tray_btn.setChecked(False)
key_tap(tray_btn, Qt.Key.Key_Space)
assert tray_btn.isChecked() is True, "空格切换被破坏"
tray_btn.setChecked(False)

clicked: list[str] = []
icon_btn.clicked.connect(lambda: clicked.append("x"))
key_press(icon_btn, Qt.Key.Key_Return)
assert clicked == ["x"], "普通按钮的回车激活被破坏"

# ---------- 7b. 添加设备功能：抽屉里不再每项都念「+ 按钮」 ----------
# 用户实测：抽屉里每个按钮只有 + / ✓ 字形，整页 Tab 下去全是「+ 按钮」，
# 既分不清是哪一项，也不知道按下去是添加还是移除。
from app.ui.add_drawer import AddDrawer  # noqa: E402
from app.ui.board_manager import BoardManagerDialog  # noqa: E402
from app.ui.workbench_item import WorkbenchItemWrapper  # noqa: E402

drawer = AddDrawer(host)
drawer.set_modules([
    ("switch", "电源开关", "switch, bool, 可读可写"),
    ("brightness", "亮度", "brightness, int, 可读可写"),
], {"brightness"})
bridge.scan()

add_btn = drawer._cards["switch"]._btn
remove_btn = drawer._cards["brightness"]._btn
assert add_btn.text() == "+" and remove_btn.text() == "✓", \
    "显示文字必须保持原样，只补可访问名称"
assert add_btn.accessibleName() == "添加 电源开关", add_btn.accessibleName()
assert remove_btn.accessibleName() == "移除 亮度", remove_btn.accessibleName()
assert drawer.windowTitle() == "添加设备功能", drawer.windowTitle()
assert drawer._close_btn.accessibleName() == "关闭对话框"
# 抽屉里同样有一个无名滚动容器，别让它也变成只读「分组」的 Tab 停靠点
drawer_scroll = drawer.findChild(QScrollArea)
assert drawer_scroll is not None
assert _is_plain_scroll_area(drawer_scroll)
assert drawer_scroll.focusPolicy() == Qt.FocusPolicy.NoFocus, \
    "添加功能抽屉的滚动容器仍在 Tab 链里"
for btn in (add_btn, remove_btn):
    nm = btn.accessibleName()
    assert not btn.accessibleDescription(), nm
    assert btn.focusPolicy() != Qt.FocusPolicy.NoFocus, "按钮要留在 Tab 链里"
    for noise in ("按钮", "复选框", "按空格", "按回车", "分组", "+", "✓"):
        assert noise not in nm, f"抽屉按钮名称含冗余文案「{noise}」: {nm}"

# 状态翻转：名称同步，原有文字同步、信号与鼠标路径都不变
toggled: list[str] = []
drawer.add_module.connect(lambda k: toggled.append(k))
add_btn.click()
assert toggled == ["switch"], "抽屉按钮的既有信号被破坏"
drawer._cards["switch"].set_added(True)
bridge.scan()
assert add_btn.text() == "✓", "set_added 的文字同步不能被改动"
assert add_btn.accessibleName() == "移除 电源开关", add_btn.accessibleName()
toggled.clear()
mouse_click(add_btn)
assert toggled == ["switch"], "抽屉按钮的鼠标点击被破坏"

# 同一页面上的其它符号按钮：↑ / ↓ / ✕ 用既有 tooltip，「+ 添加功能」
# 只去掉装饰符号，不改动按钮显示文字。
item = WorkbenchItemWrapper("switch", QWidget())
footer_probe = QPushButton("+ 添加功能")
bridge.scan()
assert item._up_btn.accessibleName() == "上移", item._up_btn.accessibleName()
assert item._down_btn.accessibleName() == "下移", item._down_btn.accessibleName()
assert item._del_btn.accessibleName() == "移除", item._del_btn.accessibleName()
assert footer_probe.text() == "+ 添加功能", "显示文字被改动"
assert footer_probe.accessibleName() == "添加功能", \
    footer_probe.accessibleName()

# 同类错误：管理桌面挂件的勾选钮也是 + / ✓，复用同行设备名
board_dev = DeviceInfo(did="b1", name="储藏室灯", model="demo.light",
                       home_name="我的家", room_name="储藏室", online=True)
board_dlg = BoardManagerDialog([board_dev], host)
bridge.scan()
board_btn = board_dlg._checks["b1"]
board_name = board_btn.accessibleName()
assert "储藏室灯" in board_name and "储藏室" in board_name, board_name
assert "+" not in board_name and "✓" not in board_name, board_name
assert not board_btn.accessibleDescription(), board_btn.accessibleDescription()
board_btn.setChecked(True)
bridge.scan()
assert board_btn.accessibleName() == board_name, \
    "checkable 按钮的名称不应随选中态抖动"
assert board_btn.text() == "✓", "原有 toggled 文字同步不能被改动"

# 「调整标签顺序」每行都有一个同名的「设为默认」按钮，房间名只写在同行
# QLabel 上；读屏逐行 Tab 全念「设为默认 按钮」，必须补上所属对象。
room_buttons = {name: row._default_btn
                for name, row in room_dlg._rows.items()}
bridge.scan()
for name, btn in room_buttons.items():
    assert btn.accessibleName() == f"{name}，设为默认", \
        (name, btn.accessibleName())
    assert btn.text() == "设为默认", "按钮显示文字不能被改动"
    assert not btn.accessibleDescription(), "HelpText 必须恒为空"
    assert btn.focusPolicy() != Qt.FocusPolicy.NoFocus
assert len({b.accessibleName() for b in room_buttons.values()}) == 3, \
    "每行按钮名称必须能互相区分"

# 设为默认后按钮变成禁用的「默认」，名称跟着变；既有信号与点击路径不变
room_dlg._on_set_default("卧室")
bridge.scan()
bedroom_btn = room_dlg._rows["卧室"]._default_btn
assert bedroom_btn.text() == "默认" and not bedroom_btn.isEnabled()
assert bedroom_btn.accessibleName() == "卧室，默认", \
    bedroom_btn.accessibleName()
room_dlg._rows["厨房"]._default_btn.click()
bridge.scan()
assert room_dlg._default_tab == "厨房", "既有信号连接被破坏"
assert room_dlg._rows["厨房"]._default_btn.accessibleName() == "厨房，默认"

# ---------- 7c. 定时任务：去掉 tooltip 追加朗读，补下拉框用途名 ----------
# Qt 的 UIA 在 accessibleDescription 为空时会回退用 toolTip() 填
# UIA_FullDescriptionProperty，读屏把悬停说明追加在名称后念一遍。定时任务
# 里这类文案含操作提示（「回车从候选里选」）、帮助文案，删除按钮还和名称
# 完全重复。只在该窗口内用空白描述占位挡住回退，不动视觉 tooltip。
from unittest.mock import patch  # noqa: E402
import app.ui.schedule_dialog as _sched_mod  # noqa: E402
from app.ui.a11y import _UIA_BLANK_DESCRIPTION, _schedule_dialog  # noqa: E402
from app.ui.schedule_dialog import ScheduleDialog  # noqa: E402

sched_dev = DeviceInfo(did="s1", name="大厅空调", model="demo.ac",
                       home_name="我的家", room_name="大厅", online=True)
sched_rules = [
    {"id": "r1", "did": "s1", "time": "07:00", "action": "on", "enabled": True},
]
with patch.object(_sched_mod.schedule_store, "load",
                  return_value=sched_rules):
    sched_dlg = ScheduleDialog([sched_dev], parent=host)
bridge.scan()

assert _schedule_dialog(sched_dlg._hour_combo) is sched_dlg
# 四个下拉框在 UIA 里原本 Name 全为空，只补缺失的用途名
for attr, expected in (("_device_combo", "设备"), ("_hour_combo", "小时"),
                       ("_minute_combo", "分钟"), ("_action_combo", "动作")):
    combo = getattr(sched_dlg, attr)
    assert combo.accessibleName() == expected, (attr, combo.accessibleName())

# 带 tooltip 的控件：描述只剩不可读的空白占位，悬停提示一个字不改
tipped = [
    (sched_dlg._hour_combo, "小时（0–23）"),
    (sched_dlg._minute_combo, "分钟（每 5 分钟一档）"),
    (sched_dlg._device_combo.lineEdit(), "输入任意片段筛设备，回车从候选里选"),
    (sched_dlg._switches["r1"], "停用后保留规则但不执行"),
]
for widget, tip_text in tipped:
    assert widget.toolTip() == tip_text, "视觉 tooltip 不能被改动"
    desc = widget.accessibleDescription()
    assert desc == _UIA_BLANK_DESCRIPTION and not desc.strip(), \
        f"需要空白占位挡住 FullDescription 回退: {desc!r}"
    # 名称里绝不混入 tooltip 的操作提示 / 帮助文案
    assert "回车" not in (widget.accessibleName() or ""), \
        widget.accessibleName()

# 删除按钮名称本来就从 tooltip 来，仍保留；但不再被追加念第二遍。
# 规则行里的开关同样是 QPushButton 子类，按 tooltip 精确定位删除按钮。
del_candidates = [b for b in sched_dlg._rows[0].findChildren(QPushButton)
                  if b.toolTip() == "删除这条规则"]
assert len(del_candidates) == 1, len(del_candidates)
del_btn = del_candidates[0]
assert del_btn.accessibleName() == "删除这条规则", del_btn.accessibleName()
assert del_btn.toolTip() == "删除这条规则"
assert not del_btn.accessibleDescription().strip()

# 规则开关仍可读、仍可聚焦，选中态照旧由读屏朗读
sched_switch = sched_dlg._switches["r1"]
assert sched_switch.accessibleName() == "大厅空调"
assert sched_switch.focusPolicy() != Qt.FocusPolicy.NoFocus
sched_switch.setChecked(False)
bridge.scan()
assert sched_switch.accessibleName() == "大厅空调", \
    "checkable 开关名称不应随选中态抖动"

# 别处同样用 tooltip 承载说明的控件不受影响，描述仍为空
outside = QPushButton("外部按钮")
outside.setToolTip("外部说明")
bridge.scan()
assert outside.accessibleDescription() == "", \
    "空白占位不能扩散到其它窗口"
assert outside.toolTip() == "外部说明"

# 反复扫描幂等
bridge.scan()
bridge.scan()
assert sched_dlg._hour_combo.accessibleDescription() == _UIA_BLANK_DESCRIPTION

# ---------- 7d. 下拉框不再多念「下方向」 ----------
# Qt 把 QComboBox 无障碍接口的 Accelerator 固定报成 Down，UIA 填进
# LegacyIAccessible.KeyboardShortcut，读屏于是追加一声「下方向」。工厂为
# 所有 QComboBox 返回代理接口：只把这一项报空，其余全部委托原生接口。
from PySide6.QtGui import QAccessible  # noqa: E402
from PySide6.QtWidgets import QComboBox  # noqa: E402
from app.ui.a11y import (  # noqa: E402
    _QuietComboBoxAccessible, _combo_interfaces, _quiet_combo_factory,
)

assert _QuietComboBoxAccessible is not None, \
    "当前 PySide6 应提供 QAccessibleWidget"
assert _quiet_combo_factory("QPushButton", outside) is None, \
    "工厂不能接管非 QComboBox 控件"

plain_combo = QComboBox()
plain_combo.addItems(["选项一", "选项二"])
plain_combo.setAccessibleName("普通下拉框")
plain_combo.show()
editable_combo = QComboBox()
editable_combo.setEditable(True)
editable_combo.addItems(["大厅空调", "卧室灯"])
editable_combo.setAccessibleName("可输入下拉框")
editable_combo.show()
bridge.scan()

combo_cases = [
    ("设备", sched_dlg._device_combo), ("小时", sched_dlg._hour_combo),
    ("分钟", sched_dlg._minute_combo), ("动作", sched_dlg._action_combo),
    ("普通", plain_combo), ("可输入", editable_combo),
]
for label, cb in combo_cases:
    iface = QAccessible.queryAccessibleInterface(cb)
    assert isinstance(iface, _QuietComboBoxAccessible), (label, type(iface))
    native = iface._native
    assert native is not None, f"{label}: 代理必须委托原生接口"
    assert native.text(QAccessible.Text.Accelerator).strip(), \
        f"{label}: 原生接口本该带 Down 提示，代理只负责挡住它"
    assert iface.text(QAccessible.Text.Accelerator) == "", \
        f"{label}: 读屏仍会多念「下方向」"
    for role in ("Name", "Description", "Value", "Help"):
        t = getattr(QAccessible.Text, role)
        assert iface.text(t) == native.text(t), \
            f"{label}: text({role}) 不该被改动"
    assert iface.text(QAccessible.Text.Value), f"{label}: 当前值不能丢"
    assert iface.role() == native.role(), f"{label}: 角色不能变"
    assert iface.childCount() == native.childCount(), f"{label}: 子节点不能丢"
    assert cb.focusPolicy() != Qt.FocusPolicy.NoFocus, f"{label}: 焦点被改"
    assert any(v is iface for _, v in _combo_interfaces.values()), \
        f"{label}: 代理接口需要保活，避免 Qt 持有悬空指针"

outside_iface = QAccessible.queryAccessibleInterface(outside)
assert not isinstance(outside_iface, _QuietComboBoxAccessible), \
    "非下拉框控件不该被代理"
assert editable_combo.lineEdit() is not None, "可输入下拉框仍要能筛选"
print("[7/9] 设置开关 / 勾选框 / 抽屉 / 符号按钮 / 房间默认 / 定时任务 / 下方向 OK")


# ---------- 7e. 托盘快捷窗口：电源钮补上设备名与房间 ----------
# 托盘行只存 _did，设备名与房间画在同行的 QLabel 上（还会按宽度省略），
# _context() 沿父链找不到 device 对象，于是每个电源钮都只念
# 「电源开关，已关闭」，逐行 Tab 时分不清是哪台设备。
from app.ui.tray.quick_window import TrayQuickWindow  # noqa: E402


class _StubBackend:
    """不联网、不发请求：托盘窗口构造时只需保存这两个引用。"""

    def __getattr__(self, _name):
        return lambda *a, **k: None

    def submit(self, *a, **k):
        return None


quick_devs = [
    DeviceInfo(did="q1", name="储藏室灯", model="demo.light",
               home_name="我的家", room_name="储藏室", online=True),
    DeviceInfo(did="q2", name="客厅灯", model="demo.light",
               home_name="我的家", room_name="客厅", online=True),
]
quick = TrayQuickWindow(_StubBackend(), _StubBackend())
quick.set_devices(quick_devs, {"q1": False, "q2": True})
quick._tray_dids = ["q1", "q2"]
quick._tray_scenes = []
quick._tray_order = []
quick._rebuild()
bridge.scan()

q1 = quick._grid.itemAt(0).widget()._power_btn
q2 = quick._grid.itemAt(1).widget()._power_btn
assert q1.accessibleName() == "储藏室灯，储藏室，电源开关，已关闭", \
    q1.accessibleName()
assert q2.accessibleName() == "客厅灯，客厅，电源开关，已开启", \
    q2.accessibleName()
assert not q1.accessibleDescription() and not q2.accessibleDescription()
for noise in ("按空格", "按回车", "按钮", "复选框", "未选中"):
    assert noise not in q1.accessibleName(), \
        f"托盘电源钮名称含冗余文案「{noise}」: {q1.accessibleName()}"
assert q1.focusPolicy() != Qt.FocusPolicy.NoFocus, \
    "托盘行电源钮是当地唯一键盘入口，不能被移出 Tab 链"

# 开关状态翻转后，设备名与房间不能丢
q1.set_state(True)
bridge.scan()
assert q1.accessibleName() == "储藏室灯，储藏室，电源开关，已开启", \
    q1.accessibleName()
q1.set_state(False)
bridge.scan()

# 场景行同样补上所属对象（场景名，家庭）
quick._tray_scenes = [
    {"scene_id": "s1", "name": "晚安模式", "home_id": "h1",
     "home_name": "我的家"},
]
srow = quick._make_scene_row(
    SceneInfo(scene_id="s1", name="晚安模式", home_id="h1",
              home_name="我的家"))
srow.setParent(quick)
bridge.scan()
scene_btn = next(b for b in srow.findChildren(QPushButton)
                 if b.objectName() == "sceneRunBtn")
assert scene_btn.accessibleName() == "晚安模式，我的家，执行场景", \
    scene_btn.accessibleName()

# 回归：主界面设备卡片与独立电源钮的名称保持原样，不被托盘规则波及
assert power.accessibleName() == "客厅灯，电源开关，已开启", \
    power.accessibleName()
assert standalone.accessibleName().startswith("电源开关"), \
    standalone.accessibleName()
print("[7e] 托盘快捷窗口电源钮 / 场景钮补上设备名与房间 OK")


# ---------- 7f. 托盘快捷窗口：Tab 焦点不再抖动 ----------
# 主窗口每 3 秒轮询一次设备，窗口可见时 set_devices() 会 _rebuild() 整窗
# 重建：持有焦点的电源钮被 deleteLater 销毁，焦点无声丢失又回到链首；
# 开关按下后 set_busy(True) 禁用按钮，Qt 还会把焦点挪到下一台设备。
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QAbstractButton  # noqa: E402

from app.core import tray_store as _tray_store  # noqa: E402
from app.ui.a11y import _semantic_key as _sem_key  # noqa: E402
from app.ui.a11y import _tray_quick_live_buttons as _tray_live  # noqa: E402


def _spin(ms=25):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _FocusBackend:
    """service / jobs 二合一：submit 只排队，finish() 手动完成忙碌态。"""

    def __init__(self):
        self.pending = []

    def __getattr__(self, _name):
        return lambda *a, **k: None

    def set_power_state(self, did, target):
        return target

    def submit(self, fn, on_success=None, on_error=None, priority=None):
        self.pending.append((fn, on_success, on_error))

    def finish(self):
        for fn, ok, err in list(self.pending):
            try:
                result = fn()
                if ok:
                    ok(result)
            except Exception as exc:
                if err:
                    err(exc)
        self.pending.clear()


def _send_key(widget, key):
    app.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key,
                                    Qt.KeyboardModifier.NoModifier))
    app.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, key,
                                    Qt.KeyboardModifier.NoModifier))
    _spin(3)


_orig_tray_loaders = (_tray_store.load, _tray_store.load_scenes,
                      _tray_store.load_order)
_tray_store.load = lambda: ["q1", "q2"]
_tray_store.load_scenes = lambda: []
_tray_store.load_order = lambda: []
_focus_win = None
try:
    backend = _FocusBackend()
    focus_devs = [
        DeviceInfo(did="q1", name="储藏室灯", model="demo.light",
                   home_name="我的家", room_name="储藏室", online=True),
        DeviceInfo(did="q2", name="客厅灯", model="demo.light",
                   home_name="我的家", room_name="客厅", online=True),
    ]
    _focus_win = TrayQuickWindow(backend, backend)
    _focus_win.set_devices(focus_devs, {"q1": False, "q2": True})
    _focus_win._rebuild()
    _focus_win.show()
    _spin()
    bridge.scan()
    _spin()
    assert getattr(_focus_win, "_a11y_rebuild_original", None) is not None, \
        "托盘窗口应装上重建守卫"

    fq1 = _focus_win._grid.itemAt(0).widget()._power_btn
    fq1.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    bridge.scan()
    for _ in range(3):
        _focus_win.set_devices(focus_devs, {"q1": False, "q2": True})
        _spin(20)
        bridge.scan()
        assert _focus_win._grid.itemAt(0).widget()._power_btn is fq1, \
            "设备内容没变就不该整窗重建、销毁焦点按钮"
        assert app.focusWidget() is fq1, "轮询不该让键盘焦点丢失"
    print("[7f] 无谓重建被抑制、连续轮询焦点稳定 OK")

    # 开关状态真的变了才重建，且焦点按 did 回到同一台设备的新按钮
    _focus_win.set_devices(focus_devs, {"q1": True, "q2": True})
    _spin(30)
    bridge.scan()
    _spin(10)
    new_fq1 = _focus_win._grid.itemAt(0).widget()._power_btn
    fq2 = _focus_win._grid.itemAt(1).widget()._power_btn
    assert new_fq1 is not fq1, "状态变化后应重建列表"
    assert app.focusWidget() is new_fq1, "重建后焦点应回到同一台设备"
    assert _sem_key(new_fq1) == ("tray-device", "q1"), "托盘行语义键按 did 稳定"

    clicks = {"q1": 0, "q2": 0}
    new_fq1.clicked.connect(
        lambda: clicks.__setitem__("q1", clicks["q1"] + 1))
    fq2.clicked.connect(
        lambda: clicks.__setitem__("q2", clicks["q2"] + 1))
    new_fq1.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    _send_key(new_fq1, Qt.Key.Key_Space)
    assert clicks == {"q1": 1, "q2": 0}, "空格应只激活当前这一台设备"
    assert new_fq1.isEnabled(), "忙碌时保持启用，才能不让焦点漂移"
    assert getattr(new_fq1, "_a11y_busy_hold", False), "忙碌守卫应生效"
    drift = app.focusWidget()
    if drift is not None and drift is not new_fq1 and _sem_key(drift):
        _send_key(drift, Qt.Key.Key_Space)
        assert clicks == {"q1": 1, "q2": 0}, \
            "忙碌期间 Qt 的被动焦点漂移不该误切另一台设备"
    backend.finish()
    _spin(20)
    bridge.scan()
    _spin(10)
    assert app.focusWidget() is new_fq1, "忙碌结束后焦点应回到刚操作的开关"
    # 回车改为「打开设备详情」（与主界面卡片一致），不再切电源；
    # 详细断言见 7i 组，这里只确认焦点稳定逻辑仍成立、不误切下一台。
    opened_q: list[str] = []
    _focus_win.open_device_requested.connect(
        lambda did: opened_q.append(did))
    new_fq1.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    _send_key(new_fq1, Qt.Key.Key_Return)
    assert opened_q == ["q1"], f"回车应打开 q1 详情: {opened_q}"
    assert clicks == {"q1": 1, "q2": 0}, \
        "回车不该切电源，也不该误切下一台设备"
    assert app.focusWidget() is new_fq1, "回车打开详情不该让焦点漂移"
    backend.finish()
    _spin(20)
    print("[7f] 空格可靠激活 / 回车打开详情、忙碌结束焦点回位 OK")

    # 强制重建时，等待 deleteLater 的旧行在删除前不能留在 Tab 链里
    _focus_win._a11y_rebuild_original()
    bridge.scan()
    live = _tray_live(_focus_win)
    orphans = [b for b in _focus_win._host.findChildren(QAbstractButton)
               if b not in live]
    assert orphans, "探针应造出等待删除的旧行"
    assert all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in orphans), \
        "旧行按钮仍留在 Tab 链里，会造成同名重复焦点"
    _spin(20)

    # 连续按 Tab 不来回落在同一个控件，期间轮询也不打断
    cur = _focus_win._grid.itemAt(0).widget()._power_btn
    cur.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    stops = []
    for _ in range(8):
        _send_key(app.focusWidget(), Qt.Key.Key_Tab)
        cur = app.focusWidget()
        stops.append(cur.accessibleName() or type(cur).__name__)
    assert all(a != b for a, b in zip(stops, stops[1:])), \
        f"相邻两次 Tab 落在同一个控件: {stops}"
    for _ in range(3):
        _focus_win.set_devices(focus_devs, {"q1": True, "q2": True})
        _spin(15)
    assert app.focusWidget() is cur, "轮询不该改变用户刚 Tab 到的控件"
    print("[7f] 旧行退出 Tab 链、连续 Tab 不重复 OK")

    # 鼠标焦点照旧，托管逻辑不把焦点抢回来
    b0 = _focus_win._grid.itemAt(0).widget()._power_btn
    b1 = _focus_win._grid.itemAt(1).widget()._power_btn
    b0.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    bridge.scan()
    b1.setFocus(Qt.FocusReason.MouseFocusReason)
    _spin()
    bridge.scan()
    _spin(10)
    assert app.focusWidget() is b1, "不该把焦点从鼠标点击的控件上抢走"
    print("[7f] 鼠标焦点不受干扰 OK")

    # ---------- 7g. 忙碌期间焦点一步都不漂移 ----------
    # 读屏按焦点事件播报：只要 Qt 把焦点推给下一颗按钮再抢回来，用户就会
    # 先听到「空调，大厅，电源开关……」读不全、很快又跳回原来的按钮。
    class _FocusSpy(QObject):
        def __init__(self):
            super().__init__()
            self.hits = []

        def eventFilter(self, obj, ev):  # noqa: N802
            if ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.hits.append((ev.type().name, type(obj).__name__))
            return False

    b = _focus_win._grid.itemAt(0).widget()._power_btn
    assert getattr(b, "_a11y_set_enabled_original", None) is not None, \
        "托盘行按钮应装上忙碌守卫"
    b.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    bridge.scan()
    fired = {"n": 0}
    b.clicked.connect(lambda: fired.__setitem__("n", fired["n"] + 1))
    sheet_idle = b.styleSheet()

    spy = _FocusSpy()
    app.installEventFilter(spy)
    spy.hits.clear()
    _send_key(b, Qt.Key.Key_Space)      # clicked → _on_toggle → set_busy(True)
    events_during_busy = list(spy.hits)
    assert not events_during_busy, \
        f"忙碌时发生了焦点事件，读屏会念到下一项: {events_during_busy}"
    assert app.focusWidget() is b, "忙碌时焦点必须原地不动"
    assert fired["n"] == 1
    assert b.isEnabled() and getattr(b, "_a11y_busy_hold", False)

    # 忙碌配色与「正常禁用」完全一致：_apply() 只读 _busy / _state
    ref = PowerButton(28)
    ref.set_state(b.state())
    ref.set_busy(True)
    assert b.styleSheet() == ref.styleSheet(), "忙碌视觉不能变"
    assert b.styleSheet() != sheet_idle, "忙碌态应当照常显示（乐观翻转）"
    ref.set_busy(False)

    # 保持启用后，防连点由桥接器等价补回：键盘与鼠标都不重复激活
    _send_key(b, Qt.Key.Key_Space)
    _send_key(b, Qt.Key.Key_Return)
    mouse_click(b)
    assert fired["n"] == 1, "忙碌期间的重复激活应被吞掉"
    assert app.focusWidget() is b, "忙碌期间焦点始终不动"

    backend.finish()
    _spin(20)
    bridge.scan()
    _spin(10)
    assert not getattr(b, "_a11y_busy_hold", False), "忙碌结束后守卫应解除"
    assert b.isEnabled() and app.focusWidget() is b
    _send_key(b, Qt.Key.Key_Space)
    assert fired["n"] == 2, "忙碌结束后可再次激活"
    backend.finish()
    app.removeEventFilter(spy)
    print("[7g] 忙碌期间零焦点事件、外观一致、防连点、结束后可再激活 OK")

    # 场景执行钮走 _apply_scene_btn(busy=True)，同样不能抢焦点
    srow = _focus_win._make_scene_row(
        SceneInfo(scene_id="s9", name="晚安模式", home_id="h1",
                  home_name="我的家"))
    srow.setParent(_focus_win)
    _focus_win._grid.addWidget(srow, 9, 0)
    bridge.scan()
    sbtn = next(x for x in srow.findChildren(QPushButton)
                if x.objectName() == "sceneRunBtn")
    assert getattr(sbtn, "_a11y_set_enabled_original", None) is not None, \
        "场景钮也应装上忙碌守卫"
    # 清掉上一台设备的键盘锚点，等价于用户第一次 Tab 进场景行；否则
    # FocusIn 会被当作「被动漂移」保留旧锚点，焦点又被纠正回设备按钮。
    bridge._tray_anchor = None
    sbtn.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    spy2 = _FocusSpy()
    app.installEventFilter(spy2)
    spy2.hits.clear()
    mouse_click(sbtn)                   # _run_scene → _apply_scene_btn(busy=True)
    assert not spy2.hits, f"场景执行也抢了焦点: {spy2.hits}"
    assert app.focusWidget() is sbtn and sbtn.isEnabled(), \
        "场景执行后焦点应留在原按钮，按钮保持启用"
    assert getattr(sbtn, "_a11y_busy_hold", False)
    app.removeEventFilter(spy2)
    backend.finish()
    _spin(10)
    print("[7g] 场景执行钮同样零焦点漂移 OK")

    # 普通窗口的电源钮不该被波及：忙碌时仍按业务原样禁用
    plain = PowerButton(28)
    plain.set_state(False)
    plain.set_busy(True)
    assert not plain.isEnabled(), "其它窗口的忙碌禁用行为不能被改"
    plain.set_busy(False)
finally:
    (_tray_store.load, _tray_store.load_scenes,
     _tray_store.load_order) = _orig_tray_loaders
    if _focus_win is not None:
        _focus_win.hide()


# ---------- 7h. 托盘小爱音频栏：切歌 / 播放 / 音量都能用键盘 ----------
# 业务把三颗图标按钮与音量条都显式设成 NoFocus，Tab 根本到不了；音量条
# 的 valueChanged 只更新预览，真正下发靠 sliderReleased → _on_slider_commit。
from PySide6.QtWidgets import QAbstractSlider as _QAbstractSlider  # noqa: E402
from app.ui.a11y import (  # noqa: E402
    _SLIDER_COMMIT_MS, _audio_bar, _audio_role,
    _tray_quick_button_key,
)


class _AudioBackend:
    """submit 只排队；finish() 手动完成，便于断言忙碌态与去抖次数。"""

    def __init__(self):
        self.pending = []
        self.writes = []
        self.actions = []

    def __getattr__(self, _n):
        return lambda *a, **k: None

    def read_props(self, _did, names):
        return {n: (42 if n == "volume" else False) for n in names}

    def write_prop(self, _did, name, value):
        self.writes.append((name, value))
        return None

    def run_action(self, _did, name, *args):
        self.actions.append(name)
        return None

    def submit(self, fn, on_success=None, on_error=None, priority=None):
        self.pending.append((fn, on_success, on_error))

    def finish(self):
        for fn, ok, err in list(self.pending):
            try:
                r = fn()
                if ok:
                    ok(r)
            except Exception as exc:
                if err:
                    err(exc)
        self.pending.clear()


class _AudioSpy(QObject):
    def __init__(self):
        super().__init__()
        self.hits = []

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.hits.append((ev.type().name,
                              obj.accessibleName() or type(obj).__name__))
        return False


_ab = _AudioBackend()
_audio_devs = [
    DeviceInfo(did="spk1", name="小爱音箱 Pro",
               model="xiaomi.wifispeaker.lx06",
               home_name="我的家", room_name="大厅", online=True),
    DeviceInfo(did="a1", name="储藏室灯", model="demo.light",
               home_name="我的家", room_name="储藏室", online=True),
]
_awin = TrayQuickWindow(_ab, _ab)
_awin._maybe_hide = lambda: None
_awin._maybe_hide_on_deactivate = lambda: None
_awin.hide_animated = lambda: None
_awin.set_devices(_audio_devs, {"a1": False})
_awin._tray_dids = ["a1"]
_awin._tray_scenes = []
_awin._tray_order = []
_awin._rebuild()
_awin.show()
_spin()
abar = _awin._audio_bar
abar._apply_state({
    "vol_range": (0, 100, 1), "vol_name": "volume", "mute_name": "mute-2",
    "vals": {"volume": 42, "mute-2": False},
})
_spin()
bridge.scan()
_spin()

_prev, _play, _next, _vol = (abar._btn_prev, abar._btn_play,
                             abar._btn_next, abar._slider)

# 名称只补缺失文字：数值交给原生 QSlider 的 RangeValuePattern，不重复写
assert _prev.accessibleName() == "上一首", _prev.accessibleName()
assert _play.accessibleName() == "暂停", _play.accessibleName()
assert _next.accessibleName() == "下一首", _next.accessibleName()
assert _vol.accessibleName() == "音量", _vol.accessibleName()
assert not _vol.accessibleDescription()
for _b in (_prev, _play, _next):
    assert _b.accessibleDescription() == " ", repr(_b.accessibleDescription())
    assert _b.toolTip(), "视觉 tooltip 不能删"
    assert _b.focusPolicy() != Qt.FocusPolicy.NoFocus
assert _vol.focusPolicy() != Qt.FocusPolicy.NoFocus
assert _vol.property("_a11y_slider"), "音量条要挂上通用滑块键盘处理"
assert _audio_bar(_vol) is abar and _audio_role(_vol, abar) == "volume"
# 音频栏也存了 _did，不能被误判成托盘设备行 / 电源钮
assert _tray_quick_button_key(_prev) is None
print("[7h] 名称 / 焦点 / 描述 / 音频栏识别 OK")

# 播放钮的 tooltip 与名称随静音状态同步
abar._muted = True
abar._refresh_play_style()
bridge.scan()
assert _play.accessibleName() == "播放", _play.accessibleName()
abar._muted = False
abar._refresh_play_style()
bridge.scan()
assert _play.accessibleName() == "暂停", _play.accessibleName()
print("[7h] 「暂停 / 播放」状态同步 OK")

# Tab 链里四个控件连续、无同名重复
chain = [w for w in _awin.findChildren(QAbstractButton) +
         _awin.findChildren(_QAbstractSlider)
         if w.isVisible() and w.focusPolicy() != Qt.FocusPolicy.NoFocus]
names = [w.accessibleName() for w in chain]
assert len(set(names)) == len(names), "Tab 链出现同名重复节点"
i = names.index("上一首")
assert names[i:i + 4] == ["上一首", "暂停", "下一首", "音量"], names[i:i + 4]
print("[7h] Tab 链四控件连续 OK")


def _audio_activate(btn, key, action=None):
    """键盘激活图标按钮：零焦点事件、只提交一次、防连点、结束可再用。"""
    _ab.pending.clear()
    _ab.actions.clear()
    _ab.writes.clear()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    _spin()
    spy = _AudioSpy()
    app.installEventFilter(spy)
    _send_key(btn, key)
    _spin(20)
    assert len(_ab.pending) == 1, _ab.pending
    assert not spy.hits, "键盘激活后焦点漂移了: %r" % (spy.hits,)
    assert app.focusWidget() is btn
    assert btn.isEnabled() and btn._a11y_busy_hold, "忙碌应保持启用不抢焦点"
    _send_key(btn, Qt.Key.Key_Space)      # 防连点
    assert len(_ab.pending) == 1, "忙碌期间不能重复提交"
    _ab.finish()
    _spin(20)
    if action:
        assert _ab.actions[-1] == action, _ab.actions
    assert not btn._a11y_busy_hold and btn.isEnabled()
    app.removeEventFilter(spy)


_audio_activate(_prev, Qt.Key.Key_Space, "previous")
_audio_activate(_next, Qt.Key.Key_Return, "next")
_audio_activate(_play, Qt.Key.Key_Space)      # 走 write_prop 静音，不报 action
print("[7h] 空格 / 回车激活切歌与播放、零焦点漂移、防连点 OK")

# 音量条：方向键改预览、停止按键后只下发一次、提交期间焦点不动
_ab.pending.clear()
_ab.writes.clear()
_vol.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_spy_vol = _AudioSpy()
app.installEventFilter(_spy_vol)
for _ in range(3):
    _send_key(_vol, Qt.Key.Key_Right)
assert _vol.value() == 45, _vol.value()
assert abar._vol_label.text() == "45", abar._vol_label.text()
assert not _ab.writes, "按键期间不应下发设备"
_spin(_SLIDER_COMMIT_MS + 120)
assert len(_ab.pending) == 1, "连按 3 次只能提交一次: %r" % (_ab.pending,)
assert not _spy_vol.hits, "提交时焦点漂移了: %r" % (_spy_vol.hits,)
assert app.focusWidget() is _vol
assert _vol.isEnabled() and _vol._a11y_busy_hold
_send_key(_vol, Qt.Key.Key_Right)            # 忙碌期间方向键被吞
assert _vol.value() == 45
_ab.finish()
_spin(20)
assert _ab.writes == [("volume", 45)], _ab.writes
assert not _vol._a11y_busy_hold
app.removeEventFilter(_spy_vol)

# 边界与大步长按键
_ab.pending.clear()
_send_key(_vol, Qt.Key.Key_Home)
assert _vol.value() == 0, _vol.value()
_send_key(_vol, Qt.Key.Key_End)
assert _vol.value() == 100, _vol.value()
_send_key(_vol, Qt.Key.Key_PageDown)
assert _vol.value() == 90, _vol.value()
_spin(_SLIDER_COMMIT_MS + 120)
_ab.finish()
_spin(20)
print("[7h] 方向键 / Home / End / Page 与松手去抖下发 OK")

# 鼠标路径不被守卫影响：业务照常禁用（明眼用户看到的忙碌变暗不变）
_ab.pending.clear()
_next.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
mouse_click(_next)
_spin(10)
assert not _next.isEnabled(), "鼠标点击后业务应照常禁用"
_ab.finish()
_spin(10)
_awin.hide()
print("[7h] 鼠标路径业务禁用照旧 OK")


# ---------- 7i. 托盘设备行：空格切电源、回车打开设备详情 ----------
# 主界面设备卡片本来就是「空格切电源、回车打开详情」（见第 5 组）。托盘
# 快捷窗口的行没有可聚焦父卡片，焦点落在行内唯一的电源钮上，回车此前被
# _a11y_enter_proxy 转成 click()，等于又切了一次电源。现在把回车转发到
# 窗口既有的 open_device_requested(did) 信号——鼠标点击行本体走的正是它。
from app.ui.a11y import _tray_device_open_detail  # noqa: E402


class _EnterBackend(_FocusBackend):
    """在 _FocusBackend 上补一个 run_scene 计数，验证场景行不受影响。"""

    def __init__(self):
        super().__init__()
        self.scenes = []
        self.actions_run = []

    def run_scene(self, scene):
        self.scenes.append(scene)

    def run_action(self, _did, name, *args):
        self.actions_run.append(name)
        return None


_eb = _EnterBackend()
_edevs = [
    DeviceInfo(did="e1", name="储藏室灯", model="demo.light",
               home_name="我的家", room_name="储藏室", online=True),
    DeviceInfo(did="e2", name="空调", model="demo.ac",
               home_name="我的家", room_name="大厅", online=True),
]
_ewin = TrayQuickWindow(_eb, _eb)
_ewin._maybe_hide = lambda: None
_ewin._maybe_hide_on_deactivate = lambda: None
_ewin.hide_animated = lambda: None
_ewin.set_devices(_edevs, {"e1": False, "e2": False})
_ewin._tray_dids = ["e1", "e2"]
_ewin._tray_scenes = []
_ewin._tray_order = []
_ewin._rebuild()
_ewin.show()
_spin()
bridge.scan()
_spin()

_opened: list[str] = []
_ewin.open_device_requested.connect(lambda did: _opened.append(did))
_e1 = _ewin._grid.itemAt(0).widget()._power_btn
_e2 = _ewin._grid.itemAt(1).widget()._power_btn

# 名称 / 描述 / 焦点策略 / 显示文字一律不变（文案只补缺失文字，不加操作提示）
assert _e1.accessibleName() == "储藏室灯，储藏室，电源开关，已关闭", \
    _e1.accessibleName()
assert not _e1.accessibleDescription()
assert _e1.focusPolicy() != Qt.FocusPolicy.NoFocus
assert not _e1.autoDefault()
assert _e1.property("_a11y_enter_proxy")
for noise in ("按空格", "按回车", "打开详情", "按钮"):
    assert noise not in _e1.accessibleName(), \
        f"设备行名称混进了操作提示「{noise}」: {_e1.accessibleName()}"

# 1) 空格：仍走 QPushButton 原生 clicked → 切电源，绝不打开详情
bridge._tray_anchor = None
_e1.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_eb.pending.clear()
_send_key(_e1, Qt.Key.Key_Space)
assert _ewin._known_power["e1"] is True, "空格应切换电源"
assert _eb.pending, "空格应下发电源写入任务"
assert _opened == [], "空格不能打开设备详情"
_eb.finish()
_spin(20)
assert not getattr(_e1, "_a11y_busy_hold", False), "忙碌结束守卫应解除"

# 2) 回车：打开该设备详情，且完全不碰电源
bridge._tray_anchor = None
_e1.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_eb.pending.clear()
_known_before = dict(_ewin._known_power)
_send_key(_e1, Qt.Key.Key_Return)
assert _opened == ["e1"], f"回车应打开 e1 的详情: {_opened}"
assert _ewin._known_power == _known_before, "回车不能切换电源"
assert not _eb.pending, "回车不能下发电源任务"
assert not getattr(_e1, "_a11y_busy_hold", False), "回车不应进入忙碌态"

# 3) 另一台设备的 did 也要带对
bridge._tray_anchor = None
_e2.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_send_key(_e2, Qt.Key.Key_Return)
assert _opened == ["e2"], f"回车应打开 e2 的详情: {_opened}"

# 4) 小键盘回车同样打开详情；长按自动重复不重复弹窗
bridge._tray_anchor = None
_e2.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_send_key(_e2, Qt.Key.Key_Enter)
assert _opened == ["e2"], f"小键盘回车应打开详情: {_opened}"
repeat = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                   Qt.KeyboardModifier.NoModifier, "", True)
assert repeat.isAutoRepeat(), "探针自身应构造出自动重复事件"
app.sendEvent(_e2, repeat)
assert _opened == ["e2"], "长按回车的自动重复不能重复打开详情"

# 5) 忙碌期间防连点：回车被吞，不重复打开详情
bridge._tray_anchor = None
_e1.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_e1._a11y_busy_hold = True
_opened.clear()
_send_key(_e1, Qt.Key.Key_Return)
assert _opened == [], "忙碌期间的回车应被防连点吞掉"
_e1._a11y_busy_hold = False
_e1.setEnabled(True)

# 6) 场景行回车仍是「执行场景」，不打开详情
_srow = _ewin._make_scene_row(
    SceneInfo(scene_id="se1", name="晚安模式", home_id="h1",
              home_name="我的家"))
_srow.setParent(_ewin)
_ewin._grid.addWidget(_srow, 9, 0)
bridge.scan()
_sbtn = next(b for b in _srow.findChildren(QPushButton)
             if b.objectName() == "sceneRunBtn")
assert _tray_device_open_detail(_sbtn) is False, "场景行不能走设备详情分支"
bridge._tray_anchor = None
_sbtn.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_eb.pending.clear()
_send_key(_sbtn, Qt.Key.Key_Return)
assert _opened == [], "场景行回车不能打开设备详情"
assert _eb.pending, "场景行回车仍应执行场景（走原有 clicked）"
_eb.finish()
_spin(20)

# 7) 音频栏按钮回车仍是激活切歌 / 播放，不打开详情
#    此窗口没有在线音箱，业务 _on_prev 会在 _did 为空时直接返回，因此用
#    clicked 信号验证「回车走原有激活路径」，而不是看是否真的下发了请求。
_aprev = _ewin._audio_bar._btn_prev
assert _tray_device_open_detail(_aprev) is False, "音频栏不能走设备详情分支"
_audio_clicks = []
_aprev.clicked.connect(lambda: _audio_clicks.append(1))
bridge._tray_anchor = None
_aprev.setFocus(Qt.FocusReason.TabFocusReason)
_spin()
_opened.clear()
_send_key(_aprev, Qt.Key.Key_Return)
assert _opened == [], "音频栏回车不能打开设备详情"
assert _audio_clicks, "音频栏回车仍应激活（走原有 clicked）"
_eb.finish()
_spin(20)

# 8) 其它窗口的按钮一概不受影响：独立 PowerButton 与主界面卡片内电源钮
assert _tray_device_open_detail(power) is False, "主界面卡片电源钮不受影响"
assert _tray_device_open_detail(standalone) is False, "独立电源钮不受影响"
assert _tray_device_open_detail(card) is False, "非按钮控件不受影响"
_opened.clear()
_opened_before = len(opened)
key_press(card, Qt.Key.Key_Return)
assert len(opened) == _opened_before + 1, "主界面卡片回车仍打开详情"
key_press(standalone, Qt.Key.Key_Return)  # 独立按钮回车仍走原生 click()
assert _opened == [], "其它窗口的回车不能触发托盘设备详情"

# 9) 鼠标路径零改动：点行本体打开详情、点电源钮切电源
_opened.clear()
_eb.pending.clear()
mouse_click(_ewin._grid.itemAt(0).widget())  # 行本体
assert _opened == ["e1"], "鼠标点行本体仍打开 e1 详情"
_send_key(_e1, Qt.Key.Key_Space)
assert _eb.pending, "鼠标/键盘切电源路径不受影响"
_eb.finish()
_ewin.hide()
print("[7i] 托盘设备行空格切电源 / 回车打开详情、边界与鼠标路径 OK")


# ---------- 8. 播报区域、快捷键与幂等性 ----------
from app.ui.toast import Toast  # noqa: E402

Toast.info(host, "刷新完成，新增 2 台设备")
bridge.scan()
live = find_live_region()
assert live is not None, "应创建隐藏的播报区域"
assert "刷新完成" in live.accessibleName(), live.accessibleName()

# 全局快捷键已注册（F5/Alt+S 需真实 MainWindow 方法，此处宿主没有，跳过）
assert len(host._a11y_shortcuts) >= 10, \
    f"快捷键数量异常: {len(host._a11y_shortcuts)}"

stable = card.accessibleName()
bridge.scan()
bridge.scan()
assert card.accessibleName() == stable, "反复扫描结果应稳定（幂等）"
assert card.focusPolicy() != Qt.FocusPolicy.NoFocus
print("[8/9] 播报区域 / 快捷键 / 幂等性 OK")


# ---------- 9. 文案精简：只补缺失的文字，不重复读屏已有的信息 ----------
# 读屏会自己朗读控件类型（按钮 / 复选框 / 选项卡 / 分组）、选中态与可用
# 按键；这些写进名称就是噪音。accessibleDescription 会变成 UIA 的
# HelpText 被追加朗读，必须恒为空。
NOISE = (
    "按空格", "按回车", "按 Esc", "方向键", "复选框", "选项卡", "分组",
    "设备卡片", "场景卡片", "模式选项", "当前值", "已选中", "未选中",
    "已加入托盘", "未加入托盘", "保存后生效", "滑块", "顶栏按钮",
    "窗口控制按钮", "整行可激活", "输入名称或房间关键字",
)
bridge.scan()
named = 0
for widget in QApplication.allWidgets():
    nm = widget.accessibleName() or ""
    if nm:
        named += 1
        for noise in NOISE:
            assert noise not in nm, (
                f"{type(widget).__name__} 名称含冗余文案「{noise}」: {nm}")
    desc = widget.accessibleDescription() or ""
    # 定时任务里允许纯空白占位（挡住 Qt 把 tooltip 回退成 FullDescription），
    # 但绝不允许任何可读文字，否则会变成 UIA HelpText 被追加朗读。
    assert not desc.strip(), (
        f"{type(widget).__name__} 写了 HelpText，会被读屏追加朗读: {desc}")
    if isinstance(widget, QPushButton):
        assert not widget.autoDefault(), (
            f"{type(widget).__name__} 仍是 autoDefault，UIA 会报 Enter 快捷键")
assert named >= 10, f"参与检查的具名控件太少: {named}"
print("[9/9] 文案精简：无控件类型 / 选中态 / 操作提示 / HelpText OK")

print("\na11y_test: 全部通过（离屏、无需米家账号、未改动真实配置）")
