# SPDX-License-Identifier: GPL-3.0-or-later
"""语音控制运行时：全局热键 → 按住录音 → 本机识别 → 解析 → 交给主窗口执行。

分工：
    本模块只管"听"和"听懂"，不直接操作设备/场景（执行由主窗口完成），
    这样语音只是又一个输入入口，控制逻辑不会重复实现一份。
"""

import logging
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtWidgets import QLabel, QWidget

import numpy as np

from app.core import settings_store
from app.core.voice import asr, models, parser
from app.core.voice.hotkey import GlobalHotkey

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 16000
# 单次录音上限：再长识别也会慢到不可用，而且迟迟不松手时麦克风一直被占着。
# 到点自动收尾并照常识别，用户不会觉得"按了没反应"
_MAX_RECORD_SECONDS = 25

# 语音浮条三种状态共用一套外观，只换边框/文字色
_PILL_STYLE = (
    "QLabel { background: rgba(20,20,24,0.92); color: #f2f3f5;"
    " border: 1px solid rgba(255,255,255,0.18); border-radius: 16px;"
    " padding: 8px 18px; font-size: 10pt; }")
_PILL_STYLE_OK = (
    "QLabel { background: rgba(20,20,24,0.92); color: #f2f3f5;"
    " border: 1px solid rgba(120,220,160,0.55); border-radius: 16px;"
    " padding: 8px 18px; font-size: 10pt; }")
_PILL_STYLE_ERROR = (
    "QLabel { background: rgba(20,20,24,0.92); color: #ffbdbd;"
    " border: 1px solid rgba(255,120,120,0.55); border-radius: 16px;"
    " padding: 8px 18px; font-size: 10pt; }")


class ListeningOverlay(QWidget):
    """按住热键时的小提示条：只在录音期间出现，不抢焦点、不挡鼠标。"""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label = QLabel(self)
        self._label.setStyleSheet(_PILL_STYLE)
        self._label.setText("● 正在听…")
        self._label.adjustSize()
        self.resize(self._label.sizeHint())
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._tick)
        # 结果反馈（已打开/已关闭/失败）复用同一个药丸：语音是"说完看一眼"
        # 的交互，提示必须出现在用户正在看的位置，不能换到右下角
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self.hide_listening)

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self._label.setText("● 正在听" + "…" * 0 + "·" * self._dots + " " * (3 - self._dots))

    def show_listening(self, text: str = "") -> None:
        self._notice_timer.stop()
        self._label.setStyleSheet(_PILL_STYLE)
        self._label.setText(text or "● 正在听…")
        self._place_and_show()
        self._timer.start()

    def show_notice(self, text: str, is_error: bool = False) -> None:
        """结果提示：占同一个药丸位置，2.2s 后自动消失。"""
        self._timer.stop()
        self._notice_timer.stop()
        mark = "✗ " if is_error else "✓ "
        self._label.setStyleSheet(_PILL_STYLE_ERROR if is_error else _PILL_STYLE_OK)
        self._label.setText(mark + text)
        self._place_and_show()
        self._notice_timer.start(2200)

    def _place_and_show(self) -> None:
        self._label.adjustSize()
        self.resize(self._label.sizeHint())
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.bottom() - self.height() - 96)
        self.show()
        self.raise_()

    def update_text(self, text: str) -> None:
        self._label.setText(text)
        self._label.adjustSize()
        self.resize(self._label.sizeHint())

    def hide_listening(self) -> None:
        self._timer.stop()
        self._notice_timer.stop()
        self.hide()


