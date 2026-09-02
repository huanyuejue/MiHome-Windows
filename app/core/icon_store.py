# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""设备图标持久化：model -> 图标 URL 映射与图片文件缓存。

图标 URL 来自米家 CDN（Expires 为 9999 年，可长期信任），启动时优先
读磁盘缓存避免重复调接口，只对缓存里没有的型号增量拉取。任何读取
异常都按无缓存处理，缓存只加速、不添堵。
"""

from pathlib import Path

from app.core import _json_store

_CACHE_VERSION = 1
_FILENAME = "device_icons.json"
_ICONS_SUBDIR = "icons"

# model 是云端型号标识（如 xiaomi.wifispeaker.x08c），文件名只保留
# 合法字符，防止异常数据把图片写出数据目录
_ILLEGAL_CHARS = set('/\\:*?"<>|')


def icons_dir() -> Path:
    return _json_store.data_dir() / _ICONS_SUBDIR


def icon_path(model: str) -> Path:
    safe = "".join(ch if ch not in _ILLEGAL_CHARS else "_" for ch in model)
    return icons_dir() / f"{safe}.png"


def load_urls() -> dict[str, str]:
    """读磁盘图标 URL 缓存；无效或损坏返回空 dict。"""
    raw = _json_store.read_json(_json_store.data_file(_FILENAME), {})
    if raw.get("version") != _CACHE_VERSION:
        return {}
    icons = raw.get("icons")
    if not isinstance(icons, dict):
        return {}
    return {str(k): str(v) for k, v in icons.items() if isinstance(v, str) and v}


def save_urls(urls: dict[str, str]) -> None:
    _json_store.write_json(
        _json_store.data_file(_FILENAME),
        {"version": _CACHE_VERSION, "icons": urls},
    )