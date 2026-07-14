# -*- coding: utf-8 -*-
"""界面主题：苹果风浅色极简（EXPWORK/DESIGN.md 是唯一事实来源）。

统一色板 + 一份全局 QSS，供 app 应用到整个 QApplication；各面板只引用这里
的颜色/间距/动画常量，控件代码中禁止写死任何色值或时长（DESIGN.md §0.3/§9）。

设计纪律（DESIGN.md §1）：做减法；层级靠字号字重不靠颜色；彩色只用于
"可交互/选中"（强调蓝）与"疲劳等级/报警"（状态色）；留白慷慨；
一个界面永远只有一个实心彩色按钮。
"""

# ------------------------------- 基础色（§2.1）-------------------------------
BG = "#F5F5F7"          # 窗口底色
SURFACE = "#FFFFFF"     # 卡片底色
SURFACE_2 = "#F5F5F7"   # 卡片内的浅灰层（磁贴/输入底、悬停底）
BORDER = "#E5E5EA"      # 卡片边框（1px）
BORDER_HL = "#D2D2D7"   # 按钮/输入控件描边
SEPARATOR = "#F0F0F2"   # 列表行分隔线
TEXT = "#1D1D1F"        # 主文字
TEXT_DIM = "#6E6E73"    # 次级文字
TEXT_MUTE = "#AEAEB2"   # 弱化文字（坐标轴、状态栏、占位）
TRACK = "#EBEBED"       # 进度条轨道 / 开关关闭态

# ------------------------------ 强调色（§2.2，全局唯一）----------------------
ACCENT = "#0071E3"      # 强调蓝：曲线、主按钮、选中
ACCENT_2 = "#0071E3"    # 兼容旧引用：不允许第二强调色，与 ACCENT 同值
SELECT_BG = "#E6F1FB"   # 选中行背景
SELECT_FG = "#185FA5"   # 选中行文字
ACCENT_DEEP = SELECT_BG  # 兼容旧引用（旧深色主题的"点缀深底"→ 选中浅蓝底）
CHART_FILL_ALPHA = 0.08  # 曲线下方填充不透明度（同强调蓝）

# ---------------------- 状态色（§2.3，仅限等级/报警，禁止挪用）----------------
GREEN = "#34C759"        # 正常/报警解除（圆点、开关开启态）
GREEN_TEXT = "#248A3D"   # 绿色状态配套文字
ORANGE = "#EF9F27"       # 中度加深橙 / 分量进度条超阈值填充
RED = "#E24B4A"          # 重度/停止按钮/报警中（白字）
# 等级 badge（底色, 文字），索引与 fusion 等级 0..3 对应
LEVEL_BADGES = (
    ("#EAF7EE", GREEN_TEXT),    # 清醒
    ("#FAEEDA", "#854F0B"),     # 轻度疲劳
    ("#FAEEDA", "#BA7517"),     # 中度疲劳
    ("#FBE9E9", RED),           # 重度疲劳
)
# 兼容旧引用：单色版（文字/描边用，取 badge 文字色系）
LEVEL_COLORS = (GREEN_TEXT, "#854F0B", ORANGE, RED)

# ------------------------------ 视频区特例（§2.4）----------------------------
VIDEO_BG = "#1A1A1C"                    # 视频容器固定深色，不随浅色主题变白
PILL_BG = "rgba(40,40,44,0.8)"          # 视频浮层胶囊底
PILL_FG = "#C8C8CC"                     # 视频浮层胶囊文字
PILL_OK_BG = "rgba(30,60,40,0.75)"      # 人脸正常胶囊底
PILL_OK_FG = "#D0F5DD"                  # 人脸正常胶囊文字

# ------------------------------ 字体（§3）-----------------------------------
FONT_STACK = ('"SF Pro Display", "SF Pro Text", "PingFang SC", "Segoe UI", '
              '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif')
