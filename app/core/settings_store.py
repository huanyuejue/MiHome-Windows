# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""应用设置的本地持久化。

与 tray.json / workbench.json 同目录，单独文件 settings.json，
保存用户在设置界面中调整的偏好。
"""

import re
import sys
from pathlib import Path

from app.core import _json_store

_VERSION = 1
_FILENAME = "settings.json"

_DEFAULTS: dict = {
    "version": _VERSION,
    "minimize_to_tray": True,
    "start_minimized": False,
    "voice_fab_enabled": True,
    "theme": "system",  # system / light / dark
    # 主题色（#rrggbb）；空串 = 默认米家绿。派生 hover/dim 与底色上的文字色
    # 由 ui/si_theme.set_accent 统一计算，这里只存用户选的那个值。
    "theme_accent": "",
    "hide_no_func_devices": False,
    # 是否隐藏离线设备（主页与托盘均不显示），默认关闭
    "hide_offline_devices": False,
    "show_scene_tab": True,  # 主页选项卡最左侧显示「场景」入口（手动场景一键执行）
    "default_speaker_did": "",  # 小爱指令默认输出音箱；空串=自动（第一个在线）
    # 托盘音响栏正在控制哪台音箱。与 default_speaker_did 刻意分开：
    # 「对谁说话」和「调谁的音量」经常不是同一台（房间里放着歌，
    # 但语音指令想发到客厅），共用一个偏好会互相覆盖。
    "tray_audio_speaker_did": "",
    "ui_scale": 1.0,    # 界面缩放个人微调乘数（叠加在软件基准缩放之上），需重启生效
    "tray_columns": 2,  # 托盘快捷窗口卡片列数：1 或 2
    "check_update_enabled": True,  # 启动时自动检查 GitHub 新版本
    # 房间标签的自定义顺序（房间名列表）；未列出的房间按名称排在后面
    "room_order": [],
    # 启动后默认打开的标签名；空串表示「全屋」
    "default_tab": "",
    # did -> 卡片副标题要显示的属性名列表（最多两个）。键不存在表示用
    # 默认温湿度；值恰好是空列表表示用户明确选择不外显，两者语义不同
    "metrics_selection": {},
    # 托盘图标颜色：system 跟随任务栏深浅，其余为固定色
    "tray_icon_color": "system",
    # 主页设备卡片的手动顺序（did 列表）。只记录用户拖动过的设备，
    # 未列出的按默认规则（在线优先 + 名称）排在后面
    "device_order": [],
    # 场景卡片的手动顺序（scene_id 列表），语义同 device_order
    "scene_order": [],
}


def _read_raw() -> dict:
    raw = _json_store.read_json(_json_store.data_file(_FILENAME), dict(_DEFAULTS))
    if raw.get("version") != _VERSION:
        return dict(_DEFAULTS)
    # 补全缺失字段
    for k, v in _DEFAULTS.items():
        raw.setdefault(k, v)
    return raw


def _write_raw(raw: dict) -> None:
    _json_store.write_json(_json_store.data_file(_FILENAME), raw)


# 托盘图标可选颜色与色值，system 表示跟随任务栏深浅。
# 色值集中在这里：界面色块和托盘染色都从这里取，避免两处各存一份
TRAY_ICON_COLORS: dict[str, str] = {
    "black": "#1a1a1a",
    "white": "#ffffff",
    "green": "#3dbba4",
}
_TRAY_ICON_CHOICES = ("system",) + tuple(TRAY_ICON_COLORS)
# 自定义颜色的存储格式，与主题色一致（小写 #rrggbb）
_HEX_COLOR_RE = re.compile(r"#[0-9a-f]{6}")


def get_metrics_selection() -> dict[str, list[str]]:
    """did -> 卡片副标题要显示的属性名列表。

    三种状态靠键是否存在于返回值里区分：没有这个 did 表示从未设置
    过（用默认温湿度）；值是空列表表示用户明确选择了不外显；值非空
    则是具体的属性。调用方要用 in / is not None 判断，不能靠真假值。
    """
    raw = _read_raw().get("metrics_selection")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for did, props in raw.items():
        if isinstance(props, list):
            result[str(did)] = [str(p) for p in props
                                if isinstance(p, str) and p][:2]
    return result


def set_metrics_selection(did: str, props: list[str]) -> None:
    """写入某台设备的副标题属性。

    传空列表表示明确不外显，会保留一条空记录；要恢复默认请调用
    clear_metrics_selection 把键删掉，两者语义不同。
    """
    raw = _read_raw()
    current = raw.get("metrics_selection")
    if not isinstance(current, dict):
        current = {}
    current[did] = [str(p) for p in props if isinstance(p, str) and p][:2]
    raw["metrics_selection"] = current
    _write_raw(raw)


def clear_metrics_selection(did: str) -> None:
    """删掉这台设备的记录，回到默认温湿度。"""
    raw = _read_raw()
    current = raw.get("metrics_selection")
    if not isinstance(current, dict):
        return
    if current.pop(did, None) is not None:
        raw["metrics_selection"] = current
        _write_raw(raw)


def _normalize_tray_color(value: str) -> str:
    """把用户值规整成内部形式：预设名原样，十六进制统一小写。

    取色板返回的本来就是小写，这里再做一次是为了兼容手动编辑过
    设置文件、或别处传入大写色值的情况。不认识的值退回 system。
    """
    if value in _TRAY_ICON_CHOICES:
        return value
    lowered = value.lower()
    if _HEX_COLOR_RE.fullmatch(lowered):
        return lowered
    return "system"


def get_tray_icon_color() -> str:
    """托盘图标颜色：system / black / white / green，或自定义 #rrggbb。"""
    value = _read_raw().get("tray_icon_color")
    return _normalize_tray_color(value) if isinstance(value, str) else "system"


