# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""给上游 HTTP 调用补一个默认超时。

上游 mijiaAPI 里有几处 `requests.get(...)` 没有 timeout（登录取二维码、
刷新 token、设备列表都在其中）。requests 在不给 timeout 时会一直等下去，
在网络被墙、代理失效、IPv6 路由不通的机器上表现为**界面无限转圈**：
用户看到的是"一直在加载"，日志里一片安静，既没法判断也没法重试。

这里在会话层注入默认超时，只影响"没显式指定 timeout"的调用：

    install_default_timeout((10, 30))   # 连接 10s，读 30s

显式传了 timeout 的调用保持原样 —— 扫码长轮询自己传了 120s，不能被改短。
"""

import logging

import requests
from requests.sessions import Session

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = (10.0, 30.0)

_installed = False


def install_default_timeout(timeout: tuple[float, float] = DEFAULT_TIMEOUT) -> None:
    """给 requests 会话注入默认超时（幂等）。"""
    global _installed
    if _installed:
        return
    original_request = Session.request

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original_request(self, method, url, **kwargs)

    Session.request = request
    _installed = True
    logger.debug("已注入 requests 默认超时 %s", timeout)