class VoiceController(QObject):
    """热键 + 录音 + 识别 + 解析；识别结果以 intent 信号抛出。"""

    intent_ready = Signal(object)          # parser.Intent
    status = Signal(str, bool)             # (文案, 是否错误)

    def __init__(self, jobs, devices_provider, scenes_provider,
                 known_power_provider, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._devices_provider = devices_provider
        self._scenes_provider = scenes_provider
        self._known_power_provider = known_power_provider
        self._hotkey = GlobalHotkey(self)
        self._hotkey.pressed.connect(self._on_pressed)
        self._hotkey.released.connect(self._on_released)
        self._overlay = ListeningOverlay()
        self._source: QAudioSource | None = None
        self._io = None
        self._chunks: list[bytes] = []
        self._busy = False                 # 识别中：忽略新的按键，避免叠任务
        self._recording = False            # 是否真的在录（没录时收到"松开"要忽略）
        self._dev_fmt = None               # 实际采集格式（可能不是 16k 单声道）
        self._registered_spec = ""
        # 识别不进共享串行队列（jobs）：那个队列与设备轮询/网络任务共用，
        # 队头一旦有慢任务或卡死的请求，识别回调永远不回来，_busy 永久
        # 卡 True → 表现为"热键只能唤起一次/被其他应用用过麦克风后就唤不起"。
        # 改为独立线程识别，结果用 singleShot(0, self) 折回主线程
        self._busy_since: float = 0.0
        # 识别代际号：每次按下派发识别时 +1 并闭包捕获；看门狗超时复位后，
        # 迟到的旧识别结果必须因"代际不符"被丢弃——只靠 _busy 标志不够，
        # 超时后用户再按键会重新置位 _busy，旧结果会冒充新结果被执行
        self._gen: int = 0
        self._pending_notice: tuple[str, bool] | None = None   # busy 期间挂起的提示
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._watchdog_tick)
        # 录音计时：松手事件正常时会先停掉；丢了也不会让麦克风无限开着
        self._rec_started: float = 0.0
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._recording_tick)

    # ---- 生命周期 ----

    def notice(self, text: str, is_error: bool = False) -> None:
        """执行结果提示：显示在「正在听」同一个药丸位置。

        录音/识别进行中不抢占（别把"正在听…"盖掉），但不能静默丢弃——
        用户执行了什么必须能看见：挂起，busy 结束后展示最后一条。
        """
        if self._recording or self._busy:
            self._pending_notice = (text, is_error)   # 保留最新一条
            return
        self._pending_notice = None
        self._overlay.show_notice(text, is_error)

    def _notify(self, message: str, is_error: bool = True) -> None:
        self._overlay.show_notice(message, is_error)
        self.status.emit(message, is_error)

    def start(self) -> bool:
        spec = settings_store.get_voice_hotkey()
        ok = self._hotkey.register(spec)
        if ok:
            self._registered_spec = spec
            logger.info("语音控制已启用，热键 %s", spec)
        else:
            # 分两种情况提示，不再一律甩"被其他软件占用"：
            # 组合键当前其实是空的 → 是注册本身被拒（常见于本程序另一个
            # 实例还持有该键），提示重启；确实被占用才让用户换键
            from app.core.voice import hotkey as vk_hotkey
            if vk_hotkey.probe_available(spec):
                self._notify(f"热键 {spec} 注册失败，若已有本程序在运行请先退出它再试")
            else:
                self._notify(f"热键 {spec} 已被其他软件占用，请在设置里换一个")
        return ok

    def stop(self) -> None:
        self._hotkey.unregister()
        self._stop_recording()
        self._overlay.hide_listening()
        self._registered_spec = ""

    # ---- 录音 ----

    def _on_pressed(self) -> None:
        if self._busy:
            return
        if self._recording:
            # 上一次录音没等到"松开"（键态错乱 / 系统休眠 / 消息被吞）：
            # 绝不能就此让热键永久失效、麦克风一直开着——丢弃上一段重来
            logger.warning("上一次录音未正常结束，已丢弃并重新开始")
            self._stop_recording()
            self._overlay.hide_listening()
        if not models.is_ready(settings_store.get_voice_model()):
            self._notify("请先下载语音识别模型（设置 → 语音控制 → 语音识别模型）")
            return
        try:
            self._start_recording()
        except Exception as exc:
            logger.exception("启动录音失败")
            self._notify(f"麦克风不可用：{exc}")
            return
        self._overlay.show_listening()

    def _start_recording(self) -> None:
        fmt = QAudioFormat()
        fmt.setSampleRate(_SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioInput()
        wanted = settings_store.get_voice_input_device()
        if wanted:
            for cand in QMediaDevices.audioInputs():
                if cand.description() == wanted:
                    device = cand
                    break
        if device is None or device.isNull():
            raise RuntimeError("没有找到可用的麦克风")
        # 设备不一定支持 16k 单声道：不支持就用它偏好的格式录，事后重采样
        if not device.isFormatSupported(fmt):
            preferred = device.preferredFormat()
            logger.info("麦克风不支持 16k 单声道，改用 %s Hz / %s 声道",
                        preferred.sampleRate(), preferred.channelCount())
            fmt = preferred
        self._dev_fmt = fmt
        self._source = QAudioSource(device, fmt)
        self._chunks = []
        self._io = self._source.start()
        if self._io is None:
            self._source.deleteLater()
            self._source = None
            raise RuntimeError("麦克风被占用或格式不支持")
        self._recording = True
        self._rec_started = time.monotonic()
        self._rec_timer.start()
        self._io.readyRead.connect(self._on_audio_ready)

    def _recording_tick(self) -> None:
        """录音时长上限：按着不放时自动收尾并识别。"""
        if not self._recording:
            self._rec_timer.stop()
            return
        if time.monotonic() - self._rec_started >= _MAX_RECORD_SECONDS:
            logger.info("录音达到 %d 秒上限，自动结束", _MAX_RECORD_SECONDS)
            self._on_released()

    def _on_audio_ready(self) -> None:
        if self._io is None:
            return
        data = bytes(self._io.readAll().data())
        if data:
            self._chunks.append(data)

    def _stop_recording(self) -> bytes:
        self._recording = False
        self._rec_timer.stop()
        if self._source is not None:
            try:
                if self._io is not None:
                    self._chunks.append(bytes(self._io.readAll().data()))
                self._source.stop()
            except Exception:
                logger.exception("停止录音失败")
            self._source.deleteLater()
            self._source = None
            self._io = None
        data = b"".join(self._chunks)
        self._chunks = []
        return data

    @staticmethod
    def _decode_pcm(data: bytes, fmt) -> np.ndarray:
        """按实际采集格式转 float32 单声道（-1~1）。"""
        if not data or fmt is None:
            return np.zeros(0, dtype=np.float32)
        kind = fmt.sampleFormat()
        if kind == QAudioFormat.SampleFormat.Int16:
            arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        elif kind == QAudioFormat.SampleFormat.Int32:
            arr = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
        elif kind == QAudioFormat.SampleFormat.Float:
            arr = np.frombuffer(data, dtype="<f4").astype(np.float32)
        elif kind == QAudioFormat.SampleFormat.UInt8:
            arr = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            raise RuntimeError(f"不支持的录音采样格式: {kind}")
        channels = max(1, int(fmt.channelCount()))
        if channels > 1:
            arr = arr.reshape(-1, channels).mean(axis=1)
        return arr.astype(np.float32)

    def _on_released(self) -> None:
        # 没在录的时候收到"松开"（模型未就绪/识别中按了键）→ 什么都不做，
        # 否则会给用户弹一句莫名其妙的"没有听清"
        if self._busy or not self._recording:
            return
        fmt = self._dev_fmt
        data = self._stop_recording()
        self._overlay.update_text("识别中…")
        self._busy = True
        try:
            samples = self._decode_pcm(data, fmt)
            if fmt is not None:
                samples = asr.resample(samples, int(fmt.sampleRate()), _SAMPLE_RATE)
        except Exception as exc:
            self._finish(None, f"录音处理失败：{exc}")
            return
        if not asr.has_speech(samples):
            self._finish(None, "没有听清，请按住热键说一句话")
            return
        # 识别走独立线程 + 主线程折回；绝不能进共享串行队列
        # （与设备轮询共用，队头慢任务会卡死识别 → 热键"只能用一次"）
        self._gen += 1
        gen = self._gen                     # 本轮识别的代际，闭包捕获
        self._busy_since = time.monotonic()
        self._watchdog.start()

        def _work():
            try:
                return self._transcribe(samples), None
            except Exception as exc:      # noqa: BLE001 - 交给 _on_text 展示
                return None, exc

        def _runner():
            result, exc = _work()
            QTimer.singleShot(0, self,
                              lambda: self._on_transcribe_done(gen, result, exc))

        threading.Thread(target=_runner, daemon=True,
                         name="voice-asr").start()

    def _on_transcribe_done(self, gen: int, text, exc) -> None:
        if gen != self._gen:
            # 本轮已被作废（看门狗超时复位，或期间开始了新一轮识别）：
            # 迟到的旧结果绝不能再生效，否则用户刚听到"识别超时"，
            # 20 秒后指令却被执行了
            logger.info("忽略迟到的识别结果（代际 %d != %d）：%r",
                        gen, self._gen, text)
            return
        if exc is not None:
            self._finish(None, f"语音识别失败：{exc}")
            return
        self._on_text(text or "")

    def _watchdog_tick(self) -> None:
        """识别卡死看门狗：独立线程理论上不受共享队列拖累，但仍可能
        被模型加载等长操作卡住；超时强制复位状态，热键永远可用。"""
        if not self._busy:
            self._watchdog.stop()
            return
        if time.monotonic() - self._busy_since > 20.0:
            logger.warning("语音识别超过 20s 未返回，强制复位状态")
            self._finish(None, "语音识别超时，请再试一次")

    @staticmethod
    def _transcribe(samples):
        model_key = settings_store.get_voice_model()
        transcriber = asr.get_transcriber(model_key)
        return transcriber.transcribe(samples, _SAMPLE_RATE)

    def _on_text(self, text: str) -> None:
        try:
            intent = parser.parse(
                text,
                self._devices_provider(),
                self._scenes_provider(),
                self._known_power_provider(),
            )
            logger.info("语音指令：%r → %s（%s）", text, intent.kind, intent.message)
            self.intent_ready.emit(intent)
        except Exception:
            logger.exception("语音指令解析失败")
            self._notify("语音指令解析失败，请换个说法再说一次")
        finally:
            # 无论成功失败都必须复位，否则一次异常会让后续按键全部失效
            self._watchdog.stop()
            self._busy = False
            self._recording = False
            self._overlay.hide_listening()
            # busy 期间挂起的结果提示（如执行回执）复位后补展
            if self._pending_notice is not None:
                text_, err_ = self._pending_notice
                self._pending_notice = None
                self.notice(text_, err_)

    def _finish(self, _value, message: str) -> None:
        self._watchdog.stop()
        self._rec_timer.stop()
        self._busy = False
        self._recording = False
        self._notify(message)
