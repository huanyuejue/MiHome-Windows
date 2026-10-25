# SPDX-License-Identifier: GPL-3.0-or-later
"""语音控制回归测试（离线、不碰真实配置/麦克风）。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests._isolate import isolate

isolate()

from app.core.models import DeviceInfo
from app.core.voice import models as voice_models
from app.core.voice import parser
from app.core.voice.hotkey import MOD_CONTROL, format_spec, parse_spec


def _dev(did, name, room, online=True):
    return DeviceInfo(did=did, name=name, model="test.light", home_name="家",
                      room_name=room, online=online)


DEVICES = [
    _dev("d1", "客厅灯带", "客厅"),
    _dev("d2", "客厅台灯", "客厅"),
    _dev("d3", "主卧吸顶灯", "主卧"),
    _dev("d4", "书房台灯", "书房"),
    _dev("d5", "客厅插座", "客厅"),
]
# 插座不是开关类设备（known_power 里没有它）→ 不应被「全部关灯」命中
POWER = {"d1": True, "d2": False, "d3": True, "d4": False}

# 多键开关子通道按房间默认命名（现场真实形态）：
# 名字里没有"灯"字 → 按既定规则**不纳入**「所有灯」（不猜测通道后面接什么，
# 猜错会误关风扇/浴霸）；用户给设备改名成「卫生间灯」后即纳入。
def _switch(did, name, room):
    return DeviceInfo(did=did, name=name, model="linp.switch.e3sbw3",
                      spec_type="urn:miot-spec-v2:device:switch:0000A003:linp-e3sbw3:1",
                      home_name="家", room_name=room, online=True)


DEFAULT_NAMED = [
    _switch("n1", "卫生间", "卫生间"),
    _switch("n2", "洗漱台1", "洗漱台"),
    _switch("n3", "洗漱台2", "洗漱台"),
    _switch("n4", "北卧双开", "北卧"),
    # 云端规格类型是 light 的真灯具：名字没有"灯"字也算灯（权威类型优先）
    DeviceInfo(did="n5", name="主卧氛围", model="yeelink.light.xxx",
               spec_type="urn:miot-spec-v2:device:light:0000A001:yeelink-xxx:1",
               home_name="家", room_name="主卧", online=True),
    DeviceInfo(did="n6", name="Nas插座", model="chuangmi.plug.m3",
               spec_type="urn:miot-spec-v2:device:outlet:0000A002:chuangmi-m3:1",
               home_name="家", room_name="书房", online=True),
]
DEFAULT_POWER = {d.did: True for d in DEFAULT_NAMED}
SCENES = [
    {"scene_id": "s1", "name": "回家模式", "home_id": "h1", "home_name": "家"},
    {"scene_id": "s2", "name": "离家关灯", "home_id": "h1", "home_name": "家"},
]


# ---------- 1. 热键写法解析 ----------
def test_hotkey_spec() -> None:
    assert parse_spec("F9") == (0, 0x78)
    assert parse_spec("Ctrl+Alt+Space")[1] == 0x20
    assert parse_spec("ctrl+alt+space") == parse_spec("Ctrl+Alt+Space")
    assert format_spec("ctrl+alt+space") == "Ctrl+Alt+Space"
    assert format_spec("F9") == "F9"
    assert format_spec("shift+a") == "Shift+A"
    # QKeySequenceEdit 产出这些名字：解析不了的话用户录完键会注册失败
    for qt_name in ("PgUp", "PgDn", "Del", "Ins", "Return", "Esc", "Space"):
        mods, vk = parse_spec(qt_name)
        assert mods == 0 and vk > 0, qt_name
    for bad in ("", "Ctrl", "Ctrl+NotAKey", "F99"):
        try:
            parse_spec(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法热键未被拒绝: {bad!r}")
    # 探测：本程序正在用的键必须判为可用。RegisterHotKey 是独占的，
    # 若照旧实现会对自己已持有的键报"被占用"（现场：设置 F8 后保存被拦）
    from app.core.voice import hotkey as vk_hotkey
    assert vk_hotkey.probe_available("F8", current="F8") is True
    assert vk_hotkey.probe_available("ctrl+alt+space",
                                     current="Ctrl+Alt+Space") is True
    assert vk_hotkey._PROBE_ID != vk_hotkey._HOTKEY_ID
    print("1. 热键写法解析 OK")


class _FakeUser32:
    """只模拟 GetAsyncKeyState：给出"此刻按着哪些虚拟键"。"""

    def __init__(self, down):
        self.down = set(down)

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down else 0


def test_hotkey_modifiers() -> None:
    """修饰键必须参与按下判定。

    RegisterHotKey 只独占"组合键"，不会屏蔽单键；若轮询只看主键，
    注册 Ctrl+Space 后在任何输入框里打个空格都会触发录音。
    """
    from app.core.voice.hotkey import GlobalHotkey

    def is_pressed(mods, down):
        hk = GlobalHotkey()
        hk._user32 = _FakeUser32(down)
        hk._mods, hk._vk = mods, 0x20
        return hk._is_pressed()

    assert is_pressed(MOD_CONTROL, {0x20}) is False, "只按空格不应触发 Ctrl+Space"
    assert is_pressed(MOD_CONTROL, {0x20, 0x11}) is True
    assert is_pressed(MOD_CONTROL, {0x20, 0x11, 0x10}) is False, \
        "多按了 Shift 不属于注册的组合"
    assert is_pressed(0, {0x20}) is True, "单键热键应正常触发"
    assert is_pressed(0, {0x20, 0x11}) is False, "单键热键不应被 Ctrl+Space 触发"
    print("1b. 热键修饰键判定（Ctrl+Space 不再被单按空格触发）OK")


# ---------- 2. 指令解析 ----------
def test_parser() -> None:
    it = parser.parse("打开客厅灯带", DEVICES, SCENES, POWER)
    assert it.kind == "power" and it.state is True
    assert [d.did for d in it.devices] == ["d1"]
    assert it.batch is False, "单台设备不应标记为批量"

    it = parser.parse("把主卧的灯关掉", DEVICES, SCENES, POWER)
    assert it.kind == "power" and it.state is False
    assert [d.did for d in it.devices] == ["d3"]

    # 房间内多台 → 批量，且只命中开关类设备
    it = parser.parse("关闭客厅的灯", DEVICES, SCENES, POWER)
    assert it.kind == "power" and it.state is False
    assert {d.did for d in it.devices} == {"d1", "d2"}, it.device_names()
    assert it.batch is True

    # 全部关灯 → 所有开关类设备里的"灯"，不含插座
    it = parser.parse("全部关灯", DEVICES, SCENES, POWER)
    assert it.state is False and it.batch is True
    assert "d5" not in [d.did for d in it.devices], "插座不应被「关灯」命中"

    # 场景：带动词 / 只报名字
    it = parser.parse("执行回家模式", DEVICES, SCENES, POWER)
    assert it.kind == "scene" and it.scene["scene_id"] == "s1"
    it = parser.parse("回家模式", DEVICES, SCENES, POWER)
    assert it.kind == "scene" and it.scene["scene_id"] == "s1"

    # 多目标
    it = parser.parse("打开主卧吸顶灯和书房台灯", DEVICES, SCENES, POWER)
    assert {d.did for d in it.devices} == {"d3", "d4"} and it.batch is True

    # 模糊匹配（识别偶尔错字）
    it = parser.parse("打开书房台等", DEVICES, SCENES, POWER)
    assert it.kind == "power" and [d.did for d in it.devices] == ["d4"], it.message


    # 设备名里带「开关」：不能把"关"当成关闭动词（曾把"打开"执行成关闭）
    sw = [_dev("s1", "开关A", "客厅"), _dev("s2", "开关B", "书房")]
    sw_power = {"s1": True, "s2": False}
    it = parser.parse("打开开关A", sw, [], sw_power)
    assert it.kind == "power" and it.state is True, f"打开被解析成 {it.state}: {it.message}"
    assert [d.did for d in it.devices] == ["s1"]
    it = parser.parse("关闭开关A", sw, [], sw_power)
    assert it.state is False and [d.did for d in it.devices] == ["s1"]

    # 启动早期 _known_power 还是空：不能把所有设备都过滤掉
    it = parser.parse("打开客厅灯带", DEVICES, SCENES, {})
    assert it.kind == "power" and [d.did for d in it.devices] == ["d1"], it.message

    # 状态只拉到一部分时（启动后几秒内，_known_power 逐渐填充）：
    # 「全部关灯」必须覆盖全部灯，不能只关已知的那几台还回执"已关闭"
    it = parser.parse("全部关灯", DEVICES, SCENES, {"d1": False})
    assert {d.did for d in it.devices} == {"d1", "d2", "d3", "d4"}, it.device_names()

    # 房间+类别没命中（插座已确认没有开关能力，值为 None）→ 必须报"没有找到"，
    # 不能退回整个房间把客厅灯全关掉（曾误关客厅所有设备）
    no_switch = {**POWER, "d5": None}       # 探测已覆盖全部设备，插座=不可开关
    it = parser.parse("关闭客厅的插座", DEVICES, SCENES, no_switch)
    assert it.kind == "error" and "没有找到" in it.message, it.message
    # 插座可开关时则正常只关插座
    it = parser.parse("关闭客厅的插座", DEVICES, SCENES, {**POWER, "d5": False})
    assert it.kind == "power" and [d.did for d in it.devices] == ["d5"], it.message

    # 场景名自带开关动词（如「离家关灯」）：设备匹配不上时要执行场景，
    # 不能丢给用户一句"没有找到设备"
    it = parser.parse("离家关灯", DEVICES, SCENES, POWER)
    assert it.kind == "scene" and it.scene["scene_id"] == "s2", it.message
    it = parser.parse("打开离家关灯模式", DEVICES, SCENES, POWER)
    assert it.kind == "scene" and it.scene["scene_id"] == "s2", it.message

    # 场景列表是 SceneInfo（dataclass）而不是 dict：main_window 传的就是它，
    # 曾因 scene.get() 抛 AttributeError → 所有指令都报"语音指令解析失败"
    from app.core.models import SceneInfo
    info_scenes = [SceneInfo(scene_id="s1", name="回家模式",
                             home_id="h1", home_name="家")]
    it = parser.parse("执行回家模式", DEVICES, info_scenes, POWER)
    assert it.kind == "scene", it.message
    it = parser.parse("打开客厅灯带", DEVICES, info_scenes, POWER)
    assert it.kind == "power" and [d.did for d in it.devices] == ["d1"], it.message
    # 属性类指令：明确拒绝，而不是乱开关
    it = parser.parse("把空调调到26度", DEVICES, SCENES, POWER)
    assert it.kind == "error" and "只支持" in it.message

    # 找不到 / 没说清
    assert parser.parse("打开不存在的设备", DEVICES, SCENES, POWER).kind == "error"
    it = parser.parse("嗯", DEVICES, SCENES, POWER)
    assert it.kind == "error" and it.message
    it = parser.parse("", DEVICES, SCENES, POWER)
    assert it.kind == "error"
    print("2. 指令解析（开关/房间批量/全部/场景/多目标/模糊/拒绝）OK")

    # 「所有的灯」规则：名字含「灯」字 或 云端规格类型是 light；
    # 不按房间名推断（猜通道接什么会误关风扇/浴霸），漏掉的靠改名解决
    it = parser.parse("关闭所有的灯", DEFAULT_NAMED, [], DEFAULT_POWER)
    got = set(it.device_names())
    assert got == {"主卧氛围"}, got          # 只有 spec_type=light 的那台
    assert "卫生间" not in got and "洗漱台1" not in got, got
    assert "Nas插座" not in got, got
    # 回执要告诉用户有几台开关没纳入（引导改名，而不是静默漏掉）
    assert "4 台开关" in it.message, it.message
    # 改名成「卫生间灯」后立刻纳入
    renamed = [
        _switch("n1", "卫生间灯", "卫生间"),
        _switch("n2", "洗漱台灯1", "洗漱台"),
    ]
    it = parser.parse("关闭所有的灯", renamed, [], {d.did: True for d in renamed})
    assert set(it.device_names()) == {"卫生间灯", "洗漱台灯1"}, it.device_names()
    # 具体点名仍然可控（名字被直接命中，不受「灯」类别规则影响）
    it = parser.parse("关闭卫生间的灯", DEFAULT_NAMED, [], DEFAULT_POWER)
    assert "卫生间" in it.device_names(), it.device_names()
    it = parser.parse("打开洗漱台1", DEFAULT_NAMED, [], DEFAULT_POWER)
    assert it.device_names() == ["洗漱台1"], it.device_names()


# ---------- 3. 模型状态与删除 ----------
def test_resample() -> None:
    import numpy as np
    from app.core.voice.asr import resample

    src = np.sin(np.linspace(0, 6.28, 48000)).astype(np.float32)     # 48k 采样
    out = resample(src, 48000, 16000)
    assert abs(len(out) - 16000) <= 1, f"48k→16k 长度异常: {len(out)}"
    assert len(resample(src, 48000, 48000)) == len(src)
    assert len(resample(np.zeros(0, dtype=np.float32), 48000)) == 0
    print("3. 重采样（48k→16k）OK")


test_resample()
def test_model_state() -> None:
    key = voice_models.DEFAULT_MODEL
    assert key in voice_models.MODELS
    assert voice_models.is_ready(key) is False, "测试环境不应已装模型"
    assert "未安装" in voice_models.describe(key)

    base = voice_models.model_dir(key)
    base.mkdir(parents=True, exist_ok=True)
    for f in voice_models.MODELS[key].files:
        (base / f.name).write_bytes(b"x" * 16)
    assert voice_models.is_ready(key) is True
    assert voice_models.ready_size_mb(key) >= 0
    assert "已就绪" in voice_models.describe(key)

    voice_models.remove_model(key)
    assert voice_models.is_ready(key) is False
    assert "未安装" in voice_models.describe(key)
    # 同一模型并发下载：第二个调用必须立刻拒绝（否则两个线程写坏同一个 .part）
    voice_models._downloading.add(key)
    try:
        try:
            voice_models.download_model(key)
        except RuntimeError as exc:
            assert "下载中" in str(exc), exc
        else:
            raise AssertionError("并发下载未被拦截")
    finally:
        voice_models._downloading.discard(key)
    print("4. 模型状态（未安装/就绪/删除/并发拦截）OK")


def test_recording_cap() -> None:
    """录音必须有时长上限 + 松开丢失后的自愈。

    否则用户按着不放 / 松开事件被吞时，麦克风会一直开着、`_recording`
    一直为 True，热键表现为"按了没反应"，只能重启程序。
    """
    import time

    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])

    from app.ui import voice_control as vc_mod

    vc = vc_mod.VoiceController(None, lambda: [], lambda: [], lambda: {})
    vc._overlay.show_notice = lambda *a, **k: None
    vc._notify = lambda *a, **k: None
    ended = []
    vc._on_released = lambda: ended.append(1)      # 自动收尾 == 当作松开

    vc._recording = True
    vc._rec_started = time.monotonic() - (vc_mod._MAX_RECORD_SECONDS + 1)
    vc._recording_tick()
    assert ended == [1], "超过录音上限应自动收尾"

    ended.clear()
    vc._rec_started = time.monotonic()
    vc._recording_tick()
    assert ended == [], "未到上限不应收尾"

    # 计时器要随录音一起停，不能留着空转
    vc._rec_timer.start()
    vc._stop_recording()
    assert vc._rec_timer.isActive() is False
    print("5. 录音上限（25s 自动收尾 + 计时器随录音停止）OK")


test_hotkey_spec()
test_hotkey_modifiers()
test_parser()
test_model_state()
test_recording_cap()
print("\nVOICE REGRESSION ALL PASS")
