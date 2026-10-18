# SPDX-License-Identifier: GPL-3.0-or-later
"""多键面板虚拟子设备的开关回归测试。

背景：一个三键实体开关在云端派生出三个虚拟设备（did 形如
`<父did>.s2/.s3/.s4`），规格里三路是 `on-2`(siid 2)、`on-3`(siid 3)、
`on-4`(siid 4)。历史实现只取第一条 on 属性，导致三路虚拟设备全在
控制第一路 —— 用户表现为"虚拟设备控制不了/开错灯"。

本测试锁死：did 后缀 N 必须映射到 siid N，读写都按这一路发请求。
"""

from app.core.service import MijiaService

# 与真实 linp.switch.e3sbw3 规格一致的三路 on 属性
SPEC_3GANG = {
    "name": "linp switch e3sbw3",
    "properties": [
        {"name": "on-2", "type": "bool", "rw": "rw", "method": {"siid": 2, "piid": 1}},
        {"name": "mode-2", "type": "uint", "rw": "rw", "method": {"siid": 2, "piid": 2}},
        {"name": "on-3", "type": "bool", "rw": "rw", "method": {"siid": 3, "piid": 1}},
        {"name": "mode-3", "type": "uint", "rw": "rw", "method": {"siid": 3, "piid": 2}},
        {"name": "on-4", "type": "bool", "rw": "rw", "method": {"siid": 4, "piid": 1}},
        {"name": "temperature", "type": "float", "rw": "r", "method": {"siid": 10, "piid": 10}},
    ],
}

# 单键/插座规格：只有一条 on-2
SPEC_1GANG = {
    "name": "plug",
    "properties": [
        {"name": "on", "type": "bool", "rw": "rw", "method": {"siid": 2, "piid": 1}},
    ],
}

# ---------- 1. did 后缀解析 ----------
assert MijiaService._gang_from_did("1234567890.s2") == 2
assert MijiaService._gang_from_did("1234567890.s3") == 3
assert MijiaService._gang_from_did("1234567890.s4") == 4
assert MijiaService._gang_from_did("1234567890") is None, "父设备不带后缀"
assert MijiaService._gang_from_did("blt.3.1fv3aoemc4c01") is None, "mac 风格 did 不应误判"
assert MijiaService._gang_from_did("987654321") is None
print("1. did 后缀 → 通道号 OK")

# ---------- 2. on 属性按通道挑选 ----------
def method_of(did, spec=SPEC_3GANG):
    return MijiaService._find_on_method(spec, did)


assert method_of("1234567890.s2") == {"siid": 2, "piid": 1}
assert method_of("1234567890.s3") == {"siid": 3, "piid": 1}, \
    f"s3 应映射 siid 3，实际 {method_of('1234567890.s3')}"
assert method_of("1234567890.s4") == {"siid": 4, "piid": 1}
# 父设备无后缀：沿用第一条（历史行为，用户反馈父设备本来就能开关）
assert method_of("1234567890") == {"siid": 2, "piid": 1}
# 单键设备不受影响
assert MijiaService._find_on_method(SPEC_1GANG, "987654321") == {"siid": 2, "piid": 1}
# 后缀与 siid 对不上时按顺序兜底（s2→第1条，s3→第2条）
assert MijiaService._find_on_method(SPEC_3GANG, "x.s3") == {"siid": 3, "piid": 1}
SPARSE = {"properties": [
    {"name": "on-5", "type": "bool", "rw": "rw", "method": {"siid": 5, "piid": 1}},
    {"name": "on-6", "type": "bool", "rw": "rw", "method": {"siid": 6, "piid": 1}},
]}
assert MijiaService._find_on_method(SPARSE, "x.s2") == {"siid": 5, "piid": 1}, "顺序兜底失效"
# 没有 on 属性 → None
assert MijiaService._find_on_method({"properties": []}, "x.s2") is None
print("2. 多路 on 按通道选取 OK")

