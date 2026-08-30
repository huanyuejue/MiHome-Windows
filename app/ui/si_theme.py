# SPDX-License-Identifier: GPL-3.0-or-later
# MiHome-Windows: 米家设备的 Windows 桌面控制端
# Copyright (C) 2026 MiHome-Windows contributors
"""主题中枢：深/浅双调色板、动态 SiColors 代理、全局 QSS 生成。

视觉语言：深黑底立体卡片 + 米家青绿主题色。主题色（THEME 系列）
与绿底上的文字在两种主题下完全一致；其余语义色按当前调色板取值。

用法约定：
- 界面代码一律 `from app.ui.si_theme import SiColors` 后取
  `SiColors.CARD` 等语义色——SiColors 经元类动态代理当前调色板，
  主题切换后新构建的控件自动取到新值；
- 内联样式是构造时求值的 f-string，已显示的界面在切换主题后由
  调用方重建（卡片网格/托盘行/工作台均为可重建结构）；
- 全局 QSS 由 build_qss() 生成，切换时整块替换。
"""

from string import Template
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSizePolicy

from app.siui.components.button import SiSwitchRefactor, SiToggleButtonRefactor

if TYPE_CHECKING:
    from PySide6.QtWidgets import QComboBox

    from app.siui.components.label import SiLabelRefactor

# ----------------------------------------------------------------------------
# 主题色：米家青绿，两种主题下不变；绿底上的文字同理
# ----------------------------------------------------------------------------
THEME = "#3dbba4"
THEME_HOVER = "#5fd0ba"
THEME_DIM = "#2a8a7a"
THEME_CHECKED_HOVER = "#4ccdb5"
ON_THEME_TEXT = "#0b0b0e"      # 绿底上的深色文字
SWITCH_THUMB = "#1C191F"       # 开关滑块深色拇指
DANGER = "#c0392b"             # 危险红（关闭按钮 hover 等）
DANGER_TEXT = "#e57373"
WHITE = "#ffffff"

# ----------------------------------------------------------------------------
# 深/浅调色板：语义键 -> 色值。新增颜色一律进这里，不要在界面代码写死
# ----------------------------------------------------------------------------
PALETTES: dict[str, dict[str, str]] = {
    "dark": dict(
        WINDOW_BG="#1e1f24",
        TITLE_BAR_BG="#1a1b20",
        TITLE_BAR_BORDER="#252630",
        CARD="#2a2c32",
        CARD_HOVER="#333540",
        CARD_BORDER_HOVER="#404252",
        SURFACE="#2e2f36",          # 输入框/小按钮/Toast 底
        SURFACE_PRESSED="#1f1f23",  # 电源钮忙碌底
        PRESSED="#17171a",          # 按下态
        BTN_PRESSED="#25262e",      # 次级按钮按下
        BTN_HOVER="#3a3b44",
        LINE="#35363f",
        SCROLLBAR="#35363f",
        SCROLLBAR_HOVER="#4a4b55",
        TEXT_PRIMARY="#e8e8ea",
        TEXT_SECONDARY="#8a8b93",
        TEXT_MUTED="#7c7d83",
        TEXT_SUBTLE="#9a9aa0",
        TEXT_DISABLED="#555558",
        TEXT_FAINT="#444448",
        OFFLINE_CARD="#1a1b20",
        OFFLINE_TEXT="#6e6e78",
        OFFLINE_SUB="#5a5b64",
        ICON_DIM="#7a7b83",
        ICON_MUTED="#a0a0aa",
        HOME_HOVER="#b9babf",
        HOME_PRESSED="#85868b",
        THUMB="#ffffff",               # 滑块拇指：深色模式白
        STATE_OFF="#40434f",
        STATE_UNKNOWN_BG="#222227",
        STATE_UNKNOWN_BORDER="#3a3a40",
        STATE_UNKNOWN_HOVER="#2a2a30",
        ERROR_TEXT="#e06060",
        DEL_TEXT="#9a6a6a",
        DEL_BORDER="#3a2f2f",
        DEL_BORDER_HOVER="#6b2f2f",
        WARN_BG="#3a2d12",
        WARN_BORDER="#5a4220",
        WARN_TEXT="#e8c87a",
    ),
    "light": dict(
        WINDOW_BG="#f2f3f5",
        TITLE_BAR_BG="#ececef",
        TITLE_BAR_BORDER="#dcdfe4",
        CARD="#ffffff",
        CARD_HOVER="#f4f5f7",
        CARD_BORDER_HOVER="#d0d3da",
        SURFACE="#eef0f3",
        SURFACE_PRESSED="#e4e6ea",
        PRESSED="#d9dade",
        BTN_PRESSED="#dfe1e6",
        BTN_HOVER="#e9eaee",
        LINE="#e3e4e8",
        SCROLLBAR="#c9cbd2",
        SCROLLBAR_HOVER="#b0b3bc",
        TEXT_PRIMARY="#17181c",
        TEXT_SECONDARY="#6b6d76",
        TEXT_MUTED="#85878e",
        TEXT_SUBTLE="#8a8b93",
        TEXT_DISABLED="#b4b6bc",
        TEXT_FAINT="#c6c8ce",
        OFFLINE_CARD="#f0f0f2",
        OFFLINE_TEXT="#a0a2a8",
        OFFLINE_SUB="#b6b8bf",
        ICON_DIM="#85878e",
        ICON_MUTED="#9a9aa0",
        HOME_HOVER="#55565c",
        HOME_PRESSED="#75767c",
        THUMB="#9a9ea6",               # 滑块拇指：浅色模式中深灰（白底上清晰可见）
        STATE_OFF="#c4c8d0",
        STATE_UNKNOWN_BG="#ececef",
        STATE_UNKNOWN_BORDER="#c9cbd2",
        STATE_UNKNOWN_HOVER="#e2e3e8",
        ERROR_TEXT="#c62828",
        DEL_TEXT="#b05a5a",
        DEL_BORDER="#e8c5c5",
        DEL_BORDER_HOVER="#e09b9b",
        WARN_BG="#fdf3d8",
        WARN_BORDER="#e8d49a",
        WARN_TEXT="#8a6d1f",
    ),
}

