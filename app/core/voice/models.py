# SPDX-License-Identifier: GPL-3.0-or-later
"""识别模型清单与按需下载。

模型不进安装包（与 DeepSeek Harness 的做法一致）：用户开启语音控制时才下载，
存放在用户数据目录 `%LOCALAPPDATA%\\MiHome-Windows\\models\\<key>\\`，
删除模型即释放空间。

两档模型（可在设置里切换）：

    sensevoice  SenseVoice（中英日韩粤）int8，约 228MB，准确率高、带标点
    paraformer  Paraformer 中文小模型 int8，体积小得多，短指令够用

下载源：先试国内镜像 hf-mirror.com，再回退 huggingface.co（DSH 同款策略）。
"""

import logging
import math
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import requests

from app.core import _json_store

logger = logging.getLogger(__name__)

# 下载源：国内镜像优先（huggingface.co 在国内经常连不上）
_SOURCES = ("https://hf-mirror.com", "https://huggingface.co")
# 探测源的超时（秒）：太短会误判，太长会让"开始下载"按钮迟迟不响应
_PROBE_TIMEOUT = (4.0, 8.0)
_CHUNK = 1024 * 256

# 并发分段下载：实测 hf-mirror 单连接约 0.06 MB/s、8 路约 0.32 MB/s，
# 但探测时连续请求会触发 429 限流。**取 6 路**（用户拍板）：4 路偏小，
# 8 路容易顶到限流阈值；遇 429 时所有线程一起退避，不硬顶
_CONCURRENCY = 6
_SEG_SIZE = 8 * 1024 * 1024          # 每段 8MB：太小请求多容易限流，太大并行度不足
_MAX_RETRIES = 4                     # 单段的尝试次数（跨源合计）
_BACKOFF = 1.5                       # 限流退避基数（秒），逐次翻倍
_TIMEOUT = (8.0, 30.0)

# 同一模型只允许一个下载任务：设置窗关掉再打开会新建对话框实例，
# 若不带全局锁，两个线程会同时写同一个 .part 文件把模型写坏
_downloading: set[str] = set()
_download_guard = threading.Lock()

# 下载进度放**模块级**而不是对话框实例：设置页保存关闭后对话框销毁，
# 后台线程还在下；重开设置页时从这里把进度接回来（曾因此显示
# "未下载完整"且无法再点下载）
dl_state: dict = {"running": False, "key": "", "done": 0, "total": 0,
                  "file": "", "error": ""}
# 取消请求标志：should_stop 采样它。置位后下载线程在下一个分块边界
# 抛 DownloadCancelled，段文件保留（可继续下载）；开始新下载时清零
dl_cancel: bool = False


@dataclass(frozen=True)
class RemoteFile:
    name: str          # 本地文件名
    path: str          # 仓库内相对路径
    size: int = 0      # 预期字节数（0=未知，不校验）
    # 预留：当前按"只钉大小"的口径，暂不校验哈希；加哈希时与 size 一起用
    sha256: str = ""


@dataclass(frozen=True)
class VoiceModel:
    key: str
    title: str
    engine: str        # sense_voice | paraformer
    approx_mb: int
    repo: str
    files: tuple[RemoteFile, ...]
    note: str = ""

    def dir(self) -> Path:
        return _models_root() / self.key


SENSE_VOICE = VoiceModel(
    key="sensevoice",
    title="SenseVoice",
    engine="sense_voice",
    approx_mb=228,
    repo="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
    files=(
        # size 为官方文件字节数（2026-10 核对）：只作参考与截断检测，
        # 上游更新导致大小变化时只警告不失败（不把"上游更新"变成"下不了"）
        RemoteFile("model.int8.onnx", "model.int8.onnx", size=239233841),
        RemoteFile("tokens.txt", "tokens.txt"),
    ),
    note="中英日韩粤，准确率高，识别带标点",
)

PARAFORMER = VoiceModel(
    key="paraformer",
    title="轻量模型",
    engine="paraformer",
    approx_mb=78,
    repo="csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09",
    files=(
        RemoteFile("model.int8.onnx", "model.int8.onnx", size=81828675),
        RemoteFile("tokens.txt", "tokens.txt"),
    ),
    note="体积小、下载快，短指令够用；标点与长句能力弱于 SenseVoice",
)

MODELS: dict[str, VoiceModel] = {m.key: m for m in (SENSE_VOICE, PARAFORMER)}
DEFAULT_MODEL = SENSE_VOICE.key


