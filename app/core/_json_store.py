# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""JSON 持久化公共基础：数据目录解析、旧位置迁移、原子写入。

打包形态曾把配置写在 exe 同目录，装进 Program Files 等受保护位置后
写入失败且被静默吞掉，表现为设置每次启动重置；现统一写入
%LOCALAPPDATA%\\MiHome-Windows\\，并把旧位置的已有文件一次性迁移过来。
开发形态仍为仓库根目录（文件均已 gitignore）。
"""

import json
import logging
import os
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

_APP_DIR_NAME = "MiHome-Windows"
# 旧版写在 exe 同目录的全部数据文件，发现即迁移
_KNOWN_FILES = ("settings.json", "tray.json", "workbench.json", "devices_cache.json")
_migrated = False


def data_dir() -> Path:
    """配置与缓存的存放目录。

    优先级：环境变量 `MIHOME_DATA_DIR` > 构建版数据目录 > 项目根。
    环境变量是给**测试**用的：测试会反复写空配置，若落到真实数据目录
    就会把用户的挂件设备、定时规则、托盘配置全部清空（曾真实发生过）。
    """
    from app import is_packaged

    override = os.environ.get("MIHOME_DATA_DIR")
    if override:
        return Path(override)
    if is_packaged():
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / _APP_DIR_NAME
    return Path(__file__).resolve().parents[2]


def data_file(filename: str) -> Path:
    """数据文件完整路径，首次访问时尝试从旧的 exe 同目录迁移。"""
    global _migrated
    if getattr(sys, "frozen", False) and not _migrated:
        _migrated = True
        _migrate_legacy_files()
    return data_dir() / filename


def _migrate_legacy_files() -> None:
    legacy_dir = Path(sys.executable).resolve().parent
    target_dir = data_dir()
    if legacy_dir == target_dir:
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in _KNOWN_FILES:
            legacy = legacy_dir / name
            target = target_dir / name
            if legacy.exists() and not target.exists():
                # copyfile 保留旧文件，新位置写坏时下次启动还能再迁
                target.write_bytes(legacy.read_bytes())
    except OSError:
        pass


def read_json(path: Path, default: dict) -> dict:
    """读取 JSON dict；文件缺失、损坏或类型不对时返回 default。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    if not isinstance(raw, dict):
        return default
    return raw


def write_json(path: Path, data: dict) -> None:
    """原子写入：先写同目录临时文件再 os.replace，进程被杀不会留半截文件。

    失败不再静默：写不进去意味着用户刚做的设置会凭空消失（曾表现为
    "每次启动都丢配置"），必须留下日志线索。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _logger.warning("写入配置失败 %s: %s（本次改动不会保存）", path, exc)