# 当前主题；启动时由 theme_service.set_theme 写定
_current: str = "dark"


def current_theme() -> str:
    """当前主题名："dark" | "light"。"""
    return _current


def is_dark() -> bool:
    return _current == "dark"


def set_theme(theme: str) -> None:
    """切换当前调色板（仅改取色；全局刷新由 theme_service 编排）。"""
    global _current
    if theme not in PALETTES:
        raise ValueError(f"未知主题: {theme}")
    _current = theme


def palette() -> dict[str, str]:
    return PALETTES[_current]


class _PaletteMeta(type):
    """让 SiColors.XXX 动态代理当前调色板；主题色与固定色为真属性。"""

    def __getattr__(cls, name: str) -> str:
        try:
            return PALETTES[_current][name]
        except KeyError:
            raise AttributeError(
                f"SiColors 无此语义色: {name}，可用键见 PALETTES") from None


class SiColors(metaclass=_PaletteMeta):
    """语义色代理。THEME 系列与固定色是硬编码真属性，两主题共用。"""

    THEME = THEME
    THEME_HOVER = THEME_HOVER
    THEME_DIM = THEME_DIM
    THEME_CHECKED_HOVER = THEME_CHECKED_HOVER
    ON_THEME_TEXT = ON_THEME_TEXT
    SWITCH_THUMB = SWITCH_THUMB
    DANGER = DANGER
    DANGER_TEXT = DANGER_TEXT
    WHITE = WHITE