def _models_root() -> Path:
    return _json_store.data_dir() / "models"


def model_dir(key: str) -> Path:
    """模型目录；未知 key 回退默认模型。"""
    return MODELS.get(key, SENSE_VOICE).dir()


def is_ready(key: str) -> bool:
    """模型文件是否齐全（非零大小）。"""
    model = MODELS.get(key)
    if model is None:
        return False
    base = model.dir()
    return all((base / f.name).is_file() and (base / f.name).stat().st_size > 0
               for f in model.files)


def ready_size_mb(key: str) -> int:
    """已占用空间（MB，四舍五入）；未安装返回 0。"""
    base = model_dir(key)
    if not base.is_dir():
        return 0
    total = sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
    return int(round(total / (1024 * 1024)))


def remove_model(key: str) -> None:
    """删除已下载的模型（释放空间）。"""
    base = model_dir(key)
    if base.is_dir():
        shutil.rmtree(base, ignore_errors=True)
        logger.info("已删除语音模型 %s（%s）", key, base)


def system_proxies() -> dict | None:
    """读取系统代理（Clash/V2Ray 等只设 WinINET 代理，requests 不会自动用）。"""
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
    except OSError:
        return None
    if not enable or not server:
        return None
    server = str(server).strip()
    # 可能是 "127.0.0.1:7890" 或 "http=...;https=..."
    if "=" in server:
        mapping = {}
        for part in server.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                mapping[k.strip().lower()] = v.strip()
        http = mapping.get("http") or mapping.get("https")
        https = mapping.get("https") or mapping.get("http")
    else:
        http = https = server
    if not http:
        return None
    if "://" not in http:
        http = "http://" + http
    if "://" not in https:
        https = "http://" + https
    return {"http": http, "https": https}


ProgressFn = Callable[[int, int, str], None]     # (已下载字节, 总字节, 文件名)
ShouldStop = Callable[[], bool]


class DownloadCancelled(Exception):
    """用户取消下载。"""


def download_model(key: str, on_progress: ProgressFn | None = None,
                   should_stop: ShouldStop | None = None) -> str:
    """下载并落地模型，返回实际使用的下载源。

    并发分段下载（4 路 Range）+ 段内续传：慢网下断了不用从头再来，
    304MB 的 SenseVoice 在 0.06MB/s 的单连接下要 60 分钟，4 路实测约 3 倍。
    最终文件只在**校验通过后**才改名落地，所以段文件（`.part.N`）可以安全
    保留，下次接着下。
    """
    model = MODELS.get(key)
    if model is None:
        raise ValueError(f"未知模型: {key}")
    with _download_guard:
        if key in _downloading:
            raise RuntimeError("该模型正在下载中，请等当前任务结束")
        _downloading.add(key)
    global dl_cancel
    dl_cancel = False                   # 新任务开始：清掉上一次的取消请求
    try:
        origins = [o for o in _SOURCES]
        base = model.dir()
        base.mkdir(parents=True, exist_ok=True)
        proxies = system_proxies()
        used = ""
        for remote in model.files:
            target = base / remote.name
            urls = [(o, _file_url(o, model, remote)) for o in origins]
            used = _download_file(urls, target, remote, on_progress,
                                  should_stop, proxies, used)
        # 逐文件比大小只能发现"截断"，发现不了"长度自洽但内容坏"（如镜像返回
        # 错误页 HTML）。下载完整后再真加载一次模型，坏文件当场清掉
        dl_state["file"] = "加载自检"
        _verify_model_loads(key)
        logger.info("语音模型 %s 下载完成：%s", key, base)
        return used
    finally:
        with _download_guard:
            _downloading.discard(key)


def _file_url(origin: str, model: VoiceModel, remote: RemoteFile) -> str:
    return f"{origin}/{model.repo}/resolve/main/{remote.path}"


