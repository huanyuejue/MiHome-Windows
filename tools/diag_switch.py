# SPDX-License-Identifier: GPL-3.0-or-later
"""诊断脚本：多键面板的虚拟子设备为什么控制不了。

只读，不改任何设备状态。用法（在项目根目录）：

    .venv\\Scripts\\python.exe -X utf8 -m tools.diag_switch

会打印：
  1. 账号下的设备里有哪些「虚拟子设备」（did 形如 <父did>.sN）
  2. 该型号规格里 on 属性分布在哪些 siid
  3. 用「子 did」「父 did」分别读 siid 2/3/4 的原始云端返回（code/message）

把输出整段发出来即可定位。
"""

import json
import sys

from app import setup_logging
from app.core.service import MijiaService

# 单次请求最多测几路、几组家庭，避免输出过长
_MAX_FAMILIES = 3


def main() -> int:
    setup_logging()
    svc = MijiaService()
    if not svc.login_status():
        print("未登录：请先正常启动一次程序并完成扫码登录，再运行本脚本。")
        return 1

    devices = svc.list_devices()
    print(f"设备总数：{len(devices)}")

    families: dict[str, list] = {}
    for dev in devices:
        if ".s" in dev.did:
            parent = dev.did.split(".s")[0]
            families.setdefault(parent, []).append(dev)

    if not families:
        print("没有发现虚拟子设备（did 里不含 .sN），本脚本不适用。")
        return 0

    print(f"发现 {len(families)} 组多键面板：\n")

    for parent_did, children in list(families.items())[:_MAX_FAMILIES]:
        parent = next((d for d in devices if d.did == parent_did), None)
        model = (parent.model if parent else children[0].model)
        print("=" * 72)
        print(f"父设备 did={parent_did} name={parent.name if parent else '?'!r} "
              f"model={model!r}")
        for child in children:
            print(f"   子设备 did={child.did} name={child.name!r} "
                  f"online={child.online}")
        print(f"   注：子设备的 spec_type / model 与父设备是否一致 —— "
              f"{'一致' if parent and children[0].model == parent.model else '不一致'}")

        # 2. 规格里的 on 属性
        spec_map = svc._specs_for([d.did for d in children] + [parent_did])
        info = next((v for v in spec_map.values() if v), None)
        if not info:
            print("   !! 取不到规格（spec_type 为空或拉取失败）——这本身可能就是原因")
            print(f"   父 spec_type={getattr(parent, 'spec_type', '?')!r}")
            continue
        print("   规格里的 on 属性：")
        for prop in info.get("properties", []):
            name = prop.get("name", "")
            if name == "on" or str(name).startswith("on"):
                m = prop.get("method", {})
                print(f"     name={name:<6} type={prop.get('type'):<5} "
                      f"rw={prop.get('rw'):<3} siid={m.get('siid')} piid={m.get('piid')}")

        # 3. 原始读返回：子 did 与父 did 各试 siid 2/3/4
        print("   原始读取返回（只读）：")
        for label, did in ([("子", c.did) for c in children]
                           + [("父", parent_did)]):
            for siid in (2, 3, 4):
                query = [{"did": did, "siid": siid, "piid": 1}]
                try:
                    ret = svc._api.get_devices_prop(query)
                except Exception as exc:
                    print(f"     {label} {did} siid={siid} -> 异常 {type(exc).__name__}: {exc}")
                    continue
                item = ret[0] if isinstance(ret, list) and ret else ret
                if isinstance(item, dict):
                    print(f"     {label} {did} siid={siid} -> "
                          f"code={item.get('code')} value={item.get('value')!r} "
                          f"message={item.get('message')!r}")
                else:
                    print(f"     {label} {did} siid={siid} -> 非预期返回 {item!r}")
        print()

    print("=" * 72)
    print("判读提示：")
    print("  · 子 did + 某个 siid 返回 code=0 → 该组合可用，若程序仍不显示按钮，")
    print("    说明是我这边的通道映射或状态写回有问题（把那行发我）。")
    print("  · 子 did 全部非 0、父 did 某个 siid 返回 0 → 云端只认父 did。")
    print("  · 全部非 0 → 这类虚拟设备可能只支持通过父设备的动作/场景控制，")
    print("    需要换实现路线（届时告知我 code/message 原文）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