# ----------------------------------------------------------------------------
# 全局 QSS 生成：模板占位符 $KEY，由当前调色板填充
# ----------------------------------------------------------------------------
_QSS_TEMPLATE = Template("""
* {
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 10pt;
    color: $TEXT_PRIMARY;
}

QMainWindow, QDialog, QMessageBox {
    background: $WINDOW_BG;
}

QWidget#contentHost, QWidget#gridHost {
    background: $WINDOW_BG;
}

/* ---------- 内嵌标题栏（无边框窗口） ---------- */

QFrame#titleBar {
    background: $TITLE_BAR_BG;
    border-bottom: 1px solid $TITLE_BAR_BORDER;
}

QLabel#titleBarText {
    font-size: 9pt;
    font-weight: 600;
    color: $TEXT_SUBTLE;
    background: transparent;
}

QPushButton#titleBarBtn,
QPushButton#titleBarClose,
QPushButton#titleBarMax {
    background: transparent;
    border: none;
    border-radius: 4px;
    color: $TEXT_SUBTLE;
    font-family: "Segoe MDL2 Assets";
    font-size: 9pt;
}

QPushButton#titleBarBtn:hover {
    background: $SURFACE;
    color: $TEXT_PRIMARY;
}

QPushButton#titleBarMax:hover {
    background: $SURFACE;
    color: $TEXT_PRIMARY;
}

QPushButton#titleBarClose:hover {
    background: $DANGER;
    color: $WHITE;
}

QLabel#statusHint {
    color: $TEXT_SUBTLE;
    background: transparent;
    font-size: 9pt;
    padding: 4px 0;
}

/* ---------- 顶栏家庭切换器 ---------- */

QPushButton#homeSwitcher {
    background: transparent;
    border: none;
    padding: 2px 0;
    font-size: 17pt;
    font-weight: 600;
    color: $TEXT_PRIMARY;
    text-align: left;
}

QPushButton#homeSwitcher:hover {
    color: $HOME_HOVER;
}

QPushButton#homeSwitcher:pressed {
    color: $HOME_PRESSED;
}

/* ---------- 详情页功能卡片（电源行/滑块/模式/输入/只读） ---------- */

QFrame#propCard {
    background: $CARD;
    border: 1px solid $LINE;
    border-radius: 14px;
}
QFrame#propCard:hover {
    background: $CARD_HOVER;
    border-color: $CARD_BORDER_HOVER;
}

QPushButton#propCardToggle {
    background: transparent;
    border: none;
    color: $THEME;
    font-size: 9pt;
    padding: 2px 6px;
}

QPushButton#propCardToggle:hover {
    color: $THEME_HOVER;
}

/* ---------- 原生菜单（家庭切换/更多操作） ---------- */

QMenu#appMenu {
    background: $CARD;
    border: 1px solid $LINE;
    border-radius: 8px;
    padding: 4px;
}

QMenu#appMenu::item {
    padding: 5px 26px 5px 6px;
    border-radius: 6px;
    font-size: 10pt;
    text-align: left;
    color: $TEXT_PRIMARY;
}

QMenu#appMenu::item:selected {
    background: $BTN_HOVER;
}

QMenu#appMenu::item:checked {
    color: $THEME;
}

QMenu#appMenu::indicator {
    width: 12px;
    height: 12px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    right: 6px;
}

/* ---------- 详情面板 ---------- */

QLabel#panelTitle {
    font-size: 15pt;
    font-weight: 600;
    color: $TEXT_PRIMARY;
}

QLabel#panelSubtitle {
    color: $TEXT_SECONDARY;
    font-size: 9pt;
}

QScrollArea {
    background: transparent;
}

QLabel {
    background: transparent;
}

/* ---------- 原生数值框（详情面板输入行） ---------- */

QSpinBox, QDoubleSpinBox {
    background: $CARD;
    border: 1px solid $LINE;
    border-radius: 8px;
    padding: 5px 10px;
    selection-background-color: $THEME;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $THEME;
}

/* ---------- 滚动条 ---------- */

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: $SCROLLBAR;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: $SCROLLBAR_HOVER;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0;
    background: none;
}

QScrollBar:horizontal {
    height: 0;
    background: transparent;
}

/* ---------- 通知浮层 ---------- */

QFrame#toastCard {
    background: $SURFACE;
    border: 1px solid $BTN_HOVER;
    border-radius: 10px;
}

/* ---------- 小爱语音悬浮球 ---------- */

QPushButton#voiceBall {
    background: $THEME;
    border: none;
    border-radius: 27px;
}
QPushButton#voiceBall:hover {
    background: $THEME_CHECKED_HOVER;
}
QPushButton#voiceBall:pressed {
    background: $THEME_DIM;
}

QFrame#voicePanel {
    background: $SURFACE;
    border: 1px solid $LINE;
    border-radius: 12px;
}
""")