def _verify_model_loads(key: str) -> None:
    """载入自检：真加载模型并跑 0.2s 静音推理。

    为什么需要：文件长度与服务器一致**不等于内容正确**——镜像返回错误页、
    缓存中间件塞了别的东西，长度都可能自洽。只有真加载才能判断"能不能用"。
    失败即删掉坏文件并报明确错误，避免用户按热键时才遇到莫名其妙的失败。
    """
    from app.core.voice import asr      # 局部导入：asr 又依赖 models，避免循环
    import numpy as np

    try:
        asr.clear_cache()
        transcriber = asr.get_transcriber(key)
        transcriber.transcribe(np.zeros(int(16000 * 0.2), dtype=np.float32))
    except (NameError, ImportError, AttributeError, MemoryError) as exc:
        # 自检代码自身的问题（曾漏 import numpy，导致每个模型都被当成坏文件
        # 删掉）：绝不能因此删用户的模型，只记日志并放行
        logger.exception("模型自检代码异常（保留模型文件，请修代码）：%s", exc)
    except Exception as exc:            # noqa: BLE001 - 坏文件统一处理
        logger.error("模型 %s 加载自检失败：%s", key, exc)
        try:
            asr.clear_cache()           # 先释放文件句柄，Windows 才删得掉
            remove_model(key)
        except Exception:
            logger.exception("清理损坏的模型文件失败")
        raise RuntimeError(
            f"模型文件异常（加载失败：{exc}），已删除，请重新下载") from exc
    finally:
        asr.clear_cache()               # 不把自检实例留在缓存里
    logger.info("模型 %s 加载自检通过", key)


def partial_bytes(key: str) -> int:
    """已下载但未完成的字节数（用于显示"可继续下载"）。

    合并阶段被中断时会同时存在"已合并的 .part"和"各分段"，直接相加会
    超过模型体积（显示成"已下 277MB"），所以按已知总长封顶。
    """
    model = MODELS.get(key)
    if model is None:
        return 0
    base = model.dir()
    if not base.is_dir():
        return 0
    raw = sum(p.stat().st_size for p in base.glob("*.part*") if p.is_file())
    cap = sum(f.size for f in model.files if f.size)
    return min(raw, cap) if cap else raw


class _RateLimited(Exception):
    """源返回 429/5xx：退避后重试，不当成硬失败。"""


class _Throttle:
    """全局退避闸门：任一请求被限流，所有线程一起歇。

    实测连续探测 hf-mirror 会返回 429，硬顶只会更糟。
    """

    def __init__(self) -> None:
        self._until = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                left = self._until - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, 0.5))

    def penalize(self, seconds: float) -> None:
        with self._lock:
            self._until = max(self._until, time.monotonic() + seconds)


def _probe_total(url: str, proxies: dict | None) -> tuple[int, bool]:
    """返回 (总字节, 是否支持 Range)；拿不到长度返回 (0, False)。"""
    r = requests.head(url, timeout=_PROBE_TIMEOUT, allow_redirects=True,
                      proxies=proxies)
    if r.status_code >= 400:
        raise _RateLimited(f"HEAD {r.status_code}")
    total = int(r.headers.get("Content-Length") or 0)
    ranged = "bytes" in str(r.headers.get("Accept-Ranges") or "").lower()
    return total, ranged and total > 0


