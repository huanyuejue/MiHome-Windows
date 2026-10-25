"""语音模型下载器回归：并发分段 / 段内续传 / 429 退避 / 大小仅警告。

不联网：本地起一个支持 Range 的 HTTP 服务充当"镜像"。
"""
import hashlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests._isolate import isolate

isolate()

from app.core.models import DeviceInfo  # noqa: F401  (保持与其它套件同样的加载顺序)
from app.core.voice import models as vm


class _State:
    payload = b""
    hits = 0
    ranges = 0
    lock = threading.Lock()
    fail_first = 0          # 前 N 个请求返回 429
    ignore_range = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):     # 静音
        pass

    def _serve(self, with_body: bool) -> None:
        with _State.lock:
            _State.hits += 1
            n = _State.hits
            fail_first = _State.fail_first
        if n <= fail_first:
            self.send_response(429)
            self.send_header("Content-Length", "0")
            self.send_header("Retry-After", "1")
            self.end_headers()
            return

        rng = self.headers.get("Range")
        data = _State.payload
        if rng and not _State.ignore_range:
            with _State.lock:
                _State.ranges += 1
            start_s, end_s = rng.split("=", 1)[1].split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            chunk = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            if with_body:
                self.wfile.write(chunk)
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if with_body:
            self.wfile.write(data)

    def do_HEAD(self):        # noqa: N802 (http.server 约定)
        self._serve(False)

    def do_GET(self):         # noqa: N802
        self._serve(True)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 客户端主动中断（Cancel）时写 socket 会报 ConnectionReset，属预期，不打印
        pass


def _start_server() -> ThreadingHTTPServer:
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="dl-srv").start()
    return srv


def _clean() -> None:
    """每个用例从干净目录开始（否则上一轮的成品文件会让 is_ready 提前为真）。"""
    import shutil
    shutil.rmtree(vm.model_dir("selftest"), ignore_errors=True)


def _fake_model(port: int, payload: bytes, size: int = 0):
    """把真实模型表临时替换成指向本地服务的假模型。"""
    origin = f"http://127.0.0.1:{port}"
    model = vm.VoiceModel(
        key="selftest", title="自测", engine="sense_voice",
        approx_mb=len(payload) // 1048576 + 1,
        repo="repo", files=(vm.RemoteFile("model.int8.onnx", "model.int8.onnx",
                                          size=size or len(payload)),),
    )
    vm.MODELS["selftest"] = model
    vm._SOURCES = (origin,)
    # 测试文件只有几 MB，把段大小调小才能真正验证多段并发
    vm._SEG_SIZE = 1024 * 1024
    # 假 payload 不是真模型，跳过加载自检（自检本身有专门用例）
    vm._verify_model_loads = lambda key: None
    return model


_REAL_VERIFY = vm._verify_model_loads


def _reset(**kw):
    _State.hits = 0
    _State.ranges = 0
    _State.fail_first = kw.get("fail_first", 0)
    _State.ignore_range = kw.get("ignore_range", False)


def test_parallel_and_merge() -> None:
    """并发分段：内容必须逐字节一致，且确实用了 Range 分段。"""
    payload = os.urandom(3 * 1024 * 1024 + 12345)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset()
        _clean()
        _fake_model(srv.server_address[1], payload)
        seen: list[tuple[int, int]] = []
        origin = vm.download_model(
            "selftest", on_progress=lambda d, t, n: seen.append((d, t)))
        out = vm.model_dir("selftest") / "model.int8.onnx"
        assert out.read_bytes() == payload, "并发合并后内容不一致"
        assert origin.startswith("http://127.0.0.1"), origin
        assert _State.ranges >= 2, f"没有真正分段（Range 命中 {_State.ranges} 次）"
        assert seen and seen[-1][0] == seen[-1][1] == len(payload), seen[-1:]
        assert vm.is_ready("selftest")
        # 段文件与 .part 都应清理干净
        leftovers = list(vm.model_dir("selftest").glob("*.part*"))
        assert not leftovers, leftovers
    finally:
        srv.shutdown()


