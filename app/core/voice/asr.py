# SPDX-License-Identifier: GPL-3.0-or-later
"""语音识别封装（sherpa-onnx）。

- 识别引擎延迟导入：没装/没启用语音控制时不占用内存，也不影响启动速度；
- 输入统一为 float32 单声道 16kHz（麦克风采集与测试音频都转成这个格式）；
- 模型加载较慢（约 1~2 秒），按模型 key 缓存实例，第二次说话不再等待。
"""

import logging
import threading

import numpy as np

from . import models

logger = logging.getLogger(__name__)

_cache: dict[str, "Transcriber"] = {}
_lock = threading.Lock()


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """16bit PCM 小端字节 → float32 [-1, 1]。"""
    if not data:
        return np.zeros(0, dtype=np.float32)
    raw = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return raw / 32768.0


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    """线性重采样到目标采样率。

    麦克风不一定支持 16k 单声道（很多设备只给 44.1k/48k），识别器却只吃 16k；
    不重采样直接喂进去，识别结果会是乱码级别。
    """
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if src_rate == dst_rate or x.size == 0:
        return x
    n_dst = max(1, int(round(x.size * dst_rate / src_rate)))
    x_old = np.linspace(0.0, 1.0, num=x.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(x_new, x_old, x).astype(np.float32)


def wav_bytes_to_float32(data: bytes) -> tuple[np.ndarray, int]:
    """WAV 字节 → (float32 采样, 采样率)；给测试与文件转写用。"""
    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"只支持 16bit PCM，当前 {width * 8}bit")
    samples = pcm16_to_float32(frames)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


class Transcriber:
    """一个已加载的识别器；线程内串行调用。"""

    def __init__(self, model_key: str):
        model = models.MODELS.get(model_key)
        if model is None:
            raise ValueError(f"未知模型: {model_key}")
        if not models.is_ready(model_key):
            raise RuntimeError("识别模型尚未下载完整，请先在设置里下载")

        import sherpa_onnx  # 延迟导入：未启用语音时完全不加载

        base = model.dir()
        model_path = str(base / "model.int8.onnx")
        tokens = str(base / "tokens.txt")
        if model.engine == "sense_voice":
            self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model_path, tokens=tokens, num_threads=2,
                language="zh", use_itn=True)
        elif model.engine == "paraformer":
            self._rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=model_path, tokens=tokens, num_threads=2)
        else:
            raise ValueError(f"不支持的识别引擎: {model.engine}")
        self.model_key = model_key
        logger.info("语音识别器已加载：%s（%s）", model_key, model.title)

    def transcribe(self, samples: np.ndarray, sample_rate: int = 16000) -> str:
        """识别一段语音，返回文本（识别不到时为空串）。"""
        if samples is None or len(samples) == 0:
            return ""
        if sample_rate != 16000:
            raise ValueError(f"只支持 16kHz，当前 {sample_rate}")
        stream = self._rec.create_stream()
        stream.accept_waveform(16000, samples)
        self._rec.decode_stream(stream)
        text = (stream.result.text or "").strip()
        logger.debug("识别结果：%r", text)
        return text


def get_transcriber(model_key: str) -> Transcriber:
    """按模型 key 取（并缓存）识别器。"""
    with _lock:
        cached = _cache.get(model_key)
        if cached is not None:
            return cached
        transcriber = Transcriber(model_key)
        _cache[model_key] = transcriber
        return transcriber


def clear_cache() -> None:
    """删除/切换模型后调用，释放已加载的识别器。"""
    with _lock:
        _cache.clear()


def has_speech(samples, min_seconds: float = 0.30, min_rms: float = 0.008) -> bool:
    """粗判这段录音里是否有人说话。

    必要性：SenseVoice 对纯静音/噪声会"编"出「没。」「我。」这类短词，
    不拦的话用户会遇到"明明没说话却执行了"。这里只做时长的能量门限，
    宁可多问一句，也不误执行。
    """
    if samples is None or len(samples) == 0:
        return False
    if len(samples) / 16000.0 < min_seconds:
        return False
    rms = float((samples.astype("float64") ** 2).mean() ** 0.5)
    return rms >= min_rms