def _download_file(urls: list[tuple[str, str]], target: Path, remote: RemoteFile,
                   on_progress: ProgressFn | None, should_stop: ShouldStop | None,
                   proxies: dict | None, preferred: str = "") -> str:
    """下载单个文件，返回成功的源。优先源排在前（上次成功的源优先）。"""
    urls = sorted(urls, key=lambda u: 0 if u[0] == preferred else 1)
    part = target.with_suffix(target.suffix + ".part")
    name = remote.name

    # 离线快路：分段已全部齐备（例如上次在"合并"阶段被关掉程序），
    # 直接本地合并，既不用重新探测源、也不受断网影响
    local = _local_segments(part, remote)
    if local is not None:
        logger.info("%s 的分段已齐备，直接从本地合并（未联网核对上游是否有更新）", name)
        _merge_segments(part, local, target, remote.size, name, on_progress)
        return preferred or (urls[0][0] if urls else "local")

    total, ranged = 0, False
    origin_used = urls[0][0]
    for origin, url in urls:
        try:
            total, ranged = _probe_total(url, proxies)
            origin_used = origin
            break
        except Exception as exc:      # noqa: BLE001 - 换下一个源
            logger.debug("源探测失败 %s: %s", origin, exc)
    if total <= 0 or not ranged:
        # 拿不到长度/不支持 Range（如 tokens.txt 这类小文件）：走单流下载
        return _download_single(urls, target, remote, on_progress,
                                should_stop, proxies)

    if remote.size and total != remote.size:
        # 「只钉大小」的软校验：大小不符只提示，不硬失败——
        # 硬失败会让"上游更新模型"变成"用户再也下不了"
        logger.warning("%s 大小与预期不同（%s → %s 字节），可能上游已更新",
                       name, remote.size, total)
        # 本地残留的分段是旧版本的：按新计划续传会拼出"半旧半新"的文件
        # （旧段长度与新 range 恰好一致时静默通过校验）。清掉重来
        for stale in target.parent.glob(f"{part.name}.*"):
            try:
                stale.unlink()
                logger.info("已清理旧版本残留分段：%s", stale.name)
            except OSError:
                logger.exception("清理残留分段失败：%s", stale)

    n = max(1, math.ceil(total / _SEG_SIZE))
    ranges = [(i * _SEG_SIZE, min((i + 1) * _SEG_SIZE, total) - 1)
              for i in range(n)]
    seg_parts = [part.with_suffix(part.suffix + f".{i}") for i in range(n)]
    done = [min(p.stat().st_size, ranges[i][1] - ranges[i][0] + 1)
            if p.is_file() else 0
            for i, p in enumerate(seg_parts)]
    lock = threading.Lock()
    throttle = _Throttle()
    abort = threading.Event()
    started = time.monotonic()
    last_report = [0.0, 0]      # [上次上报时间, 上次上报字节]

    def report(delta: int, idx: int) -> None:
        with lock:
            done[idx] += delta
            if on_progress is None:
                return
            total_done = min(sum(done), total)
            now = time.monotonic()
            # 时间或增量任一达到阈值就上报：只按时间的话慢网下一分钟才动一次，
            # UI 看起来像卡死；也保证中断检查能及时拿到进度
            if (total_done < total
                    and now - last_report[0] < 0.2
                    and total_done - last_report[1] < 256 * 1024):
                return
            last_report[0] = now
            last_report[1] = total_done
            on_progress(total_done, total, name)

    with lock:
        if on_progress is not None:
            on_progress(min(sum(done), total), total, name)

    def work(idx: int) -> None:
        start, end = ranges[idx]
        seg_part = seg_parts[idx]
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            if abort.is_set():
                return
            for _origin, url in urls:
                if abort.is_set():
                    return
                try:
                    _fetch_range(url, seg_part, start, end, throttle,
                                 lambda d, i=idx: report(d, i),
                                 should_stop, proxies)
                    return
                except DownloadCancelled:
                    abort.set()
                    raise
                except _RateLimited as exc:
                    last_exc = exc
                    # 翻倍退避，且让所有线程一起等
                    throttle.penalize(_BACKOFF * (2 ** attempt))
                except Exception as exc:      # noqa: BLE001 - 换源/重试
                    last_exc = exc
                    logger.debug("分段 %d 失败（第 %d 次）: %s", idx, attempt + 1, exc)
        abort.set()
        raise RuntimeError(f"{name} 第 {idx + 1}/{n} 段下载失败：{last_exc}")

    pending = [i for i in range(n)
               if done[i] < ranges[i][1] - ranges[i][0] + 1]
    if pending:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(_CONCURRENCY, len(pending)),
                                thread_name_prefix="voice-dl") as pool:
            futures = [pool.submit(work, i) for i in pending]
            error: Exception | None = None
            for fut in futures:
                try:
                    fut.result()
                except Exception as exc:      # noqa: BLE001 - 汇总后抛出
                    error = error or exc
            if error is not None:
                raise error

    # 合并分段：先写 .part，校验总长后再改名为正式文件
    _merge_segments(part, seg_parts, target, total, name, on_progress)
    elapsed = max(0.001, time.monotonic() - started)
    logger.info("下载 %s 完成：%.1f MB，用时 %.1fs（%.2f MB/s，%d 路分段）",
                name, total / 1048576, elapsed,
                total / 1048576 / elapsed, n)
    return origin_used


def _local_segments(part: Path, remote: RemoteFile) -> list[Path] | None:
    """分段是否已全部齐备（按已知文件大小精确核对）。齐备则返回各段路径。"""
    if not remote.size:
        return None                     # 不知道总长就没法判断段数
    n = max(1, math.ceil(remote.size / _SEG_SIZE))
    parts: list[Path] = []
    for i in range(n):
        want = min(_SEG_SIZE, remote.size - i * _SEG_SIZE)
        seg = part.with_suffix(part.suffix + f".{i}")
        if not seg.is_file() or seg.stat().st_size != want:
            return None
        parts.append(seg)
    return parts