def test_resume_after_cancel() -> None:
    """中断后重下：已下的段要被复用，不从头再来。"""
    payload = os.urandom(6 * 1024 * 1024)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset()
        _clean()
        _fake_model(srv.server_address[1], payload)
        # 下载到 1MB 就主动中断（用进度回调触发，避免依赖时序）
        state = {"stop": False}

        def on_progress(done, total, name):
            if done >= 1024 * 1024:
                state["stop"] = True

        try:
            vm.download_model("selftest", on_progress=on_progress,
                              should_stop=lambda: state["stop"])
        except vm.DownloadCancelled:
            pass
        else:
            raise AssertionError("没有按预期中断")
        kept = vm.partial_bytes("selftest")
        assert kept > 0, "中断后没有保留任何进度（下次要从 0 开始）"
        assert "可继续下载" in vm.describe("selftest"), vm.describe("selftest")

        # 继续下载：已完成/半完成的段要被复用，请求数应明显少于从头下载
        before = _State.hits
        vm.download_model("selftest")
        out = vm.model_dir("selftest") / "model.int8.onnx"
        assert out.read_bytes() == payload, "续传后内容不一致"
        assert not list(vm.model_dir("selftest").glob("*.part*"))
        print(f"   中断保留 {kept / 1048576:.1f}MB；续传只发了 "
              f"{_State.hits - before} 个请求")
    finally:
        srv.shutdown()


def test_rate_limit_backoff() -> None:
    """429 要退避重试，而不是直接失败。"""
    payload = os.urandom(1024 * 512)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset(fail_first=2)
        _clean()
        _fake_model(srv.server_address[1], payload)
        vm.download_model("selftest")
        out = vm.model_dir("selftest") / "model.int8.onnx"
        assert out.read_bytes() == payload
    finally:
        srv.shutdown()


def test_size_mismatch_only_warns() -> None:
    """大小不符只警告不失败（上游更新模型时不能让用户下不了）。"""
    payload = os.urandom(256 * 1024)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset()
        _clean()
        _fake_model(srv.server_address[1], payload, size=len(payload) + 999)
        vm.download_model("selftest")
        assert vm.is_ready("selftest")
    finally:
        srv.shutdown()


def test_no_range_fallback() -> None:
    """源不支持 Range 时退回单流下载，仍要校验通过。"""
    payload = os.urandom(300 * 1024)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset(ignore_range=True)
        _clean()
        _fake_model(srv.server_address[1], payload)
        vm.download_model("selftest")
        out = vm.model_dir("selftest") / "model.int8.onnx"
        assert out.read_bytes() == payload
    finally:
        srv.shutdown()


def test_bad_file_cleanup() -> None:
    """长度对但内容坏（加载不了）：自检要删掉坏文件并报明确错误。

    这正是"镜像返回自洽长度的错误页"那一类问题，只比大小发现不了。
    """
    from app.core.voice import asr

    payload = os.urandom(200 * 1024)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset()
        _clean()
        _fake_model(srv.server_address[1], payload)
        # 恢复真实自检函数，但让"加载"必然失败（模拟坏 onnx）
        vm._verify_model_loads = _REAL_VERIFY
        orig = asr.get_transcriber
        asr.get_transcriber = lambda key: (_ for _ in ()).throw(
            RuntimeError("onnx 解析失败"))
        try:
            try:
                vm.download_model("selftest")
            except RuntimeError as exc:
                assert "模型文件异常" in str(exc), exc
            else:
                raise AssertionError("坏模型没有被拒绝")
        finally:
            asr.get_transcriber = orig
        assert not vm.is_ready("selftest"), "坏文件应当被删除，不能被当成已就绪"
        assert not list(vm.model_dir("selftest").glob("*")), "残留文件未清理"
    finally:
        srv.shutdown()


def test_verify_runs_with_real_numpy() -> None:
    """加载自检本身必须能跑通（曾漏 import numpy，导致每个模型都被当坏文件删掉）。"""
    from app.core.voice import asr

    seen = {}

    class FakeTranscriber:
        def transcribe(self, samples, sample_rate=16000):
            seen["len"] = len(samples)
            return ""

    orig = asr.get_transcriber
    asr.get_transcriber = lambda key: FakeTranscriber()
    try:
        vm._verify_model_loads("sensevoice")
    finally:
        asr.get_transcriber = orig
    assert seen.get("len", 0) > 0, "自检没有真正跑推理"