def set_tray_icon_color(value: str) -> None:
    """写入托盘图标颜色；不认识的值按 system 处理，避免存脏数据。"""
    raw = _read_raw()
    raw["tray_icon_color"] = (_normalize_tray_color(value)
                              if isinstance(value, str) else "system")
    _write_raw(raw)


def get_default_tab() -> str:
    """启动后默认打开的标签名；空串表示「全屋」。"""
    value = _read_raw().get("default_tab")
    return value if isinstance(value, str) else ""


def set_default_tab(name: str) -> None:
    raw = _read_raw()
    raw["default_tab"] = str(name or "")
    _write_raw(raw)


def get_minimize_to_tray() -> bool:
    """关闭窗口时是否最小化到托盘，默认 True。"""
    return bool(_read_raw().get("minimize_to_tray", True))


def set_minimize_to_tray(value: bool) -> None:
    raw = _read_raw()
    raw["minimize_to_tray"] = bool(value)
    _write_raw(raw)


def get_start_minimized() -> bool:
    """启动时是否以托盘方式静默启动，不唤出主界面。默认 False。"""
    return bool(_read_raw().get("start_minimized", False))


def set_start_minimized(value: bool) -> None:
    raw = _read_raw()
    raw["start_minimized"] = bool(value)
    _write_raw(raw)


def get_voice_fab_enabled() -> bool:
    """是否启用小爱同学悬浮对话按钮，默认 True。"""
    return bool(_read_raw().get("voice_fab_enabled", True))


def set_voice_fab_enabled(value: bool) -> None:
    raw = _read_raw()
    raw["voice_fab_enabled"] = bool(value)
    _write_raw(raw)


def get_default_speaker_did() -> str:
    """小爱指令默认输出音箱的 did；空串表示自动（第一个在线音箱）。

    设备被移除或离线时不强制：调用方找不到该 did 时回退自动选择。
    """
    value = _read_raw().get("default_speaker_did", "")
    return value if isinstance(value, str) else ""


def set_default_speaker_did(value: str) -> None:
    raw = _read_raw()
    raw["default_speaker_did"] = value if isinstance(value, str) else ""
    _write_raw(raw)


