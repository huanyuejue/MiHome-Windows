# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""桌面挂件（Widgets Board）的本地持久化。

与 tray.json / workbench.json 同目录，单独文件 board.json。
与托盘快捷面板的设备清单**相互独立**：托盘是"临时呼出、用完即走"，
Board 是"常驻抬眼即用"，两者设备诉求不同（Board 放常开的灯与空调，
托盘放偶尔查看的摄像头），互不牵连。

持久化字段：
    devices  设备 did 列表，顺序即展示顺序
    columns  一行放多少个设备（1–6）
    opacity  窗口透明度（0.30–1.00，拖动滑块时实时写入）
    geom     窗口位置与宽高，恢复上次摆放
    collapsed 折叠为细横条的开关
    autostart 随应用启动自动显示
    bg       背景色（"#rrggbb"）；None = 跟随主题
    topmost  窗口是否置顶（默认 False：与普通窗口一样会被别的窗口盖住）
"""

import re

from app.core import _json_store

_VERSION = 1
_FILENAME = "board.json"

_OPACITY_MIN, _OPACITY_MAX = 0.30, 1.00
_COLUMN_MIN, _COLUMN_MAX = 1, 6
# 只接受 #rrggbb：写盘的值一律来自取色器或预设色板，格式不对按"跟随主题"处理
_BG_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _empty() -> dict:
    return {
        "version": _VERSION,
        "devices": [],
        "columns": 3,
        "opacity": 0.92,
        "geom": None,   # [x, y, w, h]
        "collapsed": False,
        "autostart": False,
        # 上次退出时挂件是不是开着的：用于"重启后恢复上次状态"。
        # 与 autostart 的分工见 get_autostart 的说明。
        "visible": False,
        # None = 跟随主题（默认，与 0.3.1 之前的行为一致）
        "bg": None,
        # 默认**不置顶**：常驻置顶会把别的窗口挡住，用户要求可关
        "topmost": False,
    }


def _read_raw() -> dict:
    raw = _json_store.read_json(_json_store.data_file(_FILENAME), _empty())
    if raw.get("version") != _VERSION:
        return _empty()
    devices = raw.get("devices")
    if not isinstance(devices, list) or not all(isinstance(d, str) for d in devices):
        return _empty()
    for k, v in _empty().items():
        raw.setdefault(k, v)
    return raw


def _write_raw(raw: dict) -> None:
    _json_store.write_json(_json_store.data_file(_FILENAME), raw)


def _clamp(value: float, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def load_devices() -> list[str]:
    """Board 设备 did 列表，按添加顺序。"""
    return list(_read_raw().get("devices", []))


def save_devices(dids: list[str]) -> None:
    raw = _read_raw()
    seen: set[str] = set()
    uniq: list[str] = []
    for d in dids:
        s = str(d)
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    raw["devices"] = uniq
    _write_raw(raw)


def get_columns() -> int:
    """一行放多少个设备，仅认 1–6，其余值回退 3。"""
    try:
        value = int(_read_raw().get("columns", 3))
    except (TypeError, ValueError):
        return 3
    return value if _COLUMN_MIN <= value <= _COLUMN_MAX else 3


def set_columns(value: int) -> None:
    raw = _read_raw()
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 3
    raw["columns"] = min(max(v, _COLUMN_MIN), _COLUMN_MAX)
    _write_raw(raw)


def get_opacity() -> float:
    """窗口透明度，范围 0.30–1.00，越界钳制。"""
    return round(_clamp(_read_raw().get("opacity", 0.92), _OPACITY_MIN, _OPACITY_MAX, 0.92), 2)


def set_opacity(value: float) -> None:
    raw = _read_raw()
    raw["opacity"] = round(_clamp(value, _OPACITY_MIN, _OPACITY_MAX, 0.92), 2)
    _write_raw(raw)


def get_geometry() -> tuple[int, int, int, int] | None:
    """上次摆放的位置与尺寸；无记录或格式不对时返回 None。"""
    geom = _read_raw().get("geom")
    if not isinstance(geom, list) or len(geom) != 4:
        return None
    try:
        x, y, w, h = (int(v) for v in geom)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def set_geometry(x: int, y: int, w: int, h: int) -> None:
    raw = _read_raw()
    raw["geom"] = [int(x), int(y), int(w), int(h)]
    _write_raw(raw)


def get_collapsed() -> bool:
    """是否处于折叠（细横条）状态。"""
    return bool(_read_raw().get("collapsed", False))


def set_collapsed(value: bool) -> None:
    raw = _read_raw()
    raw["collapsed"] = bool(value)
    _write_raw(raw)


def get_autostart() -> bool:
    """应用启动时是否**强制**显示挂件。

    与 `get_visible` 的分工：
    - `autostart=True`：每次启动都显示，不管上次是开还是关；
    - `autostart=False`（默认）：跟随上次退出时的状态（`visible`）。
    两者合起来的效果是"不勾也能记住，勾上则强制常开"。
    """
    return bool(_read_raw().get("autostart", False))


def set_autostart(value: bool) -> None:
    raw = _read_raw()
    raw["autostart"] = bool(value)
    _write_raw(raw)


def get_visible() -> bool:
    """上次挂件是否处于显示状态（由用户开关动作记录，不是每条 hide 都写）。

    只记录**用户意图**：显示、点 ×、托盘菜单隐藏。
    应用退出时的被动隐藏、以及启动时不满足条件而隐藏，都不会覆盖它——
    否则一关程序就把"上次是开着的"抹掉，重启自然回不到原状态。
    """
    return bool(_read_raw().get("visible", False))


def set_visible(value: bool) -> None:
    raw = _read_raw()
    raw["visible"] = bool(value)
    _write_raw(raw)


def get_bg() -> str | None:
    """挂件背景色；None 表示跟随主题（默认）。

    只认 `#rrggbb`，非法值（手工改坏、旧版本遗留）一律当作跟随主题，
    不让一个坏字符串把挂件刷成透明或全黑。
    """
    value = _read_raw().get("bg")
    if isinstance(value, str) and _BG_PATTERN.match(value):
        return value.lower()
    return None


def set_bg(value: str | None) -> None:
    raw = _read_raw()
    if value is None:
        raw["bg"] = None
    elif isinstance(value, str) and _BG_PATTERN.match(value):
        raw["bg"] = value.lower()
    else:
        # 非法输入不写盘：宁可不改，也不要把配色改坏
        return
    _write_raw(raw)


def get_topmost() -> bool:
    """挂件是否置顶；默认 False（与普通窗口一致，会被其他窗口盖住）。"""
    return bool(_read_raw().get("topmost", False))


def set_topmost(value: bool) -> None:
    raw = _read_raw()
    raw["topmost"] = bool(value)
    _write_raw(raw)