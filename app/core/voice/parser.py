# SPDX-License-Identifier: GPL-3.0-or-later
"""语音指令解析：「打开客厅的灯」→ 结构化意图。

不做大模型语义理解，原因：这个程序的指令空间是**封闭的**——
动词只有开/关/执行场景，目标只有设备名、房间名、场景名。
规则 + 模糊匹配能做到确定、可离线、可单测；识别错一两个字也能对上。

支持的表达：
    打开/开启/开 + 设备名        「打开主卧吸顶灯」
    关闭/关掉/关 + 设备名        「把客厅灯关掉」
    打开/关闭 + 房间 + 类别      「关闭客厅的灯」（房间内多台 → 批量）
    全部/所有/全屋 + 类别        「全部关灯」「把全屋的灯都关了」
    执行/运行/进入 + 场景名      「执行回家模式」；也支持只报场景名「回家模式」

明确不支持（首版）：亮度/色温/温度等属性控制，返回可读的错误提示。
"""

import difflib
import re
from dataclasses import dataclass, field

# ---- 词表 ----
_ON_WORDS = ("打开", "开启", "启动", "开一下", "亮起", "开")
_OFF_WORDS = ("关闭", "关掉", "关上", "关一下", "熄灭", "关")
_SCENE_WORDS = ("执行场景", "执行", "运行", "进入", "触发", "开始")
_RANGE_WORDS = ("全部", "所有", "全都", "全屋", "一切", "每个")
_LEAD_WORDS = ("帮我", "请", "麻烦", "把", "将", "给我", "现在", "立即", "马上")
_TAIL_WORDS = ("一下", "好吗", "谢谢", "吧", "呢", "啊")
# 属性类说法：首版不支持，直接给出明确提示（不要静默开/关）
_UNSUPPORTED = ("亮度", "色温", "温度", "百分比", "音量", "风速", "%")
# 类别词 → 设备名里的关键词（用于「客厅的灯」这种按类别筛选）
_CATEGORY_WORDS = {
    "灯": ("灯", "照明", "灯泡", "灯带", "吸顶灯", "台灯", "射灯", "筒灯", "吊灯"),
    "开关": ("开关", "面板"),
    "插座": ("插座", "插排"),
    "空调": ("空调",),
    "窗帘": ("窗帘",),
    "风扇": ("风扇", "风机"),
}

_PUNCT = re.compile(r"[\s,，。.、;；:：!！?？\"'“”‘’()（）\[\]【】]+")


def normalize(text: str) -> str:
    """统一大小写/标点/全角数字，便于匹配。"""
    s = (text or "").strip().lower()
    s = _PUNCT.sub("", s)
    s = s.replace("二", "2").replace("三", "3")
    return s


@dataclass
class Intent:
    kind: str                       # power | scene | error
    state: bool | None = None
    devices: list = field(default_factory=list)   # DeviceInfo
    scene: dict | None = None
    batch: bool = False             # 是否属于"批量/多台"（批量确认开关用）
    message: str = ""               # 给用户的回执或错误说明
    raw: str = ""

    def device_names(self) -> list[str]:
        return [getattr(d, "name", "") for d in self.devices]


def _strip_affixes(text: str) -> str:
    s = text
    changed = True
    while changed:
        changed = False
        for w in _LEAD_WORDS:
            if s.startswith(w):
                s = s[len(w):]
                changed = True
        for w in _TAIL_WORDS:
            if s.endswith(w):
                s = s[:-len(w)]
                changed = True
    return s


_MULTI_ON = ("打开", "开启", "启动", "亮起")
_MULTI_OFF = ("关闭", "关掉", "关上", "熄灭")


def detect_state(text: str) -> bool | None:
    """识别开/关动词。

    **只认多字动词 + 排除复合词后的单字动词**：曾经用 `rfind("开")/rfind("关")`
    在全文找，导致「打开开关A」里的「关」被当成关闭动词——说"打开"却执行关闭。
    现在：先找多字动词；没有再看单字，单字要通过「开关/关开」复合词检查。
    """
    hits = [(text.rfind(w), True) for w in _MULTI_ON] + \
           [(text.rfind(w), False) for w in _MULTI_OFF]
    hits = [h for h in hits if h[0] >= 0]
    if hits:
        return max(hits)[1]        # 同时出现取最后说的那个

    single: list[tuple[int, bool]] = []
    for i, ch in enumerate(text):
        if ch not in ("开", "关"):
            continue
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if {prev, nxt} & {"开", "关"}:
            continue               # 「开关」/「关开」是名词，不是动作
        single.append((i, ch == "开"))
    if single:
        return max(single)[1]      # 「全部关灯」「把灯关上」都能识别
    return None


def _has_scene_verb(text: str) -> bool:
    return any(w in text for w in _SCENE_WORDS)


def _strip_verbs(text: str) -> str:
    """只从**句首/句尾**剥动词，绝不替换中间字符。

    全局 replace 曾经把「开关A」这类设备名拆成「A」，也会让
    「把客厅灯的开关打开」丢掉"开关"二字。
    """
    words = sorted(_SCENE_WORDS + _ON_WORDS + _OFF_WORDS, key=len, reverse=True)
    s = text
    changed = True
    while changed:
        changed = False
        for w in words:
            if w and s.startswith(w) and len(s) > len(w):
                s = s[len(w):]
                changed = True
                break
            if w and s.endswith(w) and len(s) > len(w):
                s = s[:-len(w)]
                changed = True
                break
    return s


def _match_category(text: str) -> tuple[str, ...] | None:
    for key, words in _CATEGORY_WORDS.items():
        if key in text:
            return words
    return None


# 灯的判定（硬规则，不做推断）：
# ① 设备名含灯类词；② 云端规格 URN 的设备类型是 light。
# 「这个开关通道后面接的是灯还是风扇」云端没有、也不可能知道——那是用户
# 自己接的线，所以不按房间名/默认命名去猜（猜错会误关风扇、浴霸）；
# 使用说明里引导用户把灯类设备命名成「XX灯」，即可被「所有灯」纳入。
def _spec_device_type(device) -> str:
    """从 MIoT 规格 URN 取设备类型：
    urn:miot-spec-v2:device:<type>:<code>:<model>:<ver>"""
    parts = str(getattr(device, "spec_type", "") or "").split(":")
    if len(parts) >= 4 and parts[0] == "urn" and parts[2] == "device":
        return parts[3].lower()
    return ""


def _is_light(device) -> bool:
    name = normalize(getattr(device, "name", ""))
    if any(w in name for w in _CATEGORY_WORDS["灯"]):
        return True
    return _spec_device_type(device) == "light"


def _matches_category(device, category: tuple[str, ...]) -> bool:
    if any(w in normalize(getattr(device, "name", "")) for w in category):
        return True
    return category == _CATEGORY_WORDS["灯"] and _is_light(device)


def _unclassified_switches(devices) -> list:
    """「所有灯」没纳入的开关类设备：名字不含灯字、类型是墙壁开关。

    数量随指令回执提示用户——漏掉的灯要靠改名解决，不能靠猜。
    多键开关总控（它的子通道已各自在列表里，did 形如 xxx.s1/.s2）不计入，
    否则会把"3 台开关通道"报成 6 台，提示反而误导。
    """
    dids = [str(getattr(d, "did", "")) for d in devices]
    parents = {d.split(".")[0] for d in dids if "." in d}
    return [d for d in devices
            if _spec_device_type(d) == "switch" and not _is_light(d)
            and str(getattr(d, "did", "")) not in parents]


def _name_hit(target: str, name: str) -> bool:
    n = normalize(name)
    return bool(n) and (n in target or target in n)


def _fuzzy(target: str, *candidates: str) -> bool:
    """模糊匹配：识别偶尔错字（「客丁灯」）也能对上。"""
    for c in candidates:
        c = normalize(c)
        if not c:
            continue
        if difflib.SequenceMatcher(None, target, c).ratio() >= 0.62:
            return True
    return False


def _split_targets(target: str) -> list[str]:
    parts = re.split(r"和|跟|与|及|、", target)
    return [p for p in (x.strip() for x in parts) if p]


def _switchable(devices, known_power) -> list:
    """可开关设备集合。

    `known_power` 的语义（与主窗口一致）：
      - 没有这个 key  → 还没探测过，是否有开关能力未知；
      - 值为 None     → 已确认**没有**开关能力；
      - 值为 bool     → 可开关，值即当前状态。

    两个坑都不能踩：
    ① 全空时按它过滤会把所有设备滤掉，用户开口第一句就是"没找到设备"；
    ② 只探测到一部分时按它过滤会**静默漏设备**——说「全部关灯」只关掉已知的
       那几台，回执照样说"已关闭"，用户以为全关了。
    所以：探测覆盖全部设备时才用 None 收敛；否则一律用全量（宁可让个别不
    可开关的设备在回执里报失败，也不能悄悄少关）。
    """
    if not known_power:
        return list(devices)
    probed = sum(1 for d in devices if d.did in known_power)
    if probed < len(devices):
        return list(devices)
    return [d for d in devices if known_power.get(d.did) is not None]


def _devices_in_rooms(devices, rooms: set[str]) -> list:
    return [d for d in devices if normalize(getattr(d, "room_name", "")) in rooms]


def resolve_devices(target: str, devices: list, known_power=None) -> tuple[list, bool]:
    """把目标描述解析成设备列表；返回 (设备, 是否批量)。"""
    pool = _switchable(devices, known_power)
    if not pool:
        return [], False

    range_all = any(w in target for w in _RANGE_WORDS)
    category = _match_category(target)

    # 「全部/所有」：按类别过滤（全部关灯 → 只关灯）。
    # 类别过滤结果为空时必须返回空（报"没有找到"），绝不能退回整个 pool——
    # 否则「全部关灯」在没有灯时会连插座、空调一起关掉
    if range_all:
        if category:
            hit = [d for d in pool if _matches_category(d, category)]
            return hit, True
        return pool, True

    # 先按设备名直接命中：说「打开客厅灯带」就应当只开那一台，
    # 不能被"房间+类别"放大成客厅所有灯
    exact = [d for d in pool if normalize(getattr(d, "name", "")) == target]
    if exact:
        return exact, len(exact) > 1
    contains = [d for d in pool if _name_hit(target, getattr(d, "name", ""))]
    if contains:
        return contains, len(contains) > 1

    # 目标里直接带房间名（「客厅的灯」）。
    # 类别过滤结果为空时不能退回"整个房间"——「关闭客厅的插座」若插座不在
    # 可开关池里，退回会把客厅所有设备全关掉；空结果应继续走后续匹配，
    # 最终报"没有找到"，绝不误执行
    rooms = {normalize(getattr(d, "room_name", "")) for d in pool}
    hit_rooms = {r for r in rooms if r and r in target}
    if hit_rooms:
        in_room = _devices_in_rooms(pool, hit_rooms)
        if category:
            filtered = [d for d in in_room if _matches_category(d, category)]
            if filtered:
                return filtered, len(filtered) > 1
            # 类别与房间组合没命中：只报房间也大概率不是用户想要的，
            # 例如「书房的插座」在书房没有插座时，宁可失败也不全关书房
            return [], False
        if in_room:
            return in_room, len(in_room) > 1

    # 逐段匹配（「客厅灯和书房灯」）
    found: list = []
    seen: set[str] = set()
    for seg in _split_targets(target) or [target]:
        if not seg:
            continue
        # 先精确/包含匹配设备名
        hits = [d for d in pool if _name_hit(seg, getattr(d, "name", ""))]
        if not hits:
            # 再按"房间+类别"匹配（段里只写房间、或房间+类别）
            seg_rooms = {r for r in rooms if r and r in seg}
            if seg_rooms:
                in_room = _devices_in_rooms(pool, seg_rooms)
                cat = _match_category(seg) or category
                if cat:
                    in_room = [d for d in in_room if _matches_category(d, cat)]
                hits = in_room
        if not hits:
            # 最后用模糊匹配兜底
            hits = [d for d in pool
                    if _fuzzy(seg, getattr(d, "name", ""),
                              f"{getattr(d, 'room_name', '')}{getattr(d, 'name', '')}")]
        for d in hits:
            if d.did not in seen:
                seen.add(d.did)
                found.append(d)
    return found, len(found) > 1


def _scene_name(scene) -> str:
    """兼容 dict 与 SceneInfo（main_window 传的是 dataclass，曾因此
    每句话解析都抛 AttributeError → 全部回「语音指令解析失败」）。"""
    if isinstance(scene, dict):
        return str(scene.get("name") or "")
    return str(getattr(scene, "name", "") or "")


def _scene_id(scene) -> str:
    if isinstance(scene, dict):
        return str(scene.get("scene_id") or "")
    return str(getattr(scene, "scene_id", "") or "")


def _scene_home_id(scene) -> str:
    if isinstance(scene, dict):
        return str(scene.get("home_id") or "")
    return str(getattr(scene, "home_id", "") or "")


def _match_scene(text: str, scenes: list) -> object | None:
    best = None
    best_len = 0
    for scene in scenes or []:
        name = normalize(_scene_name(scene))
        if not name:
            continue
        if name in text or text in name:
            if len(name) > best_len:
                best, best_len = scene, len(name)
    if best is not None:
        return best
    for scene in scenes or []:
        name = normalize(_scene_name(scene))
        if name and _fuzzy(text, name):
            return scene
    return None


def parse(raw: str, devices: list, scenes: list | None = None,
          known_power: dict | None = None) -> Intent:
    """把一句话解析成意图。永不抛异常，失败时返回 kind="error" 并带可读原因。"""
    text = normalize(raw)
    if not text:
        return Intent(kind="error", message="没有听清，请再说一次", raw=raw)

    # 属性类说法先拦掉（「调到 26 度」这类没有开关动词，也要给出准确提示）
    if any(w in text for w in _UNSUPPORTED) or re.search(r"\d+度", text):
        return Intent(kind="error", raw=raw,
                      message="暂时只支持「开/关设备」和「执行场景」，还不能调节亮度或温度")

    # 场景优先：既不要求动词，也不与设备名冲突（场景名通常更长、更具体）
    scene = _match_scene(text, scenes or [])
    if scene is not None and (_has_scene_verb(text) or detect_state(text) is None):
        return Intent(kind="scene", scene=scene, raw=raw,
                      message=f"已执行：{_scene_name(scene)}")

    state = detect_state(text)
    if state is not None:
        stripped = _strip_affixes(text)
        target = _strip_verbs(stripped)
        if not target:
            # 设备名本身以动词字开头/结尾时会被剥空：退回未剥的文本再试一次
            target = stripped
        if not target:
            return Intent(kind="error", raw=raw, message="没听清要控制哪个设备或场景")
        hits, batch = resolve_devices(target, devices, known_power)
        if hits:
            verb = "打开" if state else "关闭"
            names = "、".join(str(getattr(d, "name", "")) for d in hits[:3])
            more = f" 等 {len(hits)} 台" if len(hits) > 3 else ""
            msg = f"已{verb}：{names}{more}"
            # 群体指令（全部/所有）且按类别执行时，把没纳入的开关数量说清楚：
            # 漏的灯只能靠给设备改名解决，先让用户知道有几台没算进来
            category = _match_category(target)
            if category is not None and any(w in target for w in _RANGE_WORDS):
                rest = len(_unclassified_switches(devices))
                if rest:
                    msg += f"（另有 {rest} 台开关名不含「{category[0]}」未纳入）"
            return Intent(kind="power", state=state, devices=hits, batch=batch,
                          raw=raw, message=msg)
        # 设备没匹配上：场景却精确命中（如「离家关灯」这类自带开关动词的场景名）
        # → 执行场景，而不是丢给用户一句"没有找到设备"
        if scene is not None:
            return Intent(kind="scene", scene=scene, raw=raw,
                          message=f"已执行：{_scene_name(scene)}")
        return Intent(kind="error", raw=raw, message=f"没有找到「{target}」对应的设备")

    # 没有开关动词：场景命中就执行，否则给出可读提示
    if scene is not None:
        return Intent(kind="scene", scene=scene, raw=raw,
                      message=f"已执行：{_scene_name(scene)}")
    return Intent(kind="error", raw=raw,
                  message="没听清要开还是关，请说「打开客厅灯」或「执行回家模式」")