def build_qss() -> str:
    """由当前调色板生成全局 QSS。"""
    values = dict(PALETTES[_current])
    # 模板里用到的固定主题色
    values.update(
        THEME=THEME, THEME_HOVER=THEME_HOVER, THEME_DIM=THEME_DIM,
        THEME_CHECKED_HOVER=THEME_CHECKED_HOVER,
        DANGER=DANGER, WHITE=WHITE,
    )
    # 缺键早失败：模板新增占位符而某套调色板漏配时给出可读报错
    missing = set(_QSS_TEMPLATE.get_identifiers()) - set(values)
    assert not missing, f"QSS 模板占位符缺少调色板键: {missing}"
    return _QSS_TEMPLATE.substitute(values)


# ----------------------------------------------------------------------------
# siui 全局色组联动：SiCapsuleComboBox 弹层、tooltip 等内部件取色自这里
# ----------------------------------------------------------------------------

def sync_siui_colors() -> None:
    """把 siui 全局色组切到当前主题，并覆盖主题 token 为米家绿。

    siui 内置 Bright/Dark 两套色组；覆盖后 siui 内部件的青绿与
    应用主题色完全一致（内置 Bright 组的青绿偏亮，不是米家绿）。
    """
    from app.siui.core import SiColor, SiGlobal
    from app.siui.gui.color_group import BrightColorGroup, DarkColorGroup

    # 原地链到新色组而非重新绑定：siui 控件构造时缓存了色组引用，
    # 重新绑定会让未重建的旧控件继续用旧调色板
    group = SiGlobal.siui.colors
    reference = getattr(group, "reference", None)
    fresh = DarkColorGroup() if _current == "dark" else BrightColorGroup()
    fresh.assign(SiColor.THEME, THEME)
    fresh.assign(SiColor.THEME_TRANSITION_A, THEME)
    fresh.assign(SiColor.THEME_TRANSITION_B, THEME_HOVER)
    fresh.assign(SiColor.SVG_THEME, THEME)
    group.valid_state = False  # 本组命中即转 reference，绕过陈旧覆盖
    group.reference = fresh
    if reference is not None:
        fresh.reference = reference
    # 图标包默认色：浅色底上用深灰
    SiGlobal.siui.iconpack.setDefaultColor("#D1CBD4" if _current == "dark" else "#5f6368")


# ----------------------------------------------------------------------------
# siui 控件工厂（仅保留项目实际用到的）
# ----------------------------------------------------------------------------

def themed_switch() -> SiSwitchRefactor:
    """bool 属性用的开关，开启态轨道为主题色。"""
    switch = SiSwitchRefactor()
    switch.style_data.background_color_starting = QColor(SiColors.THEME)
    switch.style_data.background_color_ending = QColor(SiColors.THEME)
    switch.style_data.thumb_color_checked = QColor(SiColors.SWITCH_THUMB)
    switch.update()
    return switch


def themed_label(text: str = "", color: str | None = None) -> "SiLabelRefactor":
    """自绘文字标签，颜色不受全局 QSS 影响；缺省为主文字色。"""
    from app.siui.components.label import SiLabelRefactor

    label = SiLabelRefactor()
    label.setText(text)
    label.setTextColor(color or SiColors.TEXT_PRIMARY)
    # QFrame 子类卡片加 WA_StyledBackground 后，某些渲染路径会让
    # 无样式表的 SiLabel 背景按 palette 画成不透明色块，必须显式
    # 强制透明（widget 级样式表优先级最高，直接覆盖任何级联来源）
    label.setStyleSheet("background: transparent;")
    return label


def themed_tab_button(text: str) -> SiToggleButtonRefactor:
    """房间筛选 tab，选中态为主题青底深字。"""
    button = SiToggleButtonRefactor()
    button.setText(text)
    # siui 按钮的默认 size hint 偏大且水平可扩展，强制固定尺寸
    # 避免三个 tab 在整行里分散排布
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    button.style_data.button_color = QColor("#00" + SiColors.CARD[1:])
    button.style_data.text_color = QColor(SiColors.TEXT_SECONDARY)
    button.style_data.toggled_button_color = QColor(SiColors.THEME)
    button.style_data.toggled_text_color = QColor(SiColors.ON_THEME_TEXT)
    button.style_data.hover_color = QColor("#1a" + SiColors.THEME[1:])
    button.style_data.idle_color = QColor("#00" + SiColors.THEME[1:])
    button.style_data.click_color = QColor("#40" + SiColors.THEME[1:])
    button.reloadStyleData()
    return button