MONO = '"SF Mono", "Consolas", "Cascadia Mono", "DejaVu Sans Mono", monospace'

# --------------------------- 间距/圆角/边框（§4）-----------------------------
GAP = 12            # 卡片之间统一间距
PAD_CARD = 20       # 卡片内边距（样图尺度，§4 允许 16-20）
RADIUS_CARD = 12    # 卡片圆角
RADIUS_PANEL = 14   # 面板/弹窗圆角
RADIUS_CTRL = 8     # 按钮与输入控件圆角
SHADOW_POPUP = "0 8px 24px rgba(0,0,0,0.12)"   # 唯一允许的阴影（弹出面板）

# ------------------------------ 动画（§7.0）---------------------------------
ANIM_FAST = 150     # ms，悬停、退场
ANIM_BASE = 220     # ms，常规状态切换
ANIM_SLOW = 300     # ms，上限，任何动画不得超过
# 缓动曲线统一：进场/状态变化 QEasingCurve.OutCubic，退场 InCubic（§7.0，
# 在动画代码处 import QEasingCurve 使用；禁止 Linear/OutBounce/OutBack）


def _qss() -> str:
    return f"""
* {{
    color: {TEXT};
    font-size: 14px;
    font-family: {FONT_STACK};
}}
QWidget {{
    background-color: {BG};
}}
QMainWindow, QDialog {{
    background-color: {BG};
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}

/* ---- 按钮（默认：白底描边；悬停浅灰；见 §6）---- */
QPushButton {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_HL};
    border-radius: {RADIUS_CTRL}px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {SURFACE_2};
}}
QPushButton:pressed {{
    background-color: {TRACK};
}}
QPushButton:disabled {{
    color: {TEXT_MUTE};
    border-color: {BORDER};
    background-color: {SURFACE};
}}
/* 旧 accent 按钮（校准/记录）：描边样式，不实心——实心只留给唯一主按钮 */
QPushButton[accent="true"] {{
    border-color: {BORDER_HL};
    color: {TEXT};
}}
QPushButton[accent="true"]:hover {{
    background-color: {SURFACE_2};
}}
QPushButton[accent="true"]:checked {{
    background-color: {SELECT_BG};
    border-color: {SELECT_BG};
    color: {SELECT_FG};
}}
/* 唯一实心主按钮（§5.1）：运行状态切换蓝/红由代码设 danger 属性 */
QPushButton[primary="true"] {{
    background-color: {ACCENT};
    border: none;
    color: #FFFFFF;
    font-weight: 500;
    font-size: 17px;
    padding: 12px 28px;
}}
QPushButton[primary="true"]:hover {{
    background-color: #0077ED;
}}
QPushButton[primary="true"][danger="true"] {{
    background-color: {RED};
}}

/* ---- 下拉框 ---- */
QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_HL};
    border-radius: {RADIUS_CTRL}px;
    padding: 10px 16px;
    font-size: 16px;
}}
QComboBox:hover {{ background-color: {SURFACE_2}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {SELECT_BG};
    selection-color: {SELECT_FG};
    outline: none;
}}

/* ---- 复选框（过渡样式；§6 最终形态为 Switch，阶段7替换）---- */
QCheckBox {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER_HL};
    border-radius: 4px;
    background-color: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background-color: {GREEN};
    border-color: {GREEN};
}}

/* ---- 数字输入 ---- */
QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_HL};
    border-radius: {RADIUS_CTRL}px;
    padding: 4px 8px;
}}

/* ---- 分组卡片 ---- */
QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD}px;
    margin-top: 14px;
    padding: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_DIM};
    font-size: 12px;
}}

/* ---- 表格 ---- */
QTableWidget {{
    background-color: {SURFACE};
    alternate-background-color: {SURFACE};
    gridline-color: {SEPARATOR};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CTRL}px;
    font-size: 13px;
}}
QHeaderView::section {{
    background-color: {SURFACE};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-size: 12px;
}}
QTableWidget::item {{ padding: 3px; }}
QTableWidget::item:selected {{
    background-color: {SELECT_BG};
    color: {SELECT_FG};
}}
QTableCornerButton::section {{ background-color: {SURFACE}; border: none; }}

/* ---- 滚动条（细长，浅色）---- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HL}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_HL}; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_MUTE}; }}

/* ---- 弹窗 ---- */
QMessageBox {{ background-color: {SURFACE}; }}

/* ---- 顶部工具栏（§5.1）---- */
QFrame#header {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD}px;
}}
QLabel#appTitle {{
    font-size: 20px;
    font-weight: 500;
    color: {TEXT};
    background: transparent;
}}
QLabel#statusDot {{
    font-size: 16px;
    color: {TEXT_DIM};
    background: transparent;
}}
/* 图标按钮：无底色（§5.1/§6）；悬停底色由 IconButton 手绘 150ms 动画（§7.1） */
QPushButton[iconbtn="true"] {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_CTRL}px;
    padding: 0;
}}
QPushButton[iconbtn="true"]:checked {{
    background-color: {SELECT_BG};
}}
QLabel#logoMark {{
    background-color: {SURFACE_2};
    color: {TEXT_DIM};
    border-radius: {RADIUS_CTRL}px;
    font-weight: 500;
    font-size: 16px;
    qproperty-alignment: AlignCenter;
}}
QLabel#headerTitle {{ font-size: 14px; font-weight: 500; color: {TEXT}; }}
QLabel#headerSub {{ font-size: 11px; color: {TEXT_MUTE}; }}
QLabel#pill {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 4px 12px;
    color: {TEXT_DIM};
    font-size: 12px;
}}

/* ---- 卡片 / 磁贴 ---- */
QFrame#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD}px;
}}
QFrame#statTile {{
    background-color: {SURFACE_2};
    border: none;
    border-radius: {RADIUS_CTRL}px;
}}
QLabel#statLabel {{
    color: {TEXT_DIM};
    font-size: 12px;
}}
QLabel#sectionTitle {{
    color: {TEXT_DIM};
    font-size: 13px;
}}
QLabel#baseline {{
    color: {TEXT_MUTE};
    font-size: 12px;
    background: transparent;
    border: none;
    padding: 4px 2px;
}}
QLabel#heroLevel {{ font-size: 30px; font-weight: 500; }}

/* ---- 疲劳等级卡片（§5.3）---- */
QLabel#bigScore {{
    font-size: 38px;
    font-weight: 500;
    color: {TEXT};
    font-family: {MONO};
}}
QLabel#kssLabel {{
    font-size: 13px;
    color: {TEXT_MUTE};
}}
QLabel#subLabel {{
    font-size: 13px;
    color: {TEXT_DIM};
}}
QLabel#subValue {{
    font-size: 13px;
    color: {TEXT};
    font-family: {MONO};
}}
/* 报警状态行（§5.3）：单行文字表达，不再用整幅色块 */
QLabel#alarmText {{ font-size: 14px; }}
QLabel#alarmCount {{ font-size: 13px; color: {TEXT_MUTE}; }}

/* ---- 指标选择器 chip（阶段4将改为列表行，此处先给浅色过渡样式）---- */
QPushButton[chip="true"] {{
    background-color: {SURFACE};
    border: 1px solid {BORDER_HL};
    border-radius: 13px;
    padding: 4px 13px;
    font-size: 12px;
    color: {TEXT_DIM};
}}
QPushButton[chip="true"]:hover {{
    background-color: {SURFACE_2};
    color: {TEXT};
}}
QPushButton[chip="true"]:checked {{
    background-color: {SELECT_BG};
    border-color: {SELECT_BG};
    color: {SELECT_FG};
    font-weight: 500;
}}
"""


STYLESHEET = _qss()
