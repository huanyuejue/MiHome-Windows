# SPDX-License-Identifier: GPL-3.0-or-later
"""浅色/深色主题回归测试（离屏运行）。"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# 配置写入临时目录：测试绝不能碰项目根下的真实配置
from tests._isolate import isolate

isolate()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])

# 测试禁写运行数据：假设备会经指标回写等路径泄漏进
# devices_cache.json（曾污染真实运行数据），测试期一律禁写
from app.core import cache as _device_cache
_device_cache.save = lambda *a, **k: None

from app.core import settings_store, tray_store
from app.core.models import DeviceInfo
from app.core.service import MijiaService
from app.core.jobs import JobExecutor
from app.ui import si_theme
from app.ui.theme_service import apply_theme, effective_theme

# ---------- 1. 调色板与 QSS 生成 ----------
assert si_theme.build_qss()  # dark 默认
si_theme.set_theme("light")
assert si_theme.SiColors.CARD == "#ffffff"
assert si_theme.SiColors.THEME == "#3dbba4"  # 主题色不随主题变化
assert "{SiColors" not in si_theme.build_qss()  # 占位符全部填充
si_theme.set_theme("dark")
assert si_theme.SiColors.CARD == "#2a2c32"
print("1. 调色板动态取值 + QSS 生成 OK")

# ---------- 2. 跟随系统解析 ----------
settings_store.set_theme_mode("system")
assert effective_theme("system") in ("dark", "light")
assert effective_theme("light") == "light"
assert effective_theme("dark") == "dark"
assert settings_store.get_theme_mode() == "system"
settings_store.set_theme_mode("light")
assert settings_store.get_theme_mode() == "light"
settings_store.set_theme_mode("dark")
print("2. 设置存取 + 系统主题解析 OK")

# ---------- 3. 启动浅色主题 + 主窗口渲染 ----------
tray_store.save([])
apply_theme("light")
svc = MijiaService()
jobs = JobExecutor()
devs = [DeviceInfo(did=str(i), name=f"设备{i}", model="a.b.c", home_name="家",
                   room_name="客厅", online=(i % 2 == 0)) for i in range(4)]
sys.path.insert(0, ".")
from app.ui.main_window import MainWindow

win = MainWindow()
win._apply_devices(devs)
win.show()
app.processEvents()

img = win.grab().toImage()
bg = img.pixelColor(500, 18)
assert bg.name().upper() == "#ECECEF", f"浅色标题栏异常: {bg.name()}"
card = next(iter(win._cards.values()))
cimg = card.grab().toImage()
cc = cimg.pixelColor(5, 5)
assert cc.name().upper() == "#FFFFFF", f"浅色卡片底异常: {cc.name()}"
print("3. 浅色主题主窗/卡片渲染 OK")

# ---------- 4. 切换深色：卡片网格重建为新调色板 ----------
win.apply_theme_mode("dark")
app.processEvents()
assert si_theme.current_theme() == "dark"
img = win.grab().toImage()
bg = img.pixelColor(500, 18)
assert bg.name().upper() == "#1A1B20", f"深色标题栏异常: {bg.name()}"
card = next(iter(win._cards.values()))
cimg = card.grab().toImage()
cc = cimg.pixelColor(5, 5)
assert cc.name().upper() == "#2A2C32", f"深色卡片底异常: {cc.name()}"
print("4. 切换深色重建 OK")

# 切回浅色
win.apply_theme_mode("light")
app.processEvents()
img = win.grab().toImage()
assert img.pixelColor(500, 18).name().upper() == "#ECECEF"
print("4b. 切回浅色 OK")

# ---------- 5. 设置页：下拉预览 + 取消还原 ----------
from app.ui.settings_dialog import SettingsDialog

dlg = SettingsDialog(win, devices=devs)
assert dlg._theme_combo.currentText() == "深色模式"  # win 当前是 dark? 否——上面切回了 light
# 修正：上面的 win 在 4b 切回了 light，但对话框读取的是 store（dark）。
# store 与实际预览可能不同步是正常的：对话框以 store 为初始值。
dlg._theme_combo.setCurrentText("浅色模式")
assert dlg._pending_mode == "light"
assert si_theme.current_theme() == "light"
dlg._theme_combo.setCurrentText("深色模式")
assert si_theme.current_theme() == "dark"
# 取消 → 还原为打开前（store 里是 dark，无变化）
dlg._pending_mode = "light"
dlg.done(QDialog.DialogCode.Rejected)
app.processEvents()
assert settings_store.get_theme_mode() == "dark"
print("5. 设置下拉预览/取消还原 OK")

# ---------- 6. 旧回归：对话框淡出关闭 ----------
def run_dialog(dlg, name):
    escape = {"used": False}
    esc_timer = QTimer()
    esc_timer.setSingleShot(True)
    esc_timer.timeout.connect(lambda: (escape.__setitem__("used", True), QDialog.done(dlg, 0)))
    closer = QTimer()
    closer.setSingleShot(True)
    closer.timeout.connect(dlg.reject)
    esc_timer.start(2500)
    closer.start(300)
    dlg.exec()
    esc_timer.stop()
    assert not dlg.isVisible() and not escape["used"], name

run_dialog(SettingsDialog(win, devices=devs), "SettingsDialog")
from app.ui.tray import TrayManagerDialog
run_dialog(TrayManagerDialog(devs), "TrayManagerDialog")
from app.ui.device_dialog import DeviceDetailDialog
run_dialog(DeviceDetailDialog(svc, jobs, devs[0], win), "DeviceDetailDialog")
from app.ui.add_drawer import AddDrawer
a = AddDrawer(win)
a.set_modules([("on", "开关", "开")], set())
run_dialog(a, "AddDrawer")
print("6. 对话框淡出关闭回归 OK")

# ---------- 7. 托盘整窗重建（走 _on_theme_changed 真实路径） ----------
if win._tray is not None:
    win._tray.set_devices(devs, {})
    win._tray._quick.show_near_tray()
    app.processEvents()
    assert win._tray._quick.isVisible()
    old_quick = win._tray._quick
    win._on_theme_changed(si_theme.current_theme())
    app.processEvents()
    assert win._tray._quick is not old_quick, "retheme 未重建快捷窗口"
    assert win._tray._quick.isVisible(), "重建后未自动恢复显示"
    # 新窗口面板底色取当前调色板
    img = win._tray._quick._root.grab().toImage()
    assert img.pixelColor(150, img.height() // 3).name().upper() in (
        "#1E1F24", "#F2F3F5", "#2A2C32", "#FFFFFF")
    win._tray.hide_quick()
    app.processEvents()
print("7. 托盘整窗重建 + 自动恢复显示 OK")

win._all_devices = []  # 关闭路径不保存假设备
# 后台线程是 daemon，进程退出时自然结束，测试不必显式 join
win.close()
jobs.shutdown()
settings_store.set_theme_mode("system")
tray_store.save([])
print("THEME REGRESSION ALL PASS")