def themed_combo(options: list[str], current: str = "",
                 editable: bool = False) -> "QComboBox":
    """设置页等处的下拉选择器：原生 QComboBox + 主题化样式。

    曾用过 siui 的 SiCapsuleComboBox，但其胶囊风格与设置页卡片
    不搭且依赖图标包资源，改回原生控件按调色板着色。

    editable=True 时允许直接键入任意文本（如缩放百分比），下拉仍
    提供预设档位；输入不会插入列表（保持档位干净）。
    """
    import qtawesome as qta
    from PySide6.QtWidgets import QLabel, QComboBox

    class _NoWheelCombo(QComboBox):
        """禁用滚轮 + 自绘主题色下拉三角的原生下拉框。

        QSS 的 border 三角技巧在 down-arrow 上会渲染成色块长条，
        改为子控件摆一个 qtawesome 三角图标，颜色随调色板。
        """

        def __init__(self):
            super().__init__()
            self._arrow = QLabel(self)
            self._arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        def set_arrow_color(self, color: str) -> None:
            self._arrow.setPixmap(qta.icon('mdi.chevron-down', color=color).pixmap(14, 14))

        def resizeEvent(self, event) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._arrow.move(self.width() - 20, (self.height() - 14) // 2)

        def wheelEvent(self, event) -> None:  # noqa: N802
            # 禁用滚轮切换：设置页滚动浏览时容易误改选项
            event.ignore()

    combo = _NoWheelCombo()
    combo.addItems(options)
    if editable:
        combo.setEditable(True)
        # 键入文本不进列表，档位保持预设干净；回车/失焦提交由调用方处理
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setCurrentText(current)
    elif current and current in options:
        combo.setCurrentText(current)
    combo.setCursor(Qt.CursorShape.PointingHandCursor)
    combo.setFixedHeight(32)
    # 选项最多四个字，固定窄宽即可，避免被布局撑得过宽；
    # 可编辑下拉同样保持此宽度，与设置页其他下拉（如主题配色）一致。
    # 弹出列表异常（压缩成几像素）的原因是 drop-down 区被隐藏，
    # 由 apply_combo_qss(editable=True) 保留 24px 下拉区解决，与宽度无关。
    combo.setFixedWidth(112)
    apply_combo_qss(combo, editable=editable)
    combo.set_arrow_color(SiColors.TEXT_SECONDARY)
    return combo


def apply_combo_qss(combo, editable: bool = False) -> None:
    """下拉框与弹出列表按当前调色板着色（主题切换时重设）。

    editable=True 时保留 ::drop-down 区域宽度：可编辑下拉需要点
    下拉区才弹出列表（点正文是输入）；隐藏该区域会导致点不开。
    """
    combo.setStyleSheet(f"""
        QComboBox {{
            background: {SiColors.SURFACE};
            border: 1px solid {SiColors.LINE};
            border-radius: 8px;
            padding: 4px {28 if editable else 28}px 4px 12px;
            color: {SiColors.TEXT_PRIMARY};
            font-size: 9pt;
        }}
        QComboBox:hover {{ border-color: {SiColors.THEME}; }}
        QComboBox::drop-down {{ border: none; width: {24 if editable else 0}px; }}
        QComboBox::down-arrow {{ width: 0px; height: 0px; }}
        QComboBox QAbstractItemView {{
            background: {SiColors.CARD};
            border: 1px solid {SiColors.LINE};
            border-radius: 6px;
            color: {SiColors.TEXT_PRIMARY};
            selection-background-color: {SiColors.BTN_HOVER};
            selection-color: {SiColors.TEXT_PRIMARY};
            outline: none;
        }}
        QComboBox QLineEdit {{
            background: transparent;
            border: none;
            color: {SiColors.TEXT_PRIMARY};
        }}
    """)