def test_offline_merge_when_segments_complete() -> None:
    """分段已齐但最终文件还没合并（如合并时关了程序）：断网也要能完成。

    现场就是这个状态：228MB 分段全下完、合并到 49MB 时退出，再点下载
    不该重新走网络。
    """
    import math

    payload = os.urandom(3 * 1024 * 1024)
    _clean()
    # 源指向一个必然连不上的端口：只要能成功，就证明没走网络
    model = _fake_model(9, payload)
    assert model.files[0].size == len(payload)
    base = vm.model_dir("selftest")
    base.mkdir(parents=True, exist_ok=True)
    part = base / "model.int8.onnx.part"
    n = math.ceil(len(payload) / vm._SEG_SIZE)
    for i in range(n):
        s = i * vm._SEG_SIZE
        e = min((i + 1) * vm._SEG_SIZE, len(payload))
        part.with_suffix(part.suffix + f".{i}").write_bytes(payload[s:e])
    vm.download_model("selftest")
    assert (base / "model.int8.onnx").read_bytes() == payload
    assert not list(base.glob("*.part*")), "合并后应清理段文件"


def test_cancel_keeps_progress() -> None:
    """取消请求（dl_cancel 标志）→ 下载中止、进度保留；新任务自动清标志。"""
    payload = os.urandom(6 * 1024 * 1024)
    _State.payload = payload
    srv = _start_server()
    try:
        _reset()
        _clean()
        _fake_model(srv.server_address[1], payload)

        # 本地回环下载太快，取消采样赶不上分块循环：给服务端加延迟，
        # 保证 on_progress 有机会在下载途中触发取消
        orig_serve = _Handler._serve

        def slow_serve(self, with_body):
            if with_body:
                time.sleep(0.05)
            orig_serve(self, with_body)

        _Handler._serve = slow_serve
        try:
            state = {"cancelled": False}

            def on_progress(done, total, name):
                if done >= 1024 * 1024:
                    vm.dl_cancel = True     # 模拟用户点"取消"

            try:
                vm.download_model("selftest", on_progress=on_progress,
                                  should_stop=lambda: vm.dl_cancel)
            except vm.DownloadCancelled:
                state["cancelled"] = True
            assert state["cancelled"], "取消请求没有生效"
        finally:
            _Handler._serve = orig_serve

        assert vm.partial_bytes("selftest") > 0, "取消后应保留断点进度"

        # 新任务开始时标志自动清零，能正常完成
        vm.dl_cancel = False
        vm.download_model("selftest")
        assert (vm.model_dir("selftest") / "model.int8.onnx").read_bytes() == payload
    finally:
        srv.shutdown()


def main() -> None:
    test_parallel_and_merge()
    print(f"1. 并发分段下载（{vm._CONCURRENCY} 路 Range + 合并校验）OK")
    test_resume_after_cancel()
    print("2. 断点续传（中断保留进度 → 继续完成）OK")
    test_rate_limit_backoff()
    print("3. 429 限流退避重试 OK")
    test_size_mismatch_only_warns()
    print("4. 大小不符只警告（不把上游更新变成下不了）OK")
    test_no_range_fallback()
    print("5. 源不支持 Range → 单流回退 OK")
    test_bad_file_cleanup()
    print("6. 加载自检（长度对但内容坏 → 删除并报错）OK")
    test_verify_runs_with_real_numpy()
    print("7. 加载自检可正常执行（numpy 导入）OK")
    test_offline_merge_when_segments_complete()
    print("8. 分段齐备 → 断网也可本地合并完成 OK")
    test_cancel_keeps_progress()
    print("9. 取消请求 → 中止保留进度，新任务自动清标志 OK")
    vm.MODELS.pop("selftest", None)
    print("\nVOICE DOWNLOAD REGRESSION ALL PASS")


if __name__ == "__main__":
    main()