# ---------- 3. 读写请求一律发给父 did + 对应 siid ----------
# 实测云端对子 did 的拒绝要等 ~6 秒才返回，所以**不能**先试子 did，
# 必须一步到位发父 did；下面断言不会出现任何子 did 请求。
class FakeApi:
    def __init__(self):
        self.writes: list[list[dict]] = []
        self.reads: list[list[dict]] = []

    def set_devices_prop(self, params):
        self.writes.append(params)
        return [{"code": 0} for _ in params]

    def get_devices_prop(self, params):
        self.reads.append(params)
        return [{"did": q["did"], "siid": q["siid"], "code": 0, "value": True}
                for q in params]


def make_service(spec):
    """绕过登录构造 service：只装本次要用的最小状态。"""
    svc = object.__new__(MijiaService)
    svc._device_index = {}
    svc._spec_cache = {}
    svc._api = FakeApi()
    svc._specs_for = lambda dids: {d: spec for d in dids}
    return svc


def child_requests(api):
    """所有发往子 did（含 .sN）的请求。"""
    return [q for batch in api.reads for q in batch if ".s" in str(q["did"])] + \
           [q for batch in api.writes for q in batch if ".s" in str(q["did"])]


svc = make_service(SPEC_3GANG)
for did, expect_siid in (("p.s2", 2), ("p.s3", 3), ("p.s4", 4)):
    svc.set_power_state(did, True)
    sent = svc._api.writes[-1][0]
    assert sent["did"] == "p", f"{did} 的写请求应发往父 did，实际: {sent}"
    assert sent["siid"] == expect_siid and sent["value"] is True, f"写请求错误: {sent}"
# 三次写入 = 三次请求，没有额外的试探请求
assert len(svc._api.writes) == 3, f"写请求数量异常（可能存在子 did 试探）: {svc._api.writes}"
assert not child_requests(svc._api), f"不应出现子 did 请求: {child_requests(svc._api)}"

# 批量读：一条请求里三路各自用不同 siid，全部发父 did
svc2 = make_service(SPEC_3GANG)
states = svc2.power_states(["p.s2", "p.s3", "p.s4"])
assert states == {"p.s2": True, "p.s3": True, "p.s4": True}, states
assert len(svc2._api.reads) == 1, f"应只需一次请求，实际 {len(svc2._api.reads)} 次"
batch = svc2._api.reads[0]
assert all(q["did"] == "p" for q in batch), f"读请求应全部发父 did: {batch}"
by_siid = {q["siid"]: q for q in batch}
assert set(by_siid) == {2, 3, 4}, f"三路 siid 应对应 2/3/4: {batch}"
assert not child_requests(svc2._api), "读也不应出现子 did 请求"
print("3. 读写一律发父 did + 对应 siid（无子 did 试探）OK")


# ---------- 4. 云端拒绝时：不记成 None，也不抛异常 ----------
class RejectApi(FakeApi):
    def get_devices_prop(self, params):
        self.reads.append(params)
        return [{"did": q["did"], "siid": q["siid"], "code": -704083036}
                for q in params]


svc3 = make_service(SPEC_3GANG)
svc3._api = RejectApi()
failed_states = svc3.power_states(["p.s3"])
assert "p.s3" not in failed_states, \
    f"读取失败不应记成 None（会被永久跳过，按钮再也不出现）: {failed_states}"

# 真正"无开关能力"仍要显式记 None，这类设备不该反复探测
NO_SWITCH = {"properties": [
    {"name": "temperature", "type": "float", "rw": "r", "method": {"siid": 2, "piid": 1}},
]}
svc4 = make_service(NO_SWITCH)
assert svc4.power_states(["p.s3"]) == {"p.s3": None}, "无 on 属性的设备应记 None"
print("4. 拒绝时不落 None / 无能力时落 None OK")


# ---------- 5. 单次请求数量：轮询延迟的直接指标 ----------
# 8 台子设备（同一父设备的三路 + 其他）应合并成一次请求
svc5 = make_service(SPEC_3GANG)
dids = ["p.s2", "p.s3", "p.s4", "q.s2", "q.s3", "q.s4"]
svc5.power_states(dids)
total = sum(len(b) for b in svc5._api.reads)
assert total == len(dids), f"请求条数应为 {len(dids)}，实际 {total}"
assert len(svc5._api.reads) == 1, f"6 台设备应压成 1 次请求，实际 {len(svc5._api.reads)} 次"
print("5. 多路子设备合并进一次批量请求 OK")

print("SUBDEVICE REGRESSION ALL PASS")
