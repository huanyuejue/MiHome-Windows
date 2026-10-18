# SPDX-License-Identifier: GPL-3.0-or-later
"""桌面挂件（Widgets Board）离屏回归测试。

覆盖：board.json 存取与越界钳制、挂件渲染、列数重排、透明度实时
预览与悬停恢复、一键收起/展开、折叠横条状态点、管理对话框勾选与
列数/透明度预览信号、主窗口接线（数据推送与显示开关）。

与 theme_test 同样禁写运行数据：假设备不得泄漏进 devices_cache.json
或 board.json。
"""

import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.core import cache as _device_cache
_device_cache.save = lambda *a, **k: None

from app.core import board_store

# 防回归：测试必须写在临时目录。隔离一旦失效，跑测试就会清空用户的
# 真实挂件设备（曾真实发生，用户表现为"每次启动设置都丢"）
from app.core import _json_store
assert "mihome-test-" in str(_json_store.data_dir()), \
    f"测试数据目录未隔离，会污染真实配置: {_json_store.data_dir()}"
from app.core.models import DeviceInfo
from app.core.service import MijiaService
from app.core.jobs import JobExecutor
from app.ui import si_theme
from app.ui.theme_service import apply_theme
from app.ui.widgets_board import WidgetsBoard, _COLLAPSE_HEIGHT
from app.ui.board_manager import BoardManagerDialog

# 测试全程离线：挂件建卡后会补拉开关状态，假设备没有真实云端，
# 不桩掉会真的发网络请求并让任务线程迟迟不退出
WidgetsBoard.refresh_power = lambda self, dids: None

# ---------- 1. board.json 存取与越界钳制 ----------
board_store.save_devices(["d3", "d1", "d3", "d2"])
assert board_store.load_devices() == ["d3", "d1", "d2"], "去重保序失败"
board_store.set_columns(4)
assert board_store.get_columns() == 4
board_store.set_columns(99)
assert board_store.get_columns() == 6, "列数上限未钳制"
board_store.set_columns(0)
assert board_store.get_columns() == 1, "列数下限未钳制"
board_store.set_columns(3)
board_store.set_opacity(0.5)
assert abs(board_store.get_opacity() - 0.5) < 1e-6
board_store.set_opacity(2.0)
assert board_store.get_opacity() == 1.0, "透明度上限未钳制"
board_store.set_opacity(0.01)
assert board_store.get_opacity() == 0.30, "透明度下限未钳制"
board_store.set_geometry(120, 80, 500, 300)
assert board_store.get_geometry() == (120, 80, 500, 300)
board_store.set_geometry(1, 2, 0, 0)
assert board_store.get_geometry() is None, "非法几何未拒绝"
board_store.set_autostart(True)
assert board_store.get_autostart() is True
board_store.set_autostart(False)
print("1. board.json 存取 + 越界钳制 OK")

