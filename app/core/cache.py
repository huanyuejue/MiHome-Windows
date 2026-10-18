# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""设备列表本地缓存。

启动时优先读缓存渲染，省去每次打开都等一轮全量拉取；手动刷新成功
与退出时落盘。文件与其他配置同放在用户数据目录（见 _json_store），
任何读取异常都按无缓存处理走在线加载，缓存只加速、不添堵。
"""

import json

from app.core import _json_store
from app.core.models import DeviceInfo

# 版本 3：修正多键面板虚拟子设备的开关记忆。
# 旧版把"读不到状态"也记成 None（None 的语义是"确认无开关能力"），
# 这类设备会被轮询永久跳过——表现为子设备卡片永远没有开关按钮。
# 作废一次缓存，让所有设备的开关状态重新探测一遍。
#
# 版本 2：DeviceInfo 增加 spec_type 字段（上游 5.x 起按规格 URN 拉 spec）；
# 旧版缓存缺该字段会让"隐藏无功能设备"误判，直接作废重新拉取
_CACHE_VERSION = 3
_FILENAME = "devices_cache.json"


def load() -> tuple[list[DeviceInfo], dict[str, bool | None], dict[str, str]] | None:
    """返回 (设备列表, 开关记忆, 温湿度记忆)；无效一律返回 None。

    开关记忆沿用主窗口语义：True/False 为上次已知状态，None 表示
    已确认无开关能力。温湿度为 did->展示文案（如 28.0°C 60%）。
    结构不匹配直接丢弃，避免脏数据进 UI。
    """
    path = _json_store.data_file(_FILENAME)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        return None
    items = raw.get("devices")
    if not isinstance(items, list):
        return None
    try:
        devices = [DeviceInfo(**item) for item in items]
    except TypeError:
        return None
    known_raw = raw.get("known_power")
    known: dict[str, bool | None] = {}
    if isinstance(known_raw, dict):
        for key, value in known_raw.items():
            if value in (True, False, None):
                known[str(key)] = value
    metrics_raw = raw.get("metrics")
    metrics: dict[str, str] = {}
    if isinstance(metrics_raw, dict):
        for key, value in metrics_raw.items():
            if isinstance(value, str) and value:
                metrics[str(key)] = value
    return devices, known, metrics


def save(
    devices: list[DeviceInfo],
    known_power: dict[str, bool | None],
    metrics: dict[str, str | None] | None = None,
) -> None:
    # metrics 只存有值条目，避免空值膨胀文件
    clean_metrics: dict[str, str] = {}
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            if isinstance(value, str) and value:
                clean_metrics[str(key)] = value
    data = {
        "version": _CACHE_VERSION,
        "devices": [vars(device) for device in devices],
        "known_power": known_power,
        "metrics": clean_metrics,
    }
    # 写失败不影响主流程，下次刷新会再次尝试
    _json_store.write_json(_json_store.data_file(_FILENAME), data)
