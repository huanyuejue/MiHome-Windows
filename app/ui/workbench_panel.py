# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""设备详情工作台：可自定义的模块化控制台。

空状态引导添加，网格承载已选模块，更多菜单提供增删改，
侧滑抽屉展示全量功能清单。配置按 did 持久化到 workbench.json。
"""

import shiboken6
from typing import Any, Callable

import qtawesome as qta

from PySide6.QtCore import QParallelAnimationGroup, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core import name_override_store, workbench_store
from app.core.models import DeviceDetail, DeviceInfo, PropInfo
from app.core.service import MijiaService, format_metrics_text
from app.ui.add_drawer import AddDrawer
from app.ui.prop_widgets import (
    GroupSection,
    build_prop_section,
    group_props,
)
from app.ui.si_theme import SiColors, primary_button_qss, themed_label
from app.ui.workbench_item import WorkbenchItemWrapper


class _SpinCircle(QWidget):
    """简易旋转弧形加载指示器。"""

    def __init__(self, size: int = 36, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._rotate)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(16)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _rotate(self):
        if not shiboken6.isValid(self):
            self._timer.stop()
            return
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        pen = QPen(QColor(SiColors.THEME), max(2, s // 12), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = s // 2
        r = m - s // 12
        p.drawArc(m - r, m - r, r * 2, r * 2, self._angle * 16, 270 * 16)
        p.end()


class WorkbenchPanel(QWidget):
    metrics_updated = Signal(str, str)
    metrics_config_changed = Signal(str)  # noqa: N815 did，卡片外显改过
    name_changed = Signal(str)  # did，主界面据此刷新卡片与托盘

    def __init__(self, service: MijiaService, jobs, parent=None):
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        self._current_did: str | None = None
        self._current_online: bool = True
        # 设备名与房间来自列表数据，不依赖详情是否加载成功：无公开
        # 规格的设备拉不到详情，仍要能显示名字并支持改名
        self._current_name: str = ""
        self._current_room: str = ""
        # 列表里的设备对象引用：主窗口改名后会原地更新它的 name 与
        # cloud_name，面板读这个对象才能拿到最新值，而不是打开时的快照
        self._device: DeviceInfo | None = None
        self._detail: DeviceDetail | None = None
        self._sections: dict[str, QWidget] = {}
        self._module_defs: list[tuple[str, str, str]] = []
        self._module_builders: dict[str, Callable[[], QWidget]] = {}
        self._active_keys: list[str] = []
        self._wrappers: dict[str, WorkbenchItemWrapper] = {}
        self._mode = "normal"
        self._drawer: AddDrawer | None = None
        # 重排动画进行中标志：动画期间布局被禁用，再入会以错误几何结算
        self._reordering = False
        self._reorder_group: QParallelAnimationGroup | None = None
        self._current_groups: list[tuple[str, list[PropInfo], PropInfo | None]] = []
        # 写后静默与乐观值：避免设备未落盘前的旧值把 UI 弹回
        self._cooldown: dict[str, float] = {}
        self._last_written: dict[str, Any] = {}
        self._last_written_time: dict[str, float] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 离线条
        self._offline_bar = QFrame()
        self._offline_bar.setStyleSheet(
            f"QFrame {{ background: {SiColors.WARN_BG}; border: 1px solid {SiColors.WARN_BORDER}; border-radius: 8px; }}")
        ob_lay = QHBoxLayout(self._offline_bar)
        ob_lay.setContentsMargins(12, 6, 12, 6)
        lab = QLabel("设备离线，控制不可用")
        lab.setStyleSheet(f"color: {SiColors.WARN_TEXT}; background: transparent; font-size: 9pt;")
        ob_lay.addWidget(lab)
        ob_lay.addStretch(1)
        self._offline_bar.hide()
        root.addWidget(self._offline_bar)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._title_label = QLabel("未选择设备")
        self._title_label.setObjectName("panelTitle")
        title_row.addWidget(self._title_label)
        # 改名按钮贴着名字放：离得远时用户不容易注意到这个入口
        self._rename_btn = QPushButton()
        self._rename_btn.setFixedSize(26, 26)
        self._rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rename_btn.setToolTip("修改设备名称")
        self._rename_btn.clicked.connect(self._rename_device)
        self._rename_btn.hide()
        title_row.addWidget(self._rename_btn)

        # 自定义名称的标记：只在用户改过名时出现，提示当前显示的不是云端名
        self._custom_badge = QLabel("自定义名称")
        self._custom_badge.setObjectName("customNameBadge")
        self._custom_badge.hide()
        title_row.addWidget(self._custom_badge)
        title_row.addStretch(1)
        title_box.addLayout(title_row)
        self._model_label = QLabel("选择一台设备开始控制")
        self._model_label.setObjectName("panelSubtitle")
        title_box.addWidget(self._model_label)
        header.addLayout(title_box)
        header.addStretch(1)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.setFixedSize(64, 32)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(lambda: self._reload_readings(force=True))
        self._refresh_btn.hide()
        header.addWidget(self._refresh_btn)
        # 卡片外显紧挨刷新：两者都影响"看到什么数值"，放在一起符合直觉
        self._metrics_btn = QPushButton("外显值设置")
        # 宽度按文字自适应并留出左右内边距：固定高度但不定宽时，
        # 样式里的 padding 不参与宽度计算，文字会顶到描边上
        self._metrics_btn.setFixedHeight(32)
        self._metrics_btn.setMinimumWidth(
            self._metrics_btn.fontMetrics().horizontalAdvance("外显值设置") + 30)
        self._metrics_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._metrics_btn.setToolTip("设置设备卡片副标题显示的值")
        self._metrics_btn.clicked.connect(self._pick_metrics)
        self._metrics_btn.hide()
        header.addWidget(self._metrics_btn)
        self._edit_btn = QPushButton("编辑功能")
        self._edit_btn.setFixedSize(80, 32)
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.clicked.connect(self._toggle_manage)
        self._edit_btn.hide()
        header.addWidget(self._edit_btn)
        self._apply_header_button_styles()
        root.addLayout(header)

        # 加载态：设备信息 + 旋转弧形 + 提示，垂直居中
        self._loading = QFrame()
        self._loading.setStyleSheet("QFrame { background: transparent; }")
        load_lay = QVBoxLayout(self._loading)
        load_lay.setAlignment(Qt.AlignCenter)
        load_lay.setSpacing(16)

        self._load_name = QLabel()
        self._load_name.setAlignment(Qt.AlignCenter)
        self._load_name.setStyleSheet(
            f"color: {SiColors.TEXT_PRIMARY}; font-size: 16pt; font-weight: bold; background: transparent;")
        load_lay.addWidget(self._load_name, alignment=Qt.AlignCenter)

        self._load_room = QLabel()
        self._load_room.setAlignment(Qt.AlignCenter)
        self._load_room.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; font-size: 10pt; background: transparent;")
        load_lay.addWidget(self._load_room, alignment=Qt.AlignCenter)

        self._spinner = _SpinCircle(36)
        load_lay.addWidget(self._spinner, alignment=Qt.AlignCenter)

        self._load_hint = QLabel("正在获取设备信息…")
        self._load_hint.setAlignment(Qt.AlignCenter)
        self._load_hint.setStyleSheet(
            f"color: {SiColors.TEXT_SECONDARY}; font-size: 9pt; background: transparent;")
        load_lay.addWidget(self._load_hint, alignment=Qt.AlignCenter)

        self._loading.hide()
        root.addWidget(self._loading, stretch=1)

        # 瀑布流网格：两列自适应填满，避免强制 2 列留白
        body = QScrollArea()
        body.setWidgetResizable(True)
        body.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        container.setStyleSheet(f"background: {SiColors.WINDOW_BG};")
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 0, 8, 0)
        outer.setSpacing(14)
        self._left_col = QVBoxLayout()
        self._left_col.setContentsMargins(0, 0, 0, 0)
        self._left_col.setSpacing(14)
        self._right_col = QVBoxLayout()
        self._right_col.setContentsMargins(0, 0, 0, 0)
        self._right_col.setSpacing(14)
        outer.addLayout(self._left_col, stretch=1)
        outer.addLayout(self._right_col, stretch=1)
        # 兼容旧代码：_grid 指向 container 布局，用于清理
        self._grid = outer
        self._left_h = 0
        self._right_h = 0
        body.setWidget(container)
        root.addWidget(body, stretch=1)

        # 空状态
        self._empty = QFrame()
        self._empty.setStyleSheet("QFrame { background: transparent; }")
        el = QVBoxLayout(self._empty)
        el.setContentsMargins(20, 40, 20, 40)
        el.setSpacing(12)
        icon = QLabel("◫")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {SiColors.OFFLINE_SUB}; font-size: 28pt; background: transparent;")
        el.addWidget(icon)
        self._empty_title = QLabel("此设备控制面板还是空的")
        self._empty_title.setAlignment(Qt.AlignCenter)
        self._empty_title.setStyleSheet(f"color: {SiColors.TEXT_PRIMARY}; font-size: 11pt; background: transparent;")
        el.addWidget(self._empty_title)
        self._empty_desc = QLabel("点击下方按钮添加控制功能")
        self._empty_desc.setAlignment(Qt.AlignCenter)
        self._empty_desc.setStyleSheet(f"color: {SiColors.TEXT_SECONDARY}; font-size: 9pt; background: transparent;")
        el.addWidget(self._empty_desc)
        el.addSpacing(12)
        self._empty_add_btn = QPushButton("添加功能")
        self._empty_add_btn.setCursor(Qt.PointingHandCursor)
        self._empty_add_btn.setFixedHeight(40)
        self._empty_add_btn.setStyleSheet(
            primary_button_qss("0 24px", 12, "10pt"))
        self._empty_add_btn.clicked.connect(self._open_drawer)
        el.addWidget(self._empty_add_btn, alignment=Qt.AlignCenter)
        el.addStretch(1)
        root.addWidget(self._empty)

        # 底部添加按钮仅在管理模式下显示
        self._footer_add_btn = QPushButton("+ 添加功能")
        self._footer_add_btn.setCursor(Qt.PointingHandCursor)
        self._footer_add_btn.setFixedHeight(40)
        self._footer_add_btn.setStyleSheet(
            f"QPushButton {{ background: {SiColors.CARD}; border: 1px solid {SiColors.THEME};"
            f" border-radius: 12px; color: {SiColors.THEME}; font-size: 10pt; }}"
            f"QPushButton:hover {{ background: {SiColors.CARD_HOVER}; }}")
        self._footer_add_btn.clicked.connect(self._open_drawer)
        self._footer_add_btn.hide()
        root.addWidget(self._footer_add_btn)

        self._body = body
        self._container = container
        self.setAcceptDrops(True)
        container.setAcceptDrops(True)

    # ---------- 加载 ----------

    def show_device(self, did: str, online: bool = True, device: DeviceInfo | None = None) -> None:
        self._current_did = did
        self._current_online = online
        self._detail = None
        self._clear_workbench()
        name = device.name if device else "设备"
        room = device.room_name if device else ""
        self._current_name = name
        self._current_room = room
        self._device = device
        self._title_label.setText(name)
        self._model_label.setText(room)
        self._title_label.hide()
        self._model_label.hide()
        self._custom_badge.hide()
        self._rename_btn.hide()
        self._load_name.setText(name)
        self._load_room.setText(room)
        self._load_hint.setText("正在获取设备信息…")
        self._update_offline_bar()
        self._refresh_btn.hide()
        self._metrics_btn.hide()
        self._edit_btn.hide()
        self._loading.show()
        self._jobs.submit(
            lambda: self._service.device_detail(did),
            on_success=self._on_detail_loaded,
            on_error=self._show_error,
        )

    def _on_detail_loaded(self, detail: DeviceDetail) -> None:
        if not shiboken6.isValid(self) or detail.did != self._current_did:
            return
        self._loading.hide()
        self._detail = detail
        self._model_label.setText(detail.model)
        self._title_label.show()
        self._model_label.show()
        self._rename_btn.show()
        self._apply_display_name()
        self._build_module_defs(detail)
        keys = workbench_store.load(detail.did)
        # 预置开关：首次进入且存在可写开关时自动加入
        if keys is None:
            pre = self._default_keys()
            if pre:
                keys = pre
                workbench_store.save(detail.did, keys)
            else:
                keys = []
        # 过滤已不存在的 key
        valid = {k for k, _, _ in self._module_defs}
        keys = [k for k in keys if k in valid]
        self._active_keys = keys
        self._render_workbench()
        self._refresh_btn.show()
        self._metrics_btn.show()
        self._edit_btn.show()

    def _default_name(self) -> str:
        """没有自定义名时该显示的名字（即"恢复默认"的目标）。

        优先用设备对象上的 cloud_name：主窗口本地化设备名时会同步
        更新它，比详情接口返回的原始名更贴近用户看到的展示名。
        """
        if self._device is not None and self._device.cloud_name:
            return self._device.cloud_name
        if self._detail is not None:
            return self._detail.name
        return self._current_name

    def _apply_display_name(self) -> None:
        """按自定义名称的优先级刷新标题与提示标记。"""
        cloud_name = self._default_name()
        custom = name_override_store.get(self._current_did or "")
        full = custom or cloud_name
        # 标题栏空间有限，超长名字省略显示，完整内容放 tooltip。
        # 可用宽度按面板宽度推算（右侧还有刷新/编辑按钮），窗口很窄
        # 时不能再用固定值，否则文字会溢出到按钮下面。
        avail = max(120, min(520, self.width() - 220))
        shown = self._title_label.fontMetrics().elidedText(
            full, Qt.TextElideMode.ElideRight, avail)
        self._title_label.setText(shown)
        self._title_label.setToolTip(full)
        self._custom_badge.setVisible(bool(custom))

    def _pick_metrics(self) -> None:
        """选择这张设备卡片副标题要显示哪些数值属性。"""
        if self._detail is None or not self._current_did:
            return
        from app.core import settings_store
        from app.ui.metrics_pick_dialog import MetricsPickDialog

        saved = settings_store.get_metrics_selection()
        # 键不存在表示从未设置，传 None 让对话框预勾默认温湿度；
        # 空列表表示用户已明确选择不外显，保持全空
        current = saved.get(self._current_did)
        dialog = MetricsPickDialog(self._detail.props, current, self)
        accepted = dialog.exec() == MetricsPickDialog.DialogCode.Accepted
        props = dialog.result_props()
        restored = dialog.restored_default()
        untouched = dialog.untouched_default()
        dialog.deleteLater()
        if not accepted:
            return
        if restored:
            settings_store.clear_metrics_selection(self._current_did)
        elif untouched:
            # 原样没动：不写配置，保持默认语义
            pass
        else:
            # 空列表有意义：用户明确选择了不外显
            settings_store.set_metrics_selection(self._current_did, props)
        self.metrics_config_changed.emit(self._current_did)

    def _rename_device(self) -> None:
        """打开改名对话框；留空或点恢复默认都会还原云端原名。

        不依赖详情是否加载成功：无公开规格的设备拉不到详情，
        但名字仍来自设备列表，照样可以改。
        """
        if not self._current_did:
            return
        from app.ui.rename_dialog import RenameDeviceDialog

        cloud_name = self._default_name()
        current = name_override_store.get(self._current_did) or cloud_name
        dialog = RenameDeviceDialog(current, cloud_name, self)
        accepted = dialog.exec() == RenameDeviceDialog.DialogCode.Accepted
        restore = dialog.restoring()
        value = dialog.value()
        dialog.deleteLater()
        if not accepted:
            return
        name_override_store.set_name(
            self._current_did, "" if restore else value)
        self._apply_display_name()
        # 主界面卡片、托盘、挂件都按设备对象里的名字展示，需要同步刷新
        self.name_changed.emit(self._current_did)

    def _default_keys(self) -> list[str]:
        # 优先选择独立的 on 开关（最常用的控制方式）
        for k, _, _ in self._module_defs:
            if k == "on" or k.startswith("on-") or k.startswith("on_"):
                return [k]
        # 其次选择组合功能中主开关为 on 类型的
        for k, _, _ in self._module_defs:
            if k.startswith("group:"):
                # 检查组合功能的主开关是否为 on 类型
                group_key = k.replace("group:", "")
                for gkey, members, master in self._current_groups:
                    if gkey == group_key and master:
                        if master.name == "on" or master.name.startswith("on-") or master.name.startswith("on_"):
                            return [k]
        # 再次选择组合功能
        for k, _, _ in self._module_defs:
            if k.startswith("group:"):
                return [k]
        # 最后选择标题包含"开关"的属性
        for k, title, _ in self._module_defs:
            if "开关" in title:
                return [k]
        return []

    @staticmethod
    def _cn_title(raw: str) -> str:
        """默认仅显示中文：'Brightness / 亮度' -> '亮度'"""
        if "/" in raw:
            return raw.split("/")[-1].strip()
        return raw.strip()

    def _build_module_defs(self, detail: DeviceDetail) -> None:
        self._module_defs.clear()
        self._module_builders.clear()
        self._current_groups.clear()

        def _desc_for_prop(p: PropInfo) -> str:
            # 区分可读只读与不可读，避免把 rw="" 的空卡当可展示项误导添加
            if p.writable:
                rw_tag = "可写"
            elif p.readable:
                rw_tag = "只读"
            else:
                rw_tag = "不可读"
            parts = [rw_tag, p.type]
            if p.range:
                parts.append(f"范围 {p.range[0]}-{p.range[1]}")
            if p.value_list:
                parts.append(f"{len(p.value_list)} 选项")
            if not p.readable and not p.writable:
                parts.append("不支持操作/读取")
            return " · ".join(parts)

        # 保存分组信息供 _default_keys 使用
        groups = group_props(detail.props)
        self._current_groups = groups

        for gkey, members, master in groups:
            if master is not None:
                key = f"group:{gkey}"
                title = self._cn_title(master.desc or gkey)
                desc = f"组合 · {len(members)} 项"
                self._module_defs.append((key, title, desc))
                # 捕获
                def _mk_group(km=gkey, mem=members, ma=master):
                    # 仅展示中文
                    cn_master = PropInfo(
                        name=ma.name, desc=self._cn_title(ma.desc), type=ma.type,
                        readable=ma.readable, writable=ma.writable,
                        range=ma.range, value_list=ma.value_list)
                    cn_members = [
                        PropInfo(name=p.name, desc=self._cn_title(p.desc), type=p.type,
                                 readable=p.readable, writable=p.writable,
                                 range=p.range, value_list=p.value_list)
                        for p in mem if p is not ma
                    ] + [cn_master]
                    # 用中文标题的 master 覆盖
                    sec = GroupSection(km, cn_members, cn_master,
                        write=lambda prop, value: self._write(prop, prop.name, value))
                    sec.set_master_write(lambda state, m=cn_master: self._write(m, m.name, state))
                    self._sections[ma.name] = sec
                    for p in mem:
                        if p is not ma:
                            # 刷新链路用原始 prop 名，但 section 已注册 master 名即可
                            pass
                    return sec
                self._module_builders[key] = _mk_group
            else:
                for m in members:
                    key = m.name
                    cn = self._cn_title(m.desc)
                    # 重名卡片加后缀区分（如两个“静音”）
                    existing_titles = {t for _, t, _ in self._module_defs}
                    if cn in existing_titles:
                        cn = f"{cn} ({m.name})"
                    self._module_defs.append((key, cn, _desc_for_prop(m)))
                    def _mk_single(pm=m, cn2=cn):
                        cn_prop = PropInfo(
                            name=pm.name, desc=cn2, type=pm.type,
                            readable=pm.readable, writable=pm.writable,
                            range=pm.range, value_list=pm.value_list)
                        sec = build_prop_section(cn_prop,
                            write=lambda value, name=pm.name, p=pm: self._write(p, name, value))
                        self._sections[pm.name] = sec
                        return sec
                    self._module_builders[key] = _mk_single

        # 每个动作独立成卡，不再聚合为“更多操作”
        text_actions = {"execute-text-directive", "play-text", "play-music", "play-radio", "execute-text-directive"}
        for act in detail.actions:
            key = f"action:{act.name}"
            title = self._cn_title(act.desc)
            existing = {t for _, t, _ in self._module_defs}
            if title in existing:
                title = f"{title} ({act.name})"
            desc = f"动作 · {act.name}"
            self._module_defs.append((key, title, desc))
            def _mk_single_action(a=act, _needs_text=(act.name in text_actions)):
                card = QFrame()
                card.setObjectName("propCard")
                card.prop = PropInfo(name=a.name, desc=a.desc, type="action", readable=False, writable=False)
                card.refresh_value = lambda v: None  # type: ignore
                if _needs_text:
                    lay = QVBoxLayout(card)
                    lay.setContentsMargins(20, 12, 20, 12)
                    lay.setSpacing(8)
                    lab = themed_label(self._cn_title(a.desc), SiColors.TEXT_PRIMARY)
                    lab.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
                    lay.addWidget(lab)
                    row = QHBoxLayout()
                    row.setSpacing(8)
                    from PySide6.QtWidgets import QLineEdit
                    edit = QLineEdit()
                    ph = "输入要执行的指令…" if a.name == "execute-text-directive" else "输入文本…"
                    edit.setPlaceholderText(ph)
                    edit.setFixedHeight(32)
                    edit.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    edit.setStyleSheet(
                        f"QLineEdit {{ background: {SiColors.WINDOW_BG}; border: 1px solid {SiColors.LINE}; border-radius: 8px; "
                        f"padding: 6px 10px; color: {SiColors.TEXT_PRIMARY};"
                        f" selection-background-color: {SiColors.THEME}; font-size: 10pt; }}"
                        f"QLineEdit:focus {{ border-color: {SiColors.THEME}; }}"
                    )
                    row.addWidget(edit, 1)
                    btn = QPushButton("执行")
                    btn.setFixedSize(64, 32)
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setStyleSheet(primary_button_qss("0", 8, "9pt"))
                    def _do_exec(n=a.name, e=edit):
                        txt = e.text().strip()
                        if not txt:
                            e.setFocus()
                            from app.ui.toast import Toast
                            Toast.info(self, "请输入文本", 2000)
                            return
                        self._run_action(n, txt)
                    btn.clicked.connect(lambda _, f=_do_exec: f())
                    edit.returnPressed.connect(lambda f=_do_exec: f())
                    row.addWidget(btn)
                    lay.addLayout(row)
                else:
                    lay = QHBoxLayout(card)
                    lay.setContentsMargins(20, 14, 20, 14)
                    lay.setSpacing(12)
                    lab = themed_label(self._cn_title(a.desc), SiColors.TEXT_PRIMARY)
                    lab.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.DemiBold))
                    lay.addWidget(lab)
                    lay.addStretch(1)
                    btn = QPushButton("执行")
                    btn.setFixedSize(64, 32)
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setStyleSheet(primary_button_qss("0", 8, "9pt"))
                    btn.clicked.connect(lambda _, n=a.name: self._run_action(n))
                    lay.addWidget(btn)
                card.setFixedHeight(76)
                return card
            self._module_builders[key] = _mk_single_action

    def _render_workbench(self) -> None:
        self._clear_grid()
        if not self._active_keys:
            # 属性与动作皆空的设备（如部分 BLE 类产品）没有可控制的
            # 功能，给出明确提示并隐藏“添加功能”入口
            no_funcs = bool(self._detail) and not self._detail.props and not self._detail.actions
            self._empty_title.setText(
                "该设备无公开的可控制功能"
                if no_funcs else "此设备控制面板还是空的")
            self._empty_desc.setText(
                "该设备未发布功能规格（常见于蓝牙类产品），无法提供控制面板"
                if no_funcs else "点击下方按钮添加控制功能")
            self._empty_add_btn.setVisible(not no_funcs)
            self._empty.show()
            self._body.hide()
            self._footer_add_btn.hide()
            return
        self._empty.hide()
        self._body.show()
        self._footer_add_btn.setVisible(self._mode == "manage")
        self._left_h = 0
        self._right_h = 0
        for key in list(self._active_keys):
            builder = self._module_builders.get(key)
            if builder is None:
                continue
            inner = builder()
            wrapper = WorkbenchItemWrapper(key, inner)
            wrapper.request_delete.connect(self._on_delete)
            wrapper.request_move.connect(self._on_move)
            wrapper.set_mode(self._mode)
            if not self._current_online:
                wrapper.set_offline(True)
                inner.setEnabled(False)
            self._wrappers[key] = wrapper
            # 瀑布流分栏高度估算（与重排动画共用同一公式）
            h = wrapper.estimated_height()
            if self._left_h <= self._right_h:
                self._left_col.addWidget(wrapper)
                self._left_h += h + 14
            else:
                self._right_col.addWidget(wrapper)
                self._right_h += h + 14
        self._left_col.addStretch(1)
        self._right_col.addStretch(1)
        # 批量回读
        self._reload_readings()

    def _clear_workbench(self) -> None:
        self._clear_grid()
        self._empty.hide()
        self._footer_add_btn.hide()
        self._body.hide()
        self._refresh_btn.hide()
        self._metrics_btn.hide()
        self._rename_btn.hide()
        self._custom_badge.hide()

    def _clear_grid(self) -> None:
        # 重排动画进行中切换设备时，先停掉动画：动画目标 wrapper 随即
        # 被 deleteLater，带着已删除对象跑几何动画会崩。
        # 注意 stop() 中途停止不发 finished（自然结束才发），状态须手动复位
        group = self._reorder_group
        if group is not None:
            self._reorder_group = None
            try:
                group.stop()
            except Exception:
                pass
            self._reordering = False
            self._container.layout().setEnabled(True)
            group.deleteLater()
        self._sections.clear()
        self._wrappers.clear()
        for lay in (self._left_col, self._right_col):
            while lay.count():
                item = lay.takeAt(0)
                if w := item.widget():
                    w.deleteLater()
                elif item.layout():
                    # 跳过 stretch
                    pass

    def _update_offline_bar(self) -> None:
        self._offline_bar.setVisible(not self._current_online)

    # ---------- 抽屉 ----------

    def _open_drawer(self) -> None:
        if self._detail is None:
            return
        if self._drawer is not None and shiboken6.isValid(self._drawer):
            self._drawer.reject()
            self._drawer.deleteLater()
        self._drawer = AddDrawer(self.window())
        self._drawer.set_modules(self._module_defs, set(self._active_keys))
        self._drawer.add_module.connect(self._on_toggle_from_drawer)
        self._drawer.exec()
        self._drawer.deleteLater()
        self._drawer = None

    def _on_toggle_from_drawer(self, key: str) -> None:
        if self._current_did is None:
            return
        if key in self._active_keys:
            self._on_delete(key)
            if self._drawer is not None:
                # 移除后卡片恢复为未添加样式
                if card := self._drawer._cards.get(key):
                    card.set_added(False)
        else:
            self._on_add(key)

    def _on_add(self, key: str) -> None:
        if key in self._active_keys or self._current_did is None:
            return
        self._active_keys.append(key)
        workbench_store.save(self._current_did, self._active_keys)
        if self._drawer is not None:
            self._drawer.mark_added(key)
        self._render_workbench()

    def _on_delete(self, key: str) -> None:
        if key not in self._active_keys or self._current_did is None:
            return
        self._active_keys.remove(key)
        workbench_store.save(self._current_did, self._active_keys)
        self._render_workbench()

    def _on_move(self, key: str, direction: int) -> None:
        if key not in self._active_keys or self._current_did is None:
            return
        idx = self._active_keys.index(key)
        new_idx = idx + direction
        if not 0 <= new_idx < len(self._active_keys):
            return
        old_geoms = {k: w.geometry() for k, w in self._wrappers.items()}
        self._active_keys[idx], self._active_keys[new_idx] = self._active_keys[new_idx], self._active_keys[idx]
        workbench_store.save(self._current_did, self._active_keys)
        self._reorder_with_animation(old_geoms)

    # ---------- 管理模式 ----------

    def _apply_header_button_styles(self) -> None:
        # 浅色模式下 SURFACE(#eef0f3) 压在 WINDOW_BG(#f2f3f5) 上几乎看不见，
        # 改用"白底 + 淡描边"的控件底色，并显式指定文字色，避免继承到灰字
        styles = (
            f"QPushButton {{ background: {SiColors.CTRL_BG};"
            f" border: 1px solid {SiColors.CTRL_BORDER}; border-radius: 8px;"
            f" color: {SiColors.TEXT_PRIMARY}; font-size: 9pt; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {SiColors.CTRL_BG_HOVER}; }}"
            f"QPushButton[active=\"true\"] {{ background: {SiColors.THEME};"
            f" border-color: {SiColors.THEME};"
            f" color: {SiColors.ON_THEME_TEXT}; }}")
        self._refresh_btn.setStyleSheet(styles)
        self._metrics_btn.setStyleSheet(styles)
        self._edit_btn.setStyleSheet(styles)
        # 改名按钮贴着小标题，做成无底色的图标按钮，避免和右侧
        # 操作按钮抢视觉
        icon_btn_qss = (
            "QPushButton { background: transparent; border: none;"
            " border-radius: 6px; }"
            f"QPushButton:hover {{ background: {SiColors.CTRL_BG_HOVER}; }}")
        self._rename_btn.setStyleSheet(icon_btn_qss)
        self._rename_btn.setIcon(
            qta.icon("mdi.pencil", color=SiColors.TEXT_SECONDARY))
        self._custom_badge.setStyleSheet(
            f"color: {SiColors.THEME}; background: {SiColors.SURFACE};"
            " border-radius: 6px; padding: 1px 8px; font-size: 8.5pt;")

    def retheme(self) -> None:
        """主题切换：刷新头部按钮与已加载的功能区块（内联样式冻结）。"""
        self._apply_header_button_styles()
        for btn in (self._refresh_btn, self._metrics_btn, self._edit_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if self._detail is not None:
            self._render_workbench()

    def _toggle_manage(self) -> None:
        self._set_mode("normal" if self._mode == "manage" else "manage")

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        is_manage = mode == "manage"
        self._edit_btn.setText("完成" if is_manage else "编辑功能")
        self._edit_btn.setProperty("active", is_manage)
        self._edit_btn.style().unpolish(self._edit_btn)
        self._edit_btn.style().polish(self._edit_btn)
        for w in self._wrappers.values():
            w.set_mode(mode)
        self._footer_add_btn.setVisible(is_manage and bool(self._active_keys))

    def _reorder_with_animation(self, old_geoms: dict) -> None:
        # 复用已有 wrapper，仅重排布局并做滑动动画
        if self._reordering:
            return
        self._reordering = True
        # 1. 移除但不删除
        for lay in (self._left_col, self._right_col):
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.hide()
        # 2. 按新顺序以瀑布流重新加入（高度公式与 _render_workbench 一致）
        left_h = right_h = 0
        for key in list(self._active_keys):
            w = self._wrappers.get(key)
            if w is None:
                continue
            h = w.estimated_height()
            if left_h <= right_h:
                self._left_col.addWidget(w)
                left_h += h + 14
            else:
                self._right_col.addWidget(w)
                right_h += h + 14
            w.show()
        self._left_col.addStretch(1)
        self._right_col.addStretch(1)
        # 3. 强制布局计算新几何
        self._container.layout().activate()
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()
        new_geoms = {k: w.geometry() for k, w in self._wrappers.items()}
        # 4. 禁用布局，准备动画
        self._container.layout().setEnabled(False)
        group = QParallelAnimationGroup(self)

        def _release() -> None:
            self._container.layout().setEnabled(True)
            self._container.update()
            self._reordering = False
            if self._reorder_group is group:
                self._reorder_group = None
            group.deleteLater()

        for key, w in self._wrappers.items():
            og = old_geoms.get(key)
            ng = new_geoms.get(key)
            if og is None or ng is None or og == ng:
                continue
            w.setGeometry(og)
            anim = QPropertyAnimation(w, b"geometry")
            anim.setDuration(260)
            anim.setStartValue(og)
            anim.setEndValue(ng)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(anim)
        group.finished.connect(_release)
        self._reorder_group = group
        if group.animationCount() > 0:
            group.start()
        else:
            _release()

    # ---------- 拖拽排序 ----------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._mode != "manage":
            event.ignore()
            return
        if event.mimeData().hasText() and event.mimeData().text() in self._active_keys:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._mode == "manage" and event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        if self._mode != "manage":
            event.ignore()
            return
        from_key = event.mimeData().text()
        if from_key not in self._active_keys:
            event.ignore()
            return
        # 找到落点下的目标卡片
        pos = self._container.mapFrom(self, event.position().toPoint())
        target = None
        for key, wrapper in self._wrappers.items():
            if wrapper.geometry().contains(pos):
                target = key
                break
        # 记录旧几何用于滑动动画
        old_geoms = {k: w.geometry() for k, w in self._wrappers.items()}
        if target is None or target == from_key:
            # 拖到空白处 -> 移到末尾
            if from_key in self._active_keys:
                self._active_keys.remove(from_key)
                self._active_keys.append(from_key)
                workbench_store.save(self._current_did, self._active_keys)
                self._reorder_with_animation(old_geoms)
            event.acceptProposedAction()
            return
        # 插入到目标之前
        self._active_keys.remove(from_key)
        idx = self._active_keys.index(target)
        self._active_keys.insert(idx, from_key)
        workbench_store.save(self._current_did, self._active_keys)
        self._reorder_with_animation(old_geoms)
        event.acceptProposedAction()

    # ---------- 读写（复用 DevicePanel 逻辑） ----------

    def _write(self, prop, name: str, value: Any) -> None:
        did = self._current_did
        if did is None:
            return
        import time
        now = time.time()
        # 进入静默期：3 秒内忽略旧值回填，乐观保留用户刚写入的值
        self._cooldown[name] = now + 3.0
        self._last_written[name] = value
        self._last_written_time[name] = now
        self._jobs.submit(
            lambda: self._service.write_prop(did, name, value),
            on_success=lambda _: QTimer.singleShot(3000, lambda: self._read_single(prop, name)),
            on_error=self._show_error,
        )

    def _read_single(self, prop, name: str) -> None:
        if not prop.readable:
            return
        did = self._current_did
        sec = self._sections.get(name)
        if did is None or sec is None:
            return
        self._jobs.submit(
            lambda: self._service.read_prop(did, name),
            on_success=lambda v: self._apply_reading(sec, v),
            on_error=lambda _: None,
        )

    def _reload_readings(self, force: bool = False) -> None:
        did = self._current_did
        if did is None:
            return
        names = []
        seen = set()
        for name, sec in self._sections.items():
            if name in seen or not sec.prop.readable:
                continue
            seen.add(name)
            names.append(name)
        if not names:
            return
        if force:
            # 手动刷新仅清除静默，不清除乐观值，避免云端旧值把刚写的弹回
            for n in names:
                self._cooldown.pop(n, None)
        self._jobs.submit(
            lambda: self._service.read_props(did, names),
            on_success=lambda v: self._apply_readings(v, force=force),
            on_error=lambda _: None,
        )

    def _apply_readings(self, values: dict[str, Any], force: bool = False) -> None:
        for name, value in values.items():
            sec = self._sections.get(name)
            if sec is not None:
                self._apply_reading(sec, value, force=force)
        self._emit_metrics(values)

    def _emit_metrics(self, values: dict[str, Any]) -> None:
        # 只回传温湿度：详情面板的实时回读取的是这两项，卡片副标题
        # 若被用户改成功率等属性，由主窗口按自己的选择另行拉取
        picked: dict[str, float] = {}
        for name in ("temperature", "relative-humidity", "humidity"):
            value = values.get(name)
            if value is None:
                continue
            try:
                picked[name] = float(value)
            except (TypeError, ValueError):
                continue
        if not picked:
            return
        # 格式化与卡片副标题共用同一实现（service 层），避免两处启发式漂移
        text = format_metrics_text(picked)
        if text and self._current_did:
            self.metrics_updated.emit(self._current_did, text)

    def _apply_reading(self, section: QWidget, value: Any, force: bool = False) -> None:
        if not shiboken6.isValid(self) or not shiboken6.isValid(section):
            return
        import time
        name = section.prop.name
        now = time.time()
        if not force:
            cd = self._cooldown.get(name, 0)
            if cd and now < cd:
                return
            lw = self._last_written.get(name, None)
            lwt = self._last_written_time.get(name, 0)
            if lw is not None and now < lwt + 10 and value != lw:
                return
        if section.prop.name in self._sections:
            section.refresh_value(value)

    def _run_action(self, name: str, value=None) -> None:
        did = self._current_did
        if did is None:
            return
        # 文本类动作已在卡片上提供输入框，不再弹二次对话框
        needs_text = name in ("execute-text-directive", "play-text", "play-music", "play-radio")
        if needs_text and (value is None or (isinstance(value, str) and not value.strip())):
            from app.ui.toast import Toast
            Toast.info(self, "请输入文本", 2000)
            return
        run_value = value
        if isinstance(value, str) and name in ("execute-text-directive", "play-text"):
            run_value = [value]
        self._jobs.submit(
            lambda: self._service.run_action(did, name, run_value) if run_value is not None else self._service.run_action(did, name),
            on_success=lambda _: None,
            on_error=lambda e: self._handle_action_error(e, name),
        )

    def _handle_action_error(self, error: Exception, name: str) -> None:
        msg = str(error)
        if "-704220025" in msg or "参数个数不匹配" in msg:
            from app.ui.toast import Toast
            Toast.info(self, f"{name} 参数错误，请检查输入", 2500)
            return
        self._show_error(error)

    def _show_error(self, error: Exception) -> None:
        if not shiboken6.isValid(self):
            return
        self._loading.hide()
        self._title_label.show()
        self._model_label.show()
        # 详情拉不到也要能改名：无公开规格的设备只剩名字可以自定义，
        # 这里补上标题与改名入口，避免用户无从下手
        self._apply_display_name()
        self._rename_btn.show()
        self._load_hint.setText(f"加载失败：{error}")
        self._load_hint.setStyleSheet(
            f"color: {SiColors.ERROR_TEXT}; font-size: 9pt; background: transparent;")
        self._loading.show()


