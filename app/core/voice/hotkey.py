# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows 全局热键（按住说话用）。

实现方式：**RegisterHotKey 独占按键 + 轮询键态判断按下/松开**。

- RegisterHotKey 的作用是"把这个键从别的软件手里拿走"，避免说话时往
  当前输入框里打出一个 F9/Space；它本身只给"按下"事件（WM_HOTKEY），
  拿不到松开；
- 全局键盘钩子（WH_KEYBOARD_LL）能拿松开，但要介入所有进程的键盘消息，
  风险大；
- 实测在 Qt6/PySide6 里 WM_HOTKEY 未必能送到 QAbstractNativeEventFilter
  （线程消息与注入按键都收不到），所以按下/松开统一用 40ms 轮询
  GetAsyncKeyState 判断——只在启用语音时轮询，CPU 开销可忽略，且不受
  消息循环实现影响。

热键被别的软件占用时 RegisterHotKey 返回 0，设置界面据此提示换键。
"""

import ctypes
import ctypes.wintypes as wt
import logging

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

_HOTKEY_ID = 0xB10C
# 探测用的独立 id：探测热键是否空闲时不能碰自己的 _HOTKEY_ID，
# 否则会把"本程序已注册"误判成"被其他软件占用"
_PROBE_ID = 0xB10D

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008

_MOD_ALIASES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN,
}
_MOD_ORDER = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"),
              (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))
# 修饰键 → 对应的 GetAsyncKeyState 虚拟键码（判断"此刻按着哪些修饰键"）
_VK_MOD_SETS = ((MOD_CONTROL, (0x11,)),              # VK_CONTROL
                (MOD_ALT, (0x12,)),                  # VK_MENU
                (MOD_SHIFT, (0x10,)),                # VK_SHIFT
                (MOD_WIN, (0x5B, 0x5C)))             # VK_LWIN / VK_RWIN

_VK_NAMES = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
    # QKeySequenceEdit 产出的名字：不认这些，用户录完键会注册失败
    "pgup": 0x21, "pgdn": 0x22, "pageup": 0x21, "pagedown": 0x22,
    "del": 0x2E, "ins": 0x2D, "return": 0x0D, "esc": 0x1B,
}
for _i in range(1, 25):
    _VK_NAMES[f"f{_i}"] = 0x6F + _i


def parse_spec(spec: str) -> tuple[int, int]:
    """'Ctrl+Alt+Space' → (修饰键位掩码, 虚拟键码)；非法时抛 ValueError。"""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        raise ValueError("热键为空")
    *mods_part, key = parts
    mods = 0
    for m in mods_part:
        if m not in _MOD_ALIASES:
            raise ValueError(f"不认识的修饰键: {m}")
        mods |= _MOD_ALIASES[m]
    if key in _MOD_ALIASES:
        raise ValueError("热键需要一个普通键（如 F9、Space）")
    if key in _VK_NAMES:
        vk = _VK_NAMES[key]
    elif len(key) == 1 and key.isalpha():
        vk = ord(key.upper())
    elif len(key) == 1 and key.isdigit():
        vk = ord(key)
    else:
        raise ValueError(f"无法识别的按键: {key}")
    return mods, vk


def format_spec(spec: str) -> str:
    """规范化为展示用文本（Ctrl+Alt+Space）；解析失败原样返回。"""
    try:
        mods, vk = parse_spec(spec)
    except ValueError:
        return spec
    names = [name for bit, name in _MOD_ORDER if mods & bit]
    key = next((k for k, v in _VK_NAMES.items() if v == vk), None)
    if key is None:
        key = chr(vk) if 32 < vk < 127 else f"0x{vk:02X}"
    key = {"space": "Space", "enter": "Enter", "tab": "Tab", "esc": "Esc",
           "pageup": "PageUp", "pagedown": "PageDown"}.get(
               key, key.upper() if len(key) == 1 else key.title())
    return "+".join(names + [key])


def probe_available(spec: str, current: str = "") -> bool:
    """试注册探测热键是否可用，立即还原（给设置页保存前预检冲突用）。

    `current` 传"本程序正在使用的热键"：与它相同时直接判可用，不去碰系统
    注册表——RegisterHotKey 是独占的，对本程序已持有的键再注册必然失败，
    会被误判成"被其他软件占用"（现场：设置 F8 后保存，提示 F8 被占用）。

    探测用 `_PROBE_ID` 而不是正式的 `_HOTKEY_ID`：避免与自己的注册状态纠缠。
    """
    try:
        mods, vk = parse_spec(spec)
    except ValueError:
        return False
    if current:
        try:
            if parse_spec(current) == (mods, vk):
                return True          # 就是本程序正在用的键，当然可用
        except ValueError:
            pass
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return False
    if user32.RegisterHotKey(None, _PROBE_ID, mods, vk):
        user32.UnregisterHotKey(None, _PROBE_ID)
        return True
    return False


class GlobalHotkey(QObject):
    """按住说话的热键：按下发 pressed，松开发 released。"""

    pressed = Signal()
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mods = 0
        self._vk = 0
        self._registered = False
        self._down = False
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        except OSError:            # 非 Windows
            self._user32 = None
        self._poll = QTimer(self)
        self._poll.setInterval(40)
        self._poll.timeout.connect(self._tick)
        # 热键保活：其他语音类软件（豆包输入法等）退出/重启自己的语音
        # 功能时可能抢注同一热键，把我们的注册顶掉；之后 GetAsyncKeyState
        # 仍能读到键态（所以轮询逻辑不变），但表现为"用过别的语音输入
        # 软件后就唤不醒"。定期重新注册（幂等）自动夺回
        self._keepalive = QTimer(self)
        self._keepalive.setInterval(5000)
        self._keepalive.timeout.connect(self._reassert)
        self._keepalive_started = False

    # ---- 注册/注销 ----

    def register(self, spec: str) -> bool:
        """注册热键；被占用或非法返回 False（调用方据此提示换键）。"""
        self.unregister()
        if self._user32 is None:
            logger.warning("非 Windows 环境，全局热键不可用")
            return False
        try:
            mods, vk = parse_spec(spec)
        except ValueError as exc:
            logger.warning("热键解析失败 %r: %s", spec, exc)
            return False
        if not self._user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
            logger.warning("热键注册失败（可能被其他软件占用）: %s", spec)
            return False
        self._mods, self._vk = mods, vk
        self._registered = True
        self._down = False
        self._poll.start()
        if not self._keepalive_started:
            self._keepalive.start()
            self._keepalive_started = True
        logger.info("全局热键已注册: %s", format_spec(spec))
        return True

    def unregister(self) -> None:
        self._poll.stop()
        self._keepalive.stop()
        self._keepalive_started = False
        if self._registered and self._user32 is not None:
            self._user32.UnregisterHotKey(None, _HOTKEY_ID)
        self._registered = False
        self._down = False

    def is_registered(self) -> bool:
        return self._registered

    # ---- 键态轮询 ----

    def _mods_down(self) -> int:
        """此刻真正按着的修饰键位掩码。"""
        state = 0
        for bit, vks in _VK_MOD_SETS:
            if any(self._user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks):
                state |= bit
        return state

    def _is_pressed(self) -> bool:
        if self._user32 is None:
            return False
        # 高字节置位 = 当前按着
        if not (self._user32.GetAsyncKeyState(self._vk) & 0x8000):
            return False
        # **必须恰好按下注册时的修饰键组合**：RegisterHotKey 只"独占"这个组合，
        # 并不会屏蔽单键。只判主键的话，注册 Ctrl+Space 后在任何输入框里打个
        # 空格都会触发录音（药丸乱弹、麦克风被抢）。单键热键（F9）则要求
        # 没按修饰键——与 RegisterHotKey 的匹配语义一致。
        return self._mods_down() == self._mods

    def _tick(self) -> None:
        pressed = self._is_pressed()
        if pressed and not self._down:
            self._down = True
            self.pressed.emit()
        elif not pressed and self._down:
            self._down = False
            self.released.emit()

    def _reassert(self) -> None:
        """保活：**非破坏性**地确认注册还在自己手里，丢了才收回。

        旧写法是"先注销再注册"，那会每 5 秒制造一个"组合键空闲"的窗口：
        别的软件（或本程序的另一个实例）正好在这时抢注，我们就永久丢失，
        而且 RegisterHotKey 复用同一 id 也无法自愈。现在用独立的 `_PROBE_ID`
        试注册同一个组合键：成功=键当前空闲（说明我们的注册确实丢了），
        失败=仍被持有（我们或别人），此时什么都不做。
        """
        if not self._registered or self._user32 is None:
            return
        try:
            free = self._user32.RegisterHotKey(
                None, _PROBE_ID, self._mods, self._vk)
        except Exception:
            return
        if not free:
            return                    # 仍被持有（正常情况），不打扰
        # 组合键空闲 = 我们的注册已经丢了：立刻收回
        self._user32.UnregisterHotKey(None, _PROBE_ID)
        if self._user32.RegisterHotKey(None, _HOTKEY_ID, self._mods, self._vk):
            logger.info("热键注册曾丢失，已自动收回: %s", format_spec(
                f"{self._mods:x}+{self._vk:x}"))
            return
        logger.warning("热键被其他软件占用，暂无法收回（键态轮询仍在工作）")