def _merge_segments(part: Path, seg_parts: list[Path], target: Path,
                    total: int, name: str,
                    on_progress: ProgressFn | None) -> None:
    """把各段按序拼成 .part，校验长度后改名为正式文件，再清理段文件。

    合并阶段被打断也无妨：段文件仍在，下次走 `_local_segments` 离线快路。
    """
    merged = 0
    with open(part, "wb") as out:
        for seg in seg_parts:
            with open(seg, "rb") as fh:
                shutil.copyfileobj(fh, out, 1024 * 1024)
            merged += seg.stat().st_size
            if on_progress is not None and total > 0:
                on_progress(min(merged, total), total, name)
    size = part.stat().st_size
    if total > 0 and size != total:
        raise RuntimeError(f"{name} 下载不完整（{size} / {total} 字节），请重试")
    os.replace(part, target)
    for seg in seg_parts:
        seg.unlink(missing_ok=True)


def _fetch_range(url: str, dest: Path, start: int, end: int, throttle: _Throttle,
                 on_bytes: Callable[[int], None], should_stop: ShouldStop | None,
                 proxies: dict | None) -> None:
    """下载 [start, end] 区间；段文件已有的字节当作进度继续（段内续传）。"""
    want = end - start + 1
    have = dest.stat().st_size if dest.is_file() else 0
    if have >= want:
        return
    throttle.wait()
    headers = {"Range": f"bytes={start + have}-{end}"}
    with requests.get(url, headers=headers, stream=True, timeout=_TIMEOUT,
                      proxies=proxies) as resp:
        if resp.status_code in (429, 500, 502, 503, 504):
            raise _RateLimited(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        mode = "ab"
        if resp.status_code == 200:
            # 源忽略了 Range，返回的是整文件：丢掉已有进度重头下
            have = 0
            mode = "wb"
        got = have
        with open(dest, mode) as fh:
            for chunk in resp.iter_content(chunk_size=_CHUNK):
                if should_stop is not None and should_stop():
                    raise DownloadCancelled()
                if not chunk:
                    continue
                fh.write(chunk)
                got += len(chunk)
                on_bytes(len(chunk))
    if got != want:
        raise RuntimeError(f"分段不完整（{got} / {want} 字节）")


def _download_single(urls: list[tuple[str, str]], target: Path, remote: RemoteFile,
                     on_progress: ProgressFn | None, should_stop: ShouldStop | None,
                     proxies: dict | None) -> str:
    """单流下载（源不支持 Range，或文件太小不值得分段）。"""
    part = target.with_suffix(target.suffix + ".part")
    name = remote.name
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        for origin, url in urls:
            try:
                with requests.get(url, stream=True, timeout=_TIMEOUT,
                                  proxies=proxies) as resp:
                    if resp.status_code in (429, 500, 502, 503, 504):
                        raise _RateLimited(f"HTTP {resp.status_code}")
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(part, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=_CHUNK):
                            if should_stop is not None and should_stop():
                                raise DownloadCancelled()
                            if not chunk:
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            if on_progress is not None:
                                on_progress(done, total, name)
                    if total and done != total:
                        raise RuntimeError(
                            f"{name} 下载不完整（{done} / {total} 字节），请重试")
                    if remote.size and done != remote.size:
                        logger.warning("%s 大小与预期不同（%s → %s 字节）",
                                       name, remote.size, done)
                    os.replace(part, target)
                    return origin
            except DownloadCancelled:
                raise
            except Exception as exc:      # noqa: BLE001 - 换源/重试
                last_exc = exc
                part.unlink(missing_ok=True)
                logger.debug("单流下载失败 %s（第 %d 次）: %s", origin, attempt + 1, exc)
        time.sleep(_BACKOFF * (2 ** attempt) * 0.5)
    raise RuntimeError(f"{name} 下载失败：{last_exc}")


def describe(key: str) -> str:
    """给界面用的一行状态文案。"""
    model = MODELS.get(key)
    if model is None:
        return "未知模型"
    if is_ready(key):
        return f"已就绪（{ready_size_mb(key)} MB）"
    with _download_guard:
        if key in _downloading or (dl_state.get("running")
                                   and dl_state.get("key") == key):
            return "下载中…"
    base = model.dir()
    if base.is_dir() and any(base.iterdir()):
        # 有未完成的段文件：告诉用户能接着下，而不是从 0 开始
        already = partial_bytes(key)
        if already > 0:
            return f"未下载完整（已下 {already / 1048576:.0f} MB，可继续下载）"
        return "未下载完整（可重新下载）"
    return f"未安装（约 {model.approx_mb} MB）"