def get_tray_audio_speaker_did() -> str:
    """托盘音响栏正在控制的音箱 did；空串表示跟随（语音默认 → 第一个在线）。

    与 `default_speaker_did` 相互独立：改音响栏不会影响语音指令发往哪台，
    反之亦然。所选音箱离线时不强制回退写入，等它重新上线就自动回到它。
    """
    value = _read_raw().get("tray_audio_speaker_did", "")
    return value if isinstance(value, str) else ""


def set_tray_audio_speaker_did(value: str) -> None:
    raw = _read_raw()
    raw["tray_audio_speaker_did"] = value if isinstance(value, str) else ""
    _write_raw(raw)


def get_hide_offline_devices() -> bool:
    """是否隐藏离线设备，默认关闭。"""
    return bool(_read_raw().get("hide_offline_devices", False))


def set_hide_offline_devices(value: bool) -> None:
    raw = _read_raw()
    raw["hide_offline_devices"] = bool(value)
    _write_raw(raw)


def get_hide_no_func_devices() -> bool:
    """是否隐藏无可控制功能的设备（无 spec / spec 无属性），默认关闭。"""
    return bool(_read_raw().get("hide_no_func_devices", False))


def set_hide_no_func_devices(value: bool) -> None:
    raw = _read_raw()
    raw["hide_no_func_devices"] = bool(value)
    _write_raw(raw)


def get_device_order() -> list[str]:
    """主页卡片的手动顺序（did 列表）；空表示按默认规则排序。"""
    raw = _read_raw().get("device_order")
    if not isinstance(raw, list):
        return []
    return [str(did) for did in raw if isinstance(did, str) and did]


def set_device_order(dids: list[str]) -> None:
    raw = _read_raw()
    raw["device_order"] = [str(did) for did in dids]
    _write_raw(raw)


def get_scene_order() -> list[str]:
    """场景卡片的手动顺序（scene_id 列表）；空表示按名称排序。"""
    raw = _read_raw().get("scene_order")
    if not isinstance(raw, list):
        return []
    return [str(sid) for sid in raw if isinstance(sid, str) and sid]


def set_scene_order(scene_ids: list[str]) -> None:
    raw = _read_raw()
    raw["scene_order"] = [str(sid) for sid in scene_ids]
    _write_raw(raw)


def get_room_order() -> list[str]:
    """房间标签的自定义顺序；空列表表示按名称排序。"""
    raw = _read_raw().get("room_order")
    if not isinstance(raw, list):
        return []
    return [str(name) for name in raw if isinstance(name, str) and name]


def set_room_order(rooms: list[str]) -> None:
    raw = _read_raw()
    raw["room_order"] = [str(name) for name in rooms]
    _write_raw(raw)


def get_show_scene_tab() -> bool:
    """是否在主页选项卡最左侧显示「场景」入口，默认开启。"""
    return bool(_read_raw().get("show_scene_tab", True))


def set_show_scene_tab(value: bool) -> None:
    raw = _read_raw()
    raw["show_scene_tab"] = bool(value)
    _write_raw(raw)


def get_tray_columns() -> int:
    """托盘快捷窗口卡片列数，仅认 1 / 2，其余值回退双列。"""
    try:
        value = int(_read_raw().get("tray_columns", 2))
    except (TypeError, ValueError):
        return 2
    return value if value in (1, 2) else 2


def set_tray_columns(value: int) -> None:
    raw = _read_raw()
    raw["tray_columns"] = int(value) if int(value) in (1, 2) else 2
    _write_raw(raw)


def get_check_update_enabled() -> bool:
    """启动时是否自动检查 GitHub 新版本，默认开启。"""
    return bool(_read_raw().get("check_update_enabled", True))


def set_check_update_enabled(value: bool) -> None:
    raw = _read_raw()
    raw["check_update_enabled"] = bool(value)
    _write_raw(raw)


# 界面缩放档位（设置页下拉选项），范围 50%–200%
UI_SCALES = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
_UI_SCALE_MIN, _UI_SCALE_MAX = 0.5, 2.0


_UI_SCALE_DEFAULT = 1.0


