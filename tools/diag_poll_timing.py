"""量化开关状态轮询耗时：整批 / 子设备 / 单次请求。只读，不改设备。"""

import time

from app import setup_logging
from app.core.service import MijiaService

setup_logging()
svc = MijiaService()
if not svc.login_status():
    print("未登录")
    raise SystemExit(1)

devices = svc.list_devices()
online = [d for d in devices if d.online]
subs = [d for d in online if ".s" in d.did]
print(f"设备总数 {len(devices)}，在线 {len(online)}，虚拟子设备 {len(subs)}")

# 统计真实网络请求次数与耗时
calls = []
raw_get = svc._api.get_devices_prop


def counted(params):
    t0 = time.perf_counter()
    ret = raw_get(params)
    calls.append((len(params), time.perf_counter() - t0))
    return ret


svc._api.get_devices_prop = counted


def bench(label, dids, rounds=3):
    svc._sub_prefer = {}          # 每轮都当作"还没学"，模拟最坏情况
    times = []
    for _ in range(rounds):
        calls.clear()
        t0 = time.perf_counter()
        svc.power_states(dids)
        times.append(time.perf_counter() - t0)
        detail = ", ".join(f"{n}条/{d*1000:.0f}ms" for n, d in calls)
        print(f"  {label}: {times[-1]*1000:.0f}ms  请求[{detail}]")
    return times


print("\n=== 已学习后（第二次及以后，和真实运行一致）===")
svc._sub_prefer = {}
svc.power_states([d.did for d in subs])   # 先学一遍
calls.clear()
t0 = time.perf_counter()
svc.power_states([d.did for d in subs])
print(f"  仅子设备({len(subs)} 台): {(time.perf_counter()-t0)*1000:.0f}ms  "
      f"请求[{', '.join(f'{n}条/{d*1000:.0f}ms' for n, d in calls)}]")

svc._sub_prefer = {}
svc.power_states([d.did for d in online])
calls.clear()
t0 = time.perf_counter()
svc.power_states([d.did for d in online])
print(f"  全部在线({len(online)} 台): {(time.perf_counter()-t0)*1000:.0f}ms  "
      f"请求[{', '.join(f'{n}条/{d*1000:.0f}ms' for n, d in calls)}]")

print("\n=== 未学习时（应用刚启动的第一次）===")
bench("仅子设备", [d.did for d in subs], rounds=2)
bench("全部在线", [d.did for d in online], rounds=2)
