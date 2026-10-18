# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""设备定时开关规则的本地持久化。

与 tray.json / board.json 同目录，单独文件 schedules.json。

一条规则 = 设备 + 时刻 + 动作，**每天**在该时刻执行一次：

    {
      "id":        规则唯一标识（删除/停用时定位用）
      "did":       设备 id
      "time":      "HH:MM"（24 小时制，零填充）
      "action":    "on" | "off"
      "enabled":   是否启用（与删除分开：临时停用不必删掉规则）
      "last_fired": "YYYY-MM-DD"，最后成功执行的日期，
                   用于保证同一天只执行一次（应用重启也不会重复触发）
    }

同一台设备可以有多条规则（例如 7:00 开、23:00 关），彼此独立。
设备被移除后规则保留（显示时会标出来），避免误删用户的配置。
"""

import uuid
from datetime import datetime

from app.core import _json_store

_VERSION = 1
_FILENAME = "schedules.json"

_ACTIONS = ("on", "off")


def _empty() -> dict:
    return {"version": _VERSION, "rules": []}


def normalize_time(value) -> str | None:
    """校验并规范化 "H:MM" / "HH:MM"，非法返回 None。

    界面层直接用这个函数做输入校验，确保"7:5"这类随手输入也能识别，
    并且落盘前一定是零填充的 24 小时制。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if ":" not in text:
        return None
    hh, _, mm = text.partition(":")
    try:
        hour, minute = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _normalize(rule: dict) -> dict | None:
    """把磁盘上的规则收敛成合法内部结构；无法修复的丢弃。"""
    if not isinstance(rule, dict):
        return None
    did = rule.get("did")
    if not isinstance(did, str) or not did:
        return None
    time_text = normalize_time(rule.get("time"))
    if time_text is None:
        return None
    action = rule.get("action")
    if action not in _ACTIONS:
        return None
    rid = rule.get("id")
    if not isinstance(rid, str) or not rid:
        rid = uuid.uuid4().hex[:12]
    last_fired = rule.get("last_fired")
    if not isinstance(last_fired, str):
        last_fired = ""
    return {
        "id": rid,
        "did": did,
        "time": time_text,
        "action": action,
        "enabled": bool(rule.get("enabled", True)),
        "last_fired": last_fired,
    }


def _read_raw() -> dict:
    raw = _json_store.read_json(_json_store.data_file(_FILENAME), _empty())
    if raw.get("version") != _VERSION:
        return _empty()
    rules = raw.get("rules")
    if not isinstance(rules, list):
        return _empty()
    cleaned = [r for r in (_normalize(item) for item in rules) if r is not None]
    raw["rules"] = cleaned
    return raw


def _write_raw(raw: dict) -> None:
    _json_store.write_json(_json_store.data_file(_FILENAME), raw)


def load() -> list[dict]:
    """全部规则，按时间升序（同时间按设备排在原顺序）。"""
    rules = list(_read_raw().get("rules", []))
    rules.sort(key=lambda r: (r["time"], r["did"]))
    return rules


def save(rules: list[dict]) -> None:
    raw = _read_raw()
    cleaned = [r for r in (_normalize(item) for item in rules) if r is not None]
    # 去重：同一 id 只保留第一条，避免界面重复操作写坏文件
    seen: set[str] = set()
    uniq: list[dict] = []
    for rule in cleaned:
        if rule["id"] in seen:
            continue
        seen.add(rule["id"])
        uniq.append(rule)
    raw["rules"] = uniq
    _write_raw(raw)


def add(did: str, time: str, action: str, *, now: datetime | None = None) -> dict | None:
    """新增一条规则并返回它；时刻/动作非法时返回 None。

    `now` 用于"今天这个时刻已过"的处理：新建规则如果落在今天已过去的
    时间点，把 last_fired 直接记为今天，避免刚建出来就被判成"今天已错过"
    或启动时立刻触发一次。从明天开始生效，符合直觉。
    """
    time_text = normalize_time(time)
    if time_text is None or action not in _ACTIONS or not did:
        return None
    moment = now or datetime.now()
    rule = {
        "id": uuid.uuid4().hex[:12],
        "did": str(did),
        "time": time_text,
        "action": action,
        "enabled": True,
        "last_fired": "",
    }
    if time_text <= moment.strftime("%H:%M"):
        rule["last_fired"] = moment.strftime("%Y-%m-%d")
    rules = _read_raw().get("rules", [])
    rules.append(rule)
    save(rules)
    return rule


def remove(rule_id: str) -> bool:
    rules = _read_raw().get("rules", [])
    kept = [r for r in rules if r["id"] != rule_id]
    if len(kept) == len(rules):
        return False
    save(kept)
    return True


def set_enabled(rule_id: str, enabled: bool) -> bool:
    rules = _read_raw().get("rules", [])
    hit = False
    for rule in rules:
        if rule["id"] == rule_id:
            rule["enabled"] = bool(enabled)
            hit = True
            break
    if hit:
        save(rules)
    return hit


def mark_fired(rule_id: str, date_text: str) -> None:
    """记录该规则今天已成功执行；同一天不会再次触发。"""
    rules = _read_raw().get("rules", [])
    hit = False
    for rule in rules:
        if rule["id"] == rule_id:
            rule["last_fired"] = str(date_text)
            hit = True
            break
    if hit:
        save(rules)


def rules_for(did: str) -> list[dict]:
    """某台设备的规则（设备详情页/管理界面按设备筛选用）。"""
    return [r for r in load() if r["did"] == did]