# ---------- 2. 挂件渲染 + 空态 ----------
apply_theme("dark")
svc = MijiaService()
jobs = JobExecutor()
devs = [
    DeviceInfo(did=f"d{i}", name=f"设备{i}", model="a.b.c", home_name="家",
               room_name="客厅" if i % 2 == 0 else "卧室", online=(i % 3 != 0))
    for i in range(7)
]
board_store.save_devices([d.did for d in devs])
board = WidgetsBoard(svc, jobs, None)
assert board.columns() == 3
power = {d.did: (True if i % 2 == 0 else False) for i, d in enumerate(devs)}
board.set_devices(devs, power)
board.show()
app.processEvents()
assert board.isVisible()
img = board._root.grab().toImage()
panel = img.pixelColor(img.width() - 8, img.height() // 2)
assert panel.name().upper() in ("#1E1F24", "#F2F3F5"), f"挂件面板底色异常: {panel.name()}"
card = board._cards[devs[0].did]
assert card._power_btn is not None, "已知开关能力的设备应显示电源钮"
# 7 台设备 / 3 列 = 3 行；高度应容得下 3 行卡片
assert board.height() >= 3 * 56, f"挂件高度未按行数计算: {board.height()}"

# 空态：清空清单后显示引导文案而非空白面板
board_store.save_devices([])
board.set_devices(devs, power)
app.processEvents()
assert board._empty.isVisible(), "无设备时应显示空态引导"
assert not board._empty.text().strip() == "", "空态缺引导文案"
print("2. 挂件渲染 + 空态 OK")

# ---------- 3. 列数重排 ----------
board_store.save_devices([d.did for d in devs])
board.set_columns(1)
app.processEvents()
w1 = board.width()
board.set_columns(5)
app.processEvents()
w5 = board.width()
assert w5 > w1, f"列数增加后宽度应变大: {w1} -> {w5}"
assert board_store.get_columns() == 5
grid = board._grid
assert grid.count() == len(devs), f"网格条目数不符: {grid.count()}"
# 6 列越界应被钳到 6
board.set_columns(99)
assert board.columns() == 6
board.set_columns(3)
print("3. 列数重排 OK")

# ---------- 4. 透明度实时预览 + 悬停恢复 ----------
# windowOpacity 由平台按 1/255 量化，断言用 _OPACITY_TOL 而非浮点精确值
_OPACITY_TOL = 1.0 / 255.0 + 1e-6
board.set_opacity(0.45)
app.processEvents()
assert abs(board.windowOpacity() - 0.45) < _OPACITY_TOL, \
    f"透明度未生效: {board.windowOpacity()}"
assert abs(board_store.get_opacity() - 0.45) < 1e-6, "透明度未落盘"
board.set_opacity(5.0)
assert abs(board.opacity() - 1.0) < 1e-6, "透明度未钳制"
# 鼠标进入 → 恢复不透明
board.set_opacity(0.45)
enter = QMouseEvent(QEvent.Type.Enter, QPoint(5, 5), Qt.MouseButton.NoButton,
                   Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
app.sendEvent(board, enter)
assert abs(board.windowOpacity() - 1.0) < _OPACITY_TOL, "悬停未恢复不透明"
leave = QMouseEvent(QEvent.Type.Leave, QPoint(5, 5), Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
app.sendEvent(board, leave)
assert abs(board.windowOpacity() - 0.45) < _OPACITY_TOL, "移开后未恢复设定透明度"
# 预览期间（管理对话框打开）不强制不透明，否则预览值被顶回 1.0
board.setProperty("previewing", True)
app.sendEvent(board, enter)
assert abs(board.windowOpacity() - 0.45) < _OPACITY_TOL, "预览期间被悬停恢复顶回"
board.setProperty("previewing", False)
app.processEvents()
board.set_opacity(0.92)
print("4. 透明度预览 + 悬停恢复 OK")

# ---------- 5. 一键收起 / 展开 ----------
x0, y0 = board.x(), board.y()
board._collapse()
app.processEvents()
assert board.collapsed is True
assert board_store.get_collapsed() is True
assert board.height() == _COLLAPSE_HEIGHT, f"收起后高度应为 {_COLLAPSE_HEIGHT}: {board.height()}"
assert board._bar.isVisible() and not board._root.isVisible()
assert (board.x(), board.y()) == (x0, y0), "收起后位置应保持不变"
# 折叠横条每台设备一个状态点
dots = board._dot_lay.count() - 1  # 末尾有一个 stretch
assert dots == len(devs), f"状态点数不符: {dots} vs {len(devs)}"
assert board_store.get_collapsed() is True

board._expand()
app.processEvents()
assert board.collapsed is False
assert board_store.get_collapsed() is False
assert board._root.isVisible() and not board._bar.isVisible()
assert board.width() > 120, "展开后应恢复网格宽度"
# 托盘菜单入口走的 toggle：折叠↔展开
board.toggle_collapse()
assert board.collapsed is True
board.toggle_collapse()
assert board.collapsed is False
print("5. 一键收起/展开 OK")


# ---------- 5b. 折叠横条：单击展开 / 拖动移动且不展开 ----------
def _press(widget, global_pos):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPoint(4, 4),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    ev._gpos = global_pos  # 见下方 _send 的说明
    return ev


def _send_bar_mouse(kind, global_pos, buttons=Qt.MouseButton.LeftButton):
    """把鼠标事件直接送给折叠横条的事件过滤器（走真实 eventFilter 路径）。"""
    return _send(board._bar, kind, global_pos, buttons)


def _send(target, kind, gpos, buttons=Qt.MouseButton.LeftButton):
    """构造带 globalPosition 的伪鼠标事件交给 eventFilter。

    QMouseEvent 的构造函数无法注入 globalPosition，用包装对象补齐；
    eventFilter 只用到 type/button/buttons/globalPosition 四个成员。
    """
    ev = QMouseEvent(kind, QPoint(4, 4), Qt.MouseButton.LeftButton,
                     buttons, Qt.KeyboardModifier.NoModifier)

    class _Wrapped:
        def __init__(self, inner, pos):
            self._inner = inner
            self._gpos = pos

        def type(self):
            return self._inner.type()

        def button(self):
            return self._inner.button()

        def buttons(self):
            return self._inner.buttons()

        def globalPosition(self):
            return QPointF(self._gpos)

    return board.eventFilter(target, _Wrapped(ev, gpos))


board._collapse()
app.processEvents()
assert board.collapsed is True
origin = QPoint(board.x(), board.y())

# 单击（无位移）→ 展开
_send_bar_mouse(QEvent.Type.MouseButtonPress, origin + QPoint(20, 12))
_send_bar_mouse(QEvent.Type.MouseButtonRelease, origin + QPoint(20, 12),
                Qt.MouseButton.NoButton)
app.processEvents()
assert board.collapsed is False, "单击折叠横条应展开"

# 拖动（越过阈值）→ 窗口移动、留在折叠态、位置落盘
board._collapse()
app.processEvents()
assert board.collapsed is True
origin = QPoint(board.x(), board.y())
_send_bar_mouse(QEvent.Type.MouseButtonPress, origin + QPoint(20, 12))
_send_bar_mouse(QEvent.Type.MouseMove, origin + QPoint(90, 52))
app.processEvents()
assert board.collapsed is True, "拖动不应展开"
assert (board.x(), board.y()) == (origin.x() + 70, origin.y() + 40), \
    f"拖动未移动窗口: {board.geometry()}"
_send_bar_mouse(QEvent.Type.MouseButtonRelease, origin + QPoint(90, 52),
                Qt.MouseButton.NoButton)
app.processEvents()
assert board.collapsed is True, "拖动后松手不应展开"
# 拖动位置要落盘，展开后回到新位置
board._expand()
app.processEvents()
assert board.x() == origin.x() + 70, f"展开后未回到拖动后的位置: {board.x()}"
print("5b. 折叠横条单击展开 / 拖动移动 OK")

# 5c. 展开态：标题栏与面板空白处都能拖；单击标题栏收起
assert board.collapsed is False
base = QPoint(board.x(), board.y())
_send(board._handle, QEvent.Type.MouseButtonPress, base + QPoint(40, 8))
_send(board._handle, QEvent.Type.MouseMove, base + QPoint(10, 38))
app.processEvents()
moved = QPoint(board.x() - base.x(), board.y() - base.y())
assert moved != QPoint(0, 0), f"标题栏拖拽失效，窗口未移动: {board.geometry()}"
assert board.collapsed is False, "拖拽标题栏不应改变折叠态"
_send(board._handle, QEvent.Type.MouseButtonRelease, base + QPoint(10, 38),
      Qt.MouseButton.NoButton)
app.processEvents()
assert board.collapsed is False, "拖动后松手不应收起"
print(f"5c. 展开态标题栏拖拽 OK（位移 {moved.x()},{moved.y()}）")

# 5d. 单击标题行 → 收起（不拖动才算点击）
assert board.collapsed is False
click_at = QPoint(board.x(), board.y()) + QPoint(50, 8)
_send(board._handle, QEvent.Type.MouseButtonPress, click_at)
_send(board._handle, QEvent.Type.MouseButtonRelease, click_at,
      Qt.MouseButton.NoButton)
app.processEvents()
assert board.collapsed is True, "单击标题行应收起为细横条"
assert board_store.get_collapsed() is True, "收起状态未落盘"

# 再单击标题行不应有副作用（此时已折叠，标题行不可见）
# 展开回来，确认可以反复收放
board._expand()
app.processEvents()
assert board.collapsed is False
click_at = QPoint(board.x(), board.y()) + QPoint(50, 8)
_send(board._handle, QEvent.Type.MouseButtonPress, click_at)
_send(board._handle, QEvent.Type.MouseButtonRelease, click_at,
      Qt.MouseButton.NoButton)
app.processEvents()
assert board.collapsed is True, "第二次单击标题行未收起"
board._expand()
app.processEvents()
print("5d. 单击标题行收起 OK")

# ---------- 6. 折叠态仍跟随开关状态 ----------
board._collapse()
board._apply_power({devs[0].did: False, devs[1].did: True})
app.processEvents()
assert board._bar.isVisible(), "折叠态应只更新状态点，不展开网格"
board._expand()
print("6. 折叠态状态同步 OK")

# ---------- 7. 管理对话框：勾选 + 列数/透明度预览信号 ----------
board_store.save_devices([devs[0].did, devs[2].did])
dlg = BoardManagerDialog(devs, None)
assert dlg.selected_dids() == [devs[0].did, devs[2].did], "初始勾选未按存储顺序"
previews = {"opacity": [], "columns": []}
dlg.opacity_preview.connect(previews["opacity"].append)
dlg.columns_preview.connect(previews["columns"].append)
# 勾选一台新设备
target = devs[4].did
dlg._checks[target].setChecked(True)
assert target in dlg.selected_dids()
# 取消勾选一台已选设备
dlg._checks[devs[0].did].setChecked(False)
assert devs[0].did not in dlg.selected_dids()
# 列数按钮：点 4 应落盘并发出预览信号
for btn in dlg._col_buttons:
    if btn.text() == "4":
        btn.click()
assert board_store.get_columns() == 4
assert previews["columns"] and previews["columns"][-1] == 4
# 透明度滑块：拖到 40% 应落盘并发出预览信号
dlg._opacity_slider.setValue(40)
assert abs(dlg.opacity() - 0.40) < 1e-6
assert abs(board_store.get_opacity() - 0.40) < 1e-6
assert previews["opacity"] and abs(previews["opacity"][-1] - 0.40) < 1e-6
# 关闭不崩溃（淡出动画路径）
dlg.done(0)
app.processEvents()
print("7. 管理对话框勾选 + 预览信号 OK")

# ---------- 8. 主窗口接线 ----------
sys.path.insert(0, ".")
from app.ui.main_window import MainWindow

# 测试全程离线：主窗口的开关轮询/温湿度拉取都走真实网络，假设备没有
# 云端，桩掉才不会卡住任务线程（挂件的 refresh_power 已在前面桩掉）
MainWindow._refresh_power_states = lambda self, force: None
MainWindow._refresh_metrics = lambda self: None

board_store.set_columns(3)
board_store.set_opacity(0.92)
board_store.save_devices([d.did for d in devs])
win = MainWindow()
assert win._board is not None, "挂件创建失败"
win._apply_devices(devs)
app.processEvents()
# 主窗口设备推送后挂件拿到全量清单
assert len(win._board._dids) == len(devs)
# 关闭主窗口房间筛选不应影响挂件（挂件用全量列表）
before = len(win._board._cards)
win._select_room("客厅")
app.processEvents()
assert len(win._board._cards) == before, "挂件不应随主窗口房间筛选变化"

# 显示开关
win.hide_board()
app.processEvents()
assert not win._board.isVisible()
win.show_board()
app.processEvents()
assert win._board.isVisible()

# 收起开关（托盘菜单同款入口）
win.toggle_board_collapse()
assert win._board.collapsed is True
win.toggle_board_collapse()
assert win._board.collapsed is False

# 启动时自动显示：勾上 → 强制显示；取消勾选后跟随上次状态（此处上次是开着）
board_store.set_autostart(True)
win._apply_board_autostart()
app.processEvents()
assert win._board.isVisible(), "开启启动显示后应自动显示"
board_store.set_autostart(False)
win._apply_board_autostart()
app.processEvents()
assert win._board.isVisible(), "取消勾选后应跟随上次状态（上次开着 → 仍显示）"
# 上次关着时才隐藏（四种组合的完整断言见第 9 组）
board_store.set_visible(False)
win._apply_board_autostart()
app.processEvents()
assert not win._board.isVisible(), "上次关着 + 未勾选 → 不显示"

# 温湿度读数同步到挂件副标题
win.show_board()
app.processEvents()
win._metrics[devs[1].did] = "22.5℃ 45%"
win._push_tray_metrics()
app.processEvents()
card1 = win._board._cards.get(devs[1].did)
assert card1 is not None and "22.5℃" in card1._sub.text(), "读数未同步到挂件卡片"
# 隐藏期间推送的读数，显示时应补回（否则一直显示旧值）
win.hide_board()
app.processEvents()
win._metrics[devs[1].did] = "26.0℃ 50%"
win._push_tray_metrics()
win.show_board()
app.processEvents()
assert "26.0℃" in win._board._cards[devs[1].did]._sub.text(), "隐藏期间的读数未补回"

# 主题切换：挂件整窗重建样式
from PySide6.QtGui import QIcon  # noqa: F401  (确保 QtGui 已加载)
win.apply_theme_mode("light")
app.processEvents()
img = win._board._root.grab().toImage()
light = img.pixelColor(img.width() - 8, img.height() // 2)
assert light.name().upper() == "#F2F3F5", f"切浅色后面板底色未刷新: {light.name()}"
win.apply_theme_mode("dark")
app.processEvents()
img = win._board._root.grab().toImage()
dark = img.pixelColor(img.width() - 8, img.height() // 2)
assert dark.name().upper() == "#1E1F24", f"切深色后面板底色未刷新: {dark.name()}"
print("8. 主窗口接线 OK")

# ---------- 9. 重启后是否恢复显示：autostart × 上次状态 四种组合 ----------
# 规则：autostart=True 一律显示；autostart=False 跟随上次退出时的 visible
board_store.set_autostart(False)
board_store.set_visible(False)
win._apply_board_autostart()
app.processEvents()
assert not win._board.isVisible(), "上次关着 + 未勾自动显示 → 应保持隐藏"

board_store.set_visible(True)
win._apply_board_autostart()
app.processEvents()
assert win._board.isVisible(), "上次开着 + 未勾自动显示 → 应恢复显示"

board_store.set_autostart(True)
board_store.set_visible(False)
win._apply_board_autostart()
app.processEvents()
assert win._board.isVisible(), "勾了自动显示 → 无论上次状态都应显示"

board_store.set_autostart(False)

# 用户点 × / 托盘隐藏 → 记录 visible=False；程序退出时被动隐藏不覆盖它
win.show_board()
app.processEvents()
assert board_store.get_visible() is True, "显示挂件应记录为可见"
win._board.hide_by_user()
app.processEvents()
assert board_store.get_visible() is False, "点 × 应记录为不可见"
win.show_board()
app.processEvents()
assert board_store.get_visible() is True
win._board.hide()          # 模拟关闭程序时的被动隐藏
app.processEvents()
assert board_store.get_visible() is True, "被动隐藏不得覆盖用户意图（否则重启回不来）"
win.show_board()
app.processEvents()
print("9. 重启恢复显示的四种组合 + 被动隐藏不覆盖 OK")

# ---------- 10. 背景色：预设 / 自定义 / 跟随主题 ----------
# 存储层：只认 #rrggbb，非法值不写盘（写坏会把挂件刷成全黑或透明）
board_store.set_bg("#000000")
assert board_store.get_bg() == "#000000", "背景色未落盘"
board_store.set_bg("黑色")
assert board_store.get_bg() == "#000000", "非法颜色不得覆盖已有值"
board_store.set_bg("#ABCDEF")
assert board_store.get_bg() == "#abcdef", "颜色应统一小写"

win.show_board()
app.processEvents()
# 1) 纯黑应立刻生效，且卡片/文字自动反色（黑底黑字是这次要防的核心问题）
win._on_board_background_preview("#000000")
app.processEvents()
assert win._board.bg() == "#000000"
img = win._board._root.grab().toImage()
corner = img.pixelColor(img.width() - 8, img.height() // 2)
assert corner.red() < 40 and corner.green() < 40 and corner.blue() < 40, \
    f"选纯黑后挂件底色不是黑色: {corner.name()}"
name_style = win._board._cards[devs[1].did]._name.styleSheet()
assert "#f2f3f5" in name_style.lower(), f"深色底上文字未转亮色: {name_style}"
# 离线设备的文字要比在线设备更暗（rgba 透明度更低），否则看不出离线
offline_style = win._board._cards[devs[0].did]._name.styleSheet()
assert "rgba(255, 255, 255" in offline_style, \
    f"深色底上离线设备文字未转半透明白: {offline_style}"

# 2) 浅色底（米白）文字要转深色，不能沿用亮字
win._on_board_background_preview("#f5f1e8")
app.processEvents()
name_style = win._board._cards[devs[1].did]._name.styleSheet()
assert "#1a1b1f" in name_style.lower(), f"浅色底上文字未转深色: {name_style}"

# 3) 折叠横条跟随同一底色（收起后不能突然变回主题灰）
win._board._collapse()
app.processEvents()
assert "#f5f1e8" in win._board._bar.styleSheet().lower(), \
    f"折叠横条未跟随背景色: {win._board._bar.styleSheet()}"
win._board._expand()
app.processEvents()

# 4) 跟随主题：恢复后与主题色一致，且主题切换能继续带动
win._on_board_background_preview(None)
app.processEvents()
assert win._board.bg() is None
img = win._board._root.grab().toImage()
corner = img.pixelColor(img.width() - 8, img.height() // 2)
assert corner.name().upper() == "#1E1F24", f"恢复跟随主题后底色异常: {corner.name()}"
print("10. 背景色（黑/浅色反色 + 折叠条同步 + 跟随主题）OK")

# ---------- 11. 窗口置顶开关（默认不置顶）----------
from PySide6.QtCore import Qt as _Qt

assert board_store.get_topmost() is False, "置顶应默认关闭"
assert win._board.is_topmost() is False
assert not (win._board.windowFlags() & _Qt.WindowType.WindowStaysOnTopHint), \
    "默认不置顶时窗口标志里不应出现 WindowStaysOnTopHint"

# 预览路径只改真实窗口、不落盘（落盘由管理对话框负责，与透明度同一约定）
win._on_board_topmost_preview(True)
app.processEvents()
assert win._board.is_topmost() is True
assert win._board.windowFlags() & _Qt.WindowType.WindowStaysOnTopHint, \
    "开启置顶后窗口标志未带上 WindowStaysOnTopHint"
assert board_store.get_topmost() is False, "预览路径不应落盘"

# 带落盘的路径（挂件自身 API / 对话框）才写 board.json
win._board.set_topmost(True)
assert board_store.get_topmost() is True, "置顶状态未落盘"
win._board.set_topmost(False)
app.processEvents()
assert board_store.get_topmost() is False
assert not (win._board.windowFlags() & _Qt.WindowType.WindowStaysOnTopHint), \
    "关闭置顶后窗口标志未清除"
# 改窗口标志会重建原生窗口，几何必须还原（否则挂件跳回屏幕左上角）
geo = win._board.geometry()
assert geo.width() > 0 and geo.height() > 0, "切换置顶后窗口尺寸丢失"
assert win._board.isVisible(), "切换置顶后窗口被隐藏了（需重新 show）"
print("11. 置顶开关（默认关 / 可开可关 / 几何与显示不丢）OK")

# ---------- 12. 管理对话框：背景色与置顶的预览信号 ----------
board_store.set_bg(None)
board_store.set_topmost(False)
dlg2 = BoardManagerDialog(devs, None)
bg_events: list = []
top_events: list = []
dlg2.background_preview.connect(bg_events.append)
dlg2.topmost_preview.connect(top_events.append)
# 「自动」= 跟随主题，初始应选中
assert dlg2._bg_chips[0].isChecked(), "初始应选中「自动」"
# 点第一个预设色块（纯黑）
dlg2._bg_chips[1].click()
assert board_store.get_bg() == "#000000", "色块未落盘"
assert bg_events and bg_events[-1] == "#000000", f"未发出背景色预览信号: {bg_events}"
# 自定义颜色路径：直接注入 picker 结果，避免测试弹真实取色器
from PySide6.QtGui import QColor as _QColor

orig_picker = dlg2._choose_custom_bg
dlg2._choose_bg(_QColor("#123456").name())
assert board_store.get_bg() == "#123456"
assert all(not c.isChecked() for c in dlg2._bg_chips[1:]), \
    "自定义颜色不在预设里时不应选中任何预设色块"
assert dlg2._bg_hint.text() == "#123456", "提示未显示自定义色号"
# 自动 → 恢复跟随主题
dlg2._bg_chips[0].click()
assert board_store.get_bg() is None
assert bg_events[-1] is None, "恢复跟随主题应发出 None"
# 置顶勾选框
dlg2._topmost_check.setChecked(True)
assert board_store.get_topmost() is True
assert top_events and top_events[-1] is True, f"未发出置顶预览信号: {top_events}"
dlg2.done(0)
app.processEvents()
print("12. 管理对话框背景色 + 置顶预览信号 OK")

# ---------- 13. 搜索设备 + 离线设备不进候选 ----------
extra_offline = DeviceInfo(did="d_off", name="次卧无人灯", model="a.b.c",
                           home_name="家", room_name="次卧", online=False)
search_devs = devs + [extra_offline]
board_store.save_devices([devs[0].did])
dlg3 = BoardManagerDialog(search_devs, None)
# 未选中且离线的设备不该出现在候选里（用户明确要求：离线的不显示/搜不到）
assert "d_off" not in dlg3._rows, "未选中且离线的设备不应出现在候选列表"
# 已选中的设备即使离线也要保留（否则保存时会被静默丢掉）
board_store.save_devices([devs[0].did, extra_offline.did])
dlg4 = BoardManagerDialog(search_devs, None)
assert extra_offline.did in dlg4._rows, "已选中的离线设备必须保留，否则保存会丢设备"
assert dlg4._checks[extra_offline.did].isChecked()
dlg4.done(0)
app.processEvents()

# 搜索：按设备名过滤（假数据里 设备1 唯一）
dlg3._search.setText("设备1")
app.processEvents()
visible = [d for d, r in dlg3._rows.items() if not r.isHidden()]
assert visible == [devs[1].did], f"按名称搜索未过滤: {visible}"
# 按房间搜索：卧室里的在线设备是 d1 / d5（d3 离线且未选中，被排除）
dlg3._search.setText("卧室")
app.processEvents()
visible = [d for d, r in dlg3._rows.items() if not r.isHidden()]
assert set(visible) == {devs[1].did, devs[5].did}, f"按房间搜索未过滤: {visible}"
# 清空恢复全部
dlg3._search.setText("")
app.processEvents()
assert all(not r.isHidden() for r in dlg3._rows.values()), "清空搜索后应恢复全部行"
# 搜索结果为空时不崩、也不残留拖拽源
dlg3._search.setText("不存在的设备")
app.processEvents()
assert all(r.isHidden() for r in dlg3._rows.values())
assert dlg3._hit_test(dlg3._host.rect().center()) is None, "过滤后不应命中隐藏行"
dlg3.done(0)
app.processEvents()
print("13. 搜索设备 + 离线设备不显示 OK")

# ---------- 14. 连点「+」只能开一个管理窗 ----------
import app.ui.main_window as _mw

created: list = []
_RealDialog = _mw.BoardManagerDialog


class _SpyDialog(_RealDialog):
    def exec(self):
        created.append(1)
        if len(created) == 1:
            # 模拟模态窗打开期间又点了一次「+」
            win.show_board_manager()
        return 0


_mw.BoardManagerDialog = _SpyDialog
board_store.save_devices([d.did for d in devs])
win.show_board_manager()
_mw.BoardManagerDialog = _RealDialog
assert len(created) == 1, f"连点 + 开了多个管理窗: {len(created)} 个"
assert win._board_manager is None, "对话框关闭后未清空引用（会影响下次打开）"
print("14. 连点 + 只开一个管理窗 OK")

# ---------- 收尾：清运行数据 ----------
win._all_devices = []
win._force_quit = True
win.close()
jobs.shutdown()
board_store.save_devices([])
board_store.set_columns(3)
board_store.set_opacity(0.92)
board_store.set_collapsed(False)
board_store.set_autostart(False)
board_store.set_bg(None)
board_store.set_topmost(False)
si_theme.set_theme("dark")
print("BOARD REGRESSION ALL PASS")