# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""定时任务对话框：按设备配「每天某时刻打开/关闭」。

同一台设备可以有多条规则（7:00 开、23:00 关），列表按时间排序。
本对话框只负责读写规则，真正的时间判定在 app/core/scheduler.py，
执行在主窗口的定时器里——界面关掉不影响已保存的规则。
"""

from datetime import datetime

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta

from app.core import schedule_store
from app.core.models import DeviceInfo
from app.ui.overlay_dialog import OverlayDialog
from app.ui.si_theme import SiColors, themed_combo, themed_switch

_ACTION_LABEL = {"on": "打开", "off": "关闭"}
_ACTION_COLOR = {"on": SiColors.THEME, "off": SiColors.STATE_OFF}


class ScheduleDialog(OverlayDialog):
    """定时任务管理：新增 / 启用停用 / 删除。"""

    # 规则有变化（增删或启停）时通知主窗口重载调度器
    rules_changed = Signal()

    def __init__(self, devices: list[DeviceInfo], parent=None):
        super().__init__(parent, overlay=False)
        self.setWindowTitle("定时任务")
        self._devices = list(devices)
        self._switches: dict[str, object] = {}
        self._rows: list[QWidget] = []

        lay = QVBoxLayout(self._panel)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(10)

        # ---- 标题栏（整条可拖动窗口）----
        title_bar = QFrame(self._panel)
        title_bar.setObjectName("schedTitleBar")
        title_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        title_bar.setStyleSheet("QFrame#schedTitleBar { background: transparent; }")
        title_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        self._title_bar = title_bar
        self._header_drag_pos = None
        title_bar.installEventFilter(self)
        header = QHBoxLayout(title_bar)
        header.setContentsMargins(4, 2, 4, 2)
        header.setSpacing(8)
        title = QLabel("定时任务")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._make_close_button())
        lay.addWidget(title_bar)

        hint = QLabel("每台设备可配多条规则，到点自动执行一次。"
                      "需应用保持运行（可最小化到托盘），关掉程序不会触发。")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 9pt;")
        lay.addWidget(hint)

        lay.addWidget(self._make_add_section())

        # ---- 规则列表 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0 2px 0 0; }"
            f"QScrollBar::handle:vertical {{ background: {SiColors.SCROLLBAR};"
            " border-radius: 3px; min-height: 30px; }"
            f"QScrollBar::handle:vertical:hover {{ background: {SiColors.SCROLLBAR_HOVER}; }}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }")
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._list_lay = QVBoxLayout(host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(6)
        scroll.setWidget(host)
        lay.addWidget(scroll, stretch=1)
        self._scroll = scroll

        self._empty = QLabel("还没有定时任务\n上面选好设备与时间，点「添加」")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 9pt;")
        self._empty.hide()
        lay.addWidget(self._empty)

        self._rebuild()

    # ---------- 新增区 ----------

    def _make_add_section(self) -> QWidget:
        box = QFrame()
        box.setObjectName("schedSection")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setStyleSheet(
            f"QFrame#schedSection {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(12, 8, 12, 10)
        box_lay.setSpacing(6)

        label = QLabel("新增规则")
        label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent; font-size: 9pt;")
        box_lay.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(6)
        labels = [f"{d.name} · {d.room_name}" for d in self._devices]
        # 设备可能几十台，普通下拉要滚很久 —— 做成可输入搜索：
        # 输入任意片段（不区分大小写、包含匹配）即可筛出候选。
        self._device_combo = themed_combo(
            labels, labels[0] if labels else "", editable=True)
        # themed_combo 给设置页的窄下拉定了 112px 固定宽，这里要放开
        self._device_combo.setMinimumWidth(150)
        self._device_combo.setMaximumWidth(16777215)
        for i, text in enumerate(labels):
            # 名字被截断时，悬停能看到完整「设备名 · 房间」
            self._device_combo.setItemData(i, text, Qt.ItemDataRole.ToolTipRole)
        completer = QCompleter(labels, self._device_combo)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setMaxVisibleItems(8)
        self._device_combo.setCompleter(completer)
        # 搜索弹层比输入框宽一些，长设备名才看得全
        popup = completer.popup()
        if popup is not None:
            popup.setMinimumWidth(240)
        self._style_completer_popup()
        line = self._device_combo.lineEdit()
        if line is not None:
            line.setPlaceholderText("输入设备名搜索…")
            line.setToolTip("输入任意片段筛设备，回车从候选里选")
        # 设备单独占一行：可搜索的输入框需要宽度，挤在时间旁边只会被截断
        box_lay.addWidget(self._device_combo)

        row = QHBoxLayout()
        row.setSpacing(6)
        # 时间用「时 + 分」两个下拉：直接选，不用记格式，也不会输错。
        # 分钟按 5 分钟一档（12 档够日常点灯用），要精确到分钟就把
        # range(0, 60, 5) 改成 range(60)。
        # 不用 QTimeEdit：它一旦套自定义样式表，内部编辑框文字就不再绘制。
        now = datetime.now()
        self._hour_combo = themed_combo(
            [f"{h:02d}" for h in range(24)], f"{now.hour:02d}")
        self._hour_combo.setFixedWidth(62)
        self._hour_combo.setToolTip("小时（0–23）")
        row.addWidget(self._hour_combo)

        colon = QLabel("：")
        colon.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent;"
            " font-size: 10pt;")
        colon.setFixedWidth(10)
        colon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(colon)

        minute_choices = [f"{m:02d}" for m in range(0, 60, 5)]
        current_minute = f"{(now.minute // 5) * 5:02d}"
        self._minute_combo = themed_combo(minute_choices, current_minute)
        self._minute_combo.setFixedWidth(62)
        self._minute_combo.setToolTip("分钟（每 5 分钟一档）")
        row.addWidget(self._minute_combo)

        row.addSpacing(4)
        self._action_combo = themed_combo(["打开", "关闭"], "打开")
        self._action_combo.setFixedWidth(84)
        row.addWidget(self._action_combo)

        row.addStretch(1)
        add_btn = QPushButton("添加")
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.THEME}; border: none;"
            " border-radius: 8px; padding: 5px 16px; color: #0b0b0e; font-weight: 600; }"
            f"QPushButton:hover {{ background: {SiColors.THEME_HOVER}; }}")
        add_btn.clicked.connect(self._on_add)
        row.addWidget(add_btn)
        box_lay.addLayout(row)

        if not self._devices:
            self._device_combo.setEnabled(False)
            self._hour_combo.setEnabled(False)
            self._minute_combo.setEnabled(False)
            self._action_combo.setEnabled(False)
            add_btn.setEnabled(False)
            # 设备列表为空时明确说明原因，避免"面板打开但什么都点不了"
            empty_hint = QLabel("设备列表还没加载，请回到主界面点「刷新」后再来配置")
            empty_hint.setWordWrap(True)
            empty_hint.setStyleSheet(
                f"color: {SiColors.TEXT_SECONDARY}; background: transparent;"
                " font-size: 8pt;")
            box_lay.addWidget(empty_hint)
        return box

    def _selected_time(self) -> str:
        """当前选中的时刻，规范化成 HH:MM。"""
        return f"{self._hour_combo.currentText()}:{self._minute_combo.currentText()}"

    def _style_completer_popup(self) -> None:
        """给补全弹层单独上色。

        QCompleter 的 popup 是它自己创建的 QListView，不继承下拉框的
        `QComboBox QAbstractItemView` 样式；不打这个补丁，深色主题下会
        出现浅底浅字、几乎看不清候选。
        """
        completer = self._device_combo.completer()
        popup = completer.popup() if completer is not None else None
        if popup is None:
            return
        popup.setStyleSheet(
            f"QListView {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 6px;"
            f" color: {SiColors.TEXT_PRIMARY}; padding: 2px; outline: none; }}"
            f"QListView::item {{ padding: 4px 6px; border-radius: 4px; }}"
            f"QListView::item:selected {{ background: {SiColors.BTN_HOVER};"
            f" color: {SiColors.TEXT_PRIMARY}; }}")

    def _resolve_device(self) -> DeviceInfo | None:
        """把下拉框里的文本解析成设备。

        可搜索之后，用户可能只输入了片段没从候选里点选，所以不能只看
        currentIndex：先按完整标签精确匹配，再退化为"唯一包含匹配"
        （只命中一台才认，命中多台视为有歧义，交给用户继续输入）。
        """
        text = self._device_combo.currentText().strip()
        if not text:
            return None
        for device in self._devices:
            if f"{device.name} · {device.room_name}" == text:
                return device
        hits = [d for d in self._devices
                if text.lower() in f"{d.name} · {d.room_name}".lower()]
        return hits[0] if len(hits) == 1 else None

    def _on_add(self) -> None:
        from app.ui.toast import Toast

        device = self._resolve_device()
        if device is None:
            Toast.info(self, "请先选择设备：输入设备名后从候选里点一条", 3000)
            self._device_combo.setFocus()
            return
        # 两个下拉选出来的值必然合法，这里仍走一次规范化做兜底
        time_text = schedule_store.normalize_time(self._selected_time())
        if time_text is None:
            Toast.info(self, "时间不合法，请重新选择", 3000)
            return
        action = "on" if self._action_combo.currentIndex() == 0 else "off"
        rule = schedule_store.add(device.did, time_text, action)
        if rule is None:
            return
        self._rebuild()
        self.rules_changed.emit()

    # ---------- 规则列表 ----------

    def _rebuild(self) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._switches.clear()
        self._rows.clear()

        rules = schedule_store.load()
        if not rules:
            self._empty.show()
            self._scroll.hide()
            return
        self._empty.hide()
        self._scroll.show()
        names = {d.did: d for d in self._devices}
        for rule in rules:
            self._list_lay.addWidget(self._make_row(rule, names))
        self._list_lay.addStretch(1)

    def _make_row(self, rule: dict, devices: dict[str, DeviceInfo]) -> QWidget:
        row = QFrame()
        row.setObjectName("schedRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setFixedHeight(48)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.setStyleSheet(
            f"QFrame#schedRow {{ background: {SiColors.CARD};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 10px; }}"
            f"QFrame#schedRow:hover {{ background: {SiColors.CARD_HOVER}; }}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        device = devices.get(rule["did"])
        if device is None:
            # 设备被移除：规则保留但标出来，避免用户以为规则丢了却没人执行
            name_text = f"设备已移除（{rule['did'][-6:]}）"
            sub_text = "该规则不会执行，可删除"
            name_color = SiColors.OFFLINE_TEXT
        else:
            name_text = device.name
            sub_text = device.room_name if device.online else f"{device.room_name} · 离线"
            name_color = SiColors.TEXT_PRIMARY if device.online else SiColors.OFFLINE_TEXT

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        name = QLabel(name_text)
        name.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        name.setStyleSheet(f"color: {name_color}; background: transparent;")
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sub = QLabel(sub_text)
        sub.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; background: transparent; font-size: 8pt;")
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_col.addWidget(name)
        text_col.addWidget(sub)
        lay.addLayout(text_col, stretch=1)

        time_label = QLabel(f"{rule['time']} 每天")
        time_label.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        time_label.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; background: transparent;")
        lay.addWidget(time_label)

        action_label = QLabel(_ACTION_LABEL[rule["action"]])
        action_label.setFixedWidth(34)
        action_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_label.setStyleSheet(
            f"color: {_ACTION_COLOR[rule['action']]}; background: transparent;"
            " font-size: 8pt; font-weight: 600;")
        lay.addWidget(action_label)

        switch = themed_switch()
        switch.setChecked(bool(rule["enabled"]))
        _sync_switch(switch)
        switch.setToolTip("停用后保留规则但不执行")
        switch.toggled.connect(
            lambda checked, rid=rule["id"]: self._on_enabled(rid, checked))
        self._switches[rule["id"]] = switch
        lay.addWidget(switch)

        del_btn = QPushButton()
        del_btn.setFixedSize(22, 22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setToolTip("删除这条规则")
        del_btn.setIcon(qta.icon('mdi.trash-can-outline', color=SiColors.TEXT_PRIMARY))
        del_btn.setIconSize(_icon_size())
        del_btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.SURFACE}; border: none;"
            " border-radius: 11px; }"
            f"QPushButton:hover {{ background: {SiColors.DANGER}; }}")
        del_btn.clicked.connect(lambda _c=False, rid=rule["id"]: self._on_delete(rid))
        lay.addWidget(del_btn)
        row._rule_id = rule["id"]  # type: ignore[attr-defined]
        self._rows.append(row)
        return row

    def _on_enabled(self, rule_id: str, enabled: bool) -> None:
        schedule_store.set_enabled(rule_id, enabled)
        self.rules_changed.emit()

    def _on_delete(self, rule_id: str) -> None:
        if schedule_store.remove(rule_id):
            self._rebuild()
            self.rules_changed.emit()

    # ---------- 窗口 ----------

    def retheme(self) -> None:
        """主题切换：规则行与控件内联样式都是构造期求值，整表重建最稳。"""
        super().retheme()
        self._panel.setStyleSheet(
            f"QFrame#overlayPanel {{ background: {SiColors.WINDOW_BG};"
            f" border: 1px solid {SiColors.LINE}; border-radius: 16px; }}")
        # 补全弹层不随父控件样式变化，须显式重刷
        self._style_completer_popup()
        self._rebuild()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._title_bar and event.type() in (
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._header_drag_pos = (
                        event.globalPosition().toPoint()
                        - self.frameGeometry().topLeft())
                return True
            if event.type() == QEvent.Type.MouseMove:
                if (self._header_drag_pos is not None
                        and event.buttons() & Qt.MouseButton.LeftButton):
                    self.move(event.globalPosition().toPoint() - self._header_drag_pos)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._header_drag_pos = None
                return False
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._header_drag_pos = None
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._panel.setGeometry(self.rect())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._center_on_screen(400, 520)
        self.raise_()
        self._fade_in()


def _sync_switch(switch) -> None:
    """SiSwitchRefactor 自绘进度：构造/重建后必须同步一次外观。"""
    switch.progress = 1 if switch.isChecked() else 0
    switch.progress_ani.fromProperty()


def _icon_size():
    from PySide6.QtCore import QSize

    return QSize(14, 14)
