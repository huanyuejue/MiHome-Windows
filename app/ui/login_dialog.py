# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""扫码登录对话框。

上游的登录二维码只往终端打印 ASCII 字符，图形界面无法直接复用，
因此这里用适配层拆出的两步流程：先取 loginUrl 渲染成图片展示，
再在后台长轮询等待扫码结果。
"""

import io
import logging
import time

import qrcode
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.ui.si_theme import SiColors

logger = logging.getLogger(__name__)

# 取二维码的超时上限：正常 1~5 秒，慢网十几秒。超过这个时间仍未拿到数据，
# 说明网络被墙/代理失效/链路异常，必须给用户一句话和重试按钮，
# 而不是让「正在获取登录二维码…」一直转下去（曾有版本在这里无限转圈，
# 用户完全无法判断是网慢还是卡死）。
_QR_TIMEOUT_MS = 40_000


class LoginDialog(QDialog):
    """模态登录窗：三态切换 获取中 -> 待扫码 -> 失败可重试。

    凭据仍有效时上游直接返回免登录结论，本窗口立即关闭。
    """

    def __init__(self, service, jobs, parent=None):
        super().__init__(parent)
        self._service = service
        self._jobs = jobs
        # 登录数据要在 begin 与 wait 两次调用间传递
        self._login_data = None
        # 登录轮次：串行队列无法取消任务，「重新获取」后旧长轮询
        # （最长阻塞两分钟）完成时必须被丢弃，否则会用过期凭据误关窗口
        self._generation = 0
        # 本轮开始时刻（monotonic），用于把"取二维码花了多久"写进日志
        self._started_at = 0.0

        self.setWindowTitle("登录米家账号")
        self.setModal(True)
        self.setMinimumSize(320, 380)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignHCenter)

        self._qr_label = QLabel(alignment=Qt.AlignCenter)
        self._qr_label.setMinimumSize(260, 260)
        layout.addWidget(self._qr_label)

        self._status_label = QLabel(alignment=Qt.AlignCenter)
        layout.addWidget(self._status_label)

        self._retry_btn = QPushButton("重新获取二维码")
        self._retry_btn.clicked.connect(self._begin_login)
        self._retry_btn.hide()
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._retry_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 取二维码的看门狗：超时就把"一直转圈"换成明确提示 + 重试
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(_QR_TIMEOUT_MS)
        self._watchdog.timeout.connect(self._on_qr_timeout)

    def exec_and_wait(self) -> int:
        """启动登录流程并阻塞至完成或用户关闭窗口。"""
        self._begin_login()
        return self.exec()

    # ---------- 流程编排 ----------

    def _begin_login(self) -> None:
        self._generation += 1
        generation = self._generation
        self._retry_btn.hide()
        self._set_status("正在获取登录二维码…", error=False)
        self._qr_label.clear()
        self._started_at = time.monotonic()
        logger.info("登录: 开始获取二维码 (第 %d 轮)", generation)
        self._watchdog.start()
        self._jobs.submit(
            self._service.qr_login_begin,
            on_success=lambda data: self._on_qr_data(data, generation),
            on_error=lambda e: self._on_failed(e, generation),
        )

    def _on_qr_timeout(self) -> None:
        """看门狗：这么久还没拿到二维码，说明网络侧出了问题。"""
        logger.warning("登录: %d 秒内未取到二维码，判定网络异常", _QR_TIMEOUT_MS // 1000)
        self._set_status(
            f"获取二维码超时（已等 {_QR_TIMEOUT_MS // 1000} 秒）。"
            "请检查网络是否正常、代理是否可用，然后点击下方按钮重试。",
            error=True,
        )
        self._retry_btn.show()

    def _on_qr_data(self, data: dict | None, generation: int) -> None:
        elapsed = time.monotonic() - self._started_at
        self._watchdog.stop()
        logger.info("登录: 第 %d 轮取到登录数据（%.1fs）", generation, elapsed)
        if generation != self._generation or not self.isVisible():
            logger.info(
                "登录: 第 %d 轮结果已过期被丢弃（当前轮 %d，窗口可见=%s）",
                generation, self._generation, self.isVisible())
            return
        if data is None:
            # 本地凭据自动刷新成功，无需扫码
            logger.info("登录: 本地凭据刷新成功，无需扫码")
            self.accept()
            return
        self._login_data = data
        try:
            self._show_qr_image(data["loginUrl"])
        except Exception as exc:
            # 渲染失败必须显性化：否则界面停在「正在获取二维码…」，
            # 用户只会看到一直转圈，既不知道原因也没法重试
            logger.exception("登录: 二维码渲染失败")
            self._set_status(f"二维码生成失败：{exc}（可点击下方按钮重试）", error=True)
            self._retry_btn.show()
            return
        logger.info("登录: 二维码已显示（%.1fs）", time.monotonic() - self._started_at)
        self._set_status("请使用米家 APP 扫描二维码", error=False)
        self._jobs.submit(
            lambda: self._service.qr_login_wait(self._login_data),
            on_success=lambda _: self._on_login_success(generation),
            on_error=lambda e: self._on_failed(e, generation),
        )

    def _on_login_success(self, generation: int) -> None:
        if generation != self._generation or not self.isVisible():
            return
        logger.info("登录: 扫码成功，凭据已保存")
        self.accept()

    def _on_failed(self, error: Exception, generation: int) -> None:
        self._watchdog.stop()
        logger.warning("登录: 第 %d 轮失败: %s", generation, error)
        if generation != self._generation or not self.isVisible():
            return
        self._set_status(str(error), error=True)
        self._retry_btn.show()

    # ---------- 绘制 ----------

    def _show_qr_image(self, login_url: str) -> None:
        qr = qrcode.QRCode(border=2, box_size=6)
        qr.add_data(login_url)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue())
        self._qr_label.setPixmap(
            pixmap.scaled(self._qr_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _set_status(self, text: str, error: bool) -> None:
        self._status_label.setText(text)
        color = "#c0392b" if error else f"{SiColors.TEXT_SECONDARY}"
        self._status_label.setStyleSheet(f"color: {color};")


