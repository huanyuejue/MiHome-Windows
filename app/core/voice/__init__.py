# SPDX-License-Identifier: GPL-3.0-or-later
"""语音控制子系统。

分层（与界面解耦，便于单测与替换）：

    models.py   模型清单、下载、校验、本地缓存目录
    asr.py      语音识别封装（sherpa-onnx），输入 float32 单声道 16k
    parser.py   「打开客厅灯」→ 结构化指令（设备/场景/批量）
    hotkey.py   Windows 全局热键（按住说话，松开结束）

设计要点见 docs/语音控制-设计.md：模型不进安装包，按需下载到用户数据目录；
指令空间是封闭的（开关 + 设备/场景 + 房间/批量），因此走规则解析而不是大模型。
"""