def get_ui_scale() -> float:
    """界面缩放个人微调乘数（叠加在软件基准 1.25 之上），默认 100%。

    档位值直接用；手工改配置文件的任意值按范围收窄；非法值回退默认。
    """
    try:
        value = float(_read_raw().get("ui_scale", _UI_SCALE_DEFAULT))
    except (TypeError, ValueError):
        return _UI_SCALE_DEFAULT
    if value in UI_SCALES:
        return value
    return min(max(value, _UI_SCALE_MIN), _UI_SCALE_MAX) if value > 0 else _UI_SCALE_DEFAULT


def set_ui_scale(value: float) -> None:
    raw = _read_raw()
    value = float(value)
    # 超出 50%-200% 范围的值钳制到边界
    raw["ui_scale"] = min(max(value, _UI_SCALE_MIN), _UI_SCALE_MAX)
    _write_raw(raw)


def get_theme_mode() -> str:
    """主题配色设置：system（跟随系统）/ light / dark，默认 system。"""
    value = str(_read_raw().get("theme", "system"))
    return value if value in ("system", "light", "dark") else "system"


def set_theme_mode(value: str) -> None:
    raw = _read_raw()
    raw["theme"] = value if value in ("system", "light", "dark") else "system"
    _write_raw(raw)


def get_theme_accent() -> str:
    """主题色（#rrggbb）；空串表示使用默认米家绿。"""
    value = _read_raw().get("theme_accent", "")
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    # 只认 #rrggbb：手改坏的值按默认色处理，不让界面刷成透明/黑块
    return value if len(value) == 7 and value.startswith("#") else ""


def set_theme_accent(value: str) -> None:
    raw = _read_raw()
    text = (value or "").strip().lower()
    raw["theme_accent"] = text if len(text) == 7 and text.startswith("#") else ""
    _write_raw(raw)


# ---------- 开机自启动（Windows 注册表 HKCU Run） ----------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "MiHome-Windows"


def autostart_supported() -> bool:
    """仅构建版（Nuitka standalone exe）支持开机自启动。

    开发模式写入的是 venv 解释器 + run.py：任务管理器显示为无名
    "Python"、venv 一旦清理即失效、安全软件对解释器自启天然不信任
    （火绒报「无效/建议删除」即此情形），因此不提供。
    """
    from app import is_packaged

    return is_packaged() and sys.platform == "win32"


def _autostart_command() -> str:
    """注册表里写入的启动命令（仅打包形态调用）。

    不能用 sys.executable：Nuitka standalone 会在产物目录附带一个
    python.exe 且 sys.executable 指向它（已实测），写进注册表就是
    开机启动裸解释器——任务管理器显示 "Python"、安全软件报无效。
    sys.argv[0] 才是用户实际启动的 exe 路径（已实测）。
    """
    argv0 = Path(sys.argv[0]).resolve()
    return f'"{argv0}"'


def get_autostart() -> bool:
    """读取注册表实际状态（而非偏好文件），外部改动也能反映。

    目标文件已不存在的残留条目视为未开启（返回 False），开关重新
    打开时会用正确的命令覆写——否则无效条目会永远谎报「已开启」。
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_VALUE_NAME)
    except OSError:
        return False
    target = _command_target(value)
    return target is not None and target.exists()


def _command_target(value: str) -> Path | None:
    """从 Run 条目命令里解析目标可执行文件路径。"""
    value = value.strip()
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        if end == -1:
            return None
        inner = value[1:end]
        # 空引号命令解析为 Path(".")，目录 exists() 恒真，需显式拒绝
        return Path(inner) if inner else None
    bare = value.split(" ", 1)[0]
    return Path(bare) if bare else None


def set_autostart(value: bool) -> None:
    """开启自启动仅在构建版受支持；关闭（清理残留）任何形态都允许。"""
    if sys.platform != "win32":
        return
    if value and not autostart_supported():
        # 开发模式拒绝写入（调用方负责置灰开关并提示）
        return
    import winreg

    if value:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _RUN_VALUE_NAME, 0, winreg.REG_SZ,
                              _autostart_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_VALUE_NAME)
    except FileNotFoundError:
        pass
