# -*- coding: utf-8 -*-
"""界面主题：深色现代仪表盘风格（geek / 赛博终端观感）。

统一色板 + 一份全局 QSS，供 app 应用到整个 QApplication；各面板只引用这里
的颜色常量，不再各写各的内联浅色样式，保证整体风格一致。

设计取向：近黑蓝底 + 霓虹青(teal/cyan)点缀 + 等宽字体读数；数据用 Consolas
等宽显示更有"仪表盘"感，中文自动回退到界面 CJK 字体正常渲染。
"""

# ------------------------------- 色板 ---------------------------------------
BG = "#0d1117"          # 主背景（近黑、微蓝）
SURFACE = "#161b22"     # 卡片/面板
SURFACE_2 = "#1c2128"   # 更深一层（输入框/表格行）
BORDER = "#30363d"      # 细边框
BORDER_HL = "#3d444d"   # 高亮边框
TEXT = "#e6edf3"        # 主文字
TEXT_DIM = "#8b949e"    # 次要文字
TEXT_MUTE = "#6e7681"   # 更弱文字
ACCENT = "#2dd4bf"      # 主点缀：霓虹青
ACCENT_2 = "#22d3ee"    # 次点缀：青蓝
ACCENT_DEEP = "#0f2e2a" # 点缀的深色底（悬停填充）

# 四级疲劳配色（深色主题下鲜明但不刺眼），索引与 fusion 等级 0..3 对应
LEVEL_COLORS = ("#3fb950", "#d29922", "#f0883e", "#f85149")

# 等宽字体（读数/表格用）；中文由后面的 CJK 字体回退渲染
MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace'


def _qss() -> str:
    return f"""
* {{
    color: {TEXT};
    font-size: 13px;
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

/* ---- 按钮 ---- */
QPushButton {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 7px 14px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT_DEEP};
}}
QPushButton:disabled {{
    color: {TEXT_MUTE};
    border-color: {BORDER};
    background-color: {SURFACE};
}}
/* 主行动按钮（校准/记录）：青色描边更醒目 */
QPushButton[accent="true"] {{
    border-color: {ACCENT};
    color: {ACCENT};
    font-weight: bold;
}}
QPushButton[accent="true"]:hover {{
    background-color: {ACCENT_DEEP};
}}
QPushButton[accent="true"]:checked {{
    background-color: {ACCENT};
    color: {BG};
    font-weight: bold;
}}

/* ---- 下拉框 ---- */
QComboBox {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 10px;
}}
QComboBox:hover {{ border-color: {BORDER_HL}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DEEP};
    selection-color: {ACCENT};
    outline: none;
}}

/* ---- 复选框 ---- */
QCheckBox {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER_HL};
    border-radius: 4px;
    background-color: {SURFACE_2};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

/* ---- 分组卡片 ---- */
QGroupBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT};
}}

/* ---- 表格 ---- */
QTableWidget {{
    background-color: {SURFACE};
    alternate-background-color: {SURFACE_2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: {MONO};
    font-size: 12px;
}}
QHeaderView::section {{
    background-color: {SURFACE_2};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-weight: bold;
}}
QTableWidget::item {{ padding: 3px; }}
QTableWidget::item:selected {{
    background-color: {ACCENT_DEEP};
    color: {ACCENT};
}}
QTableCornerButton::section {{ background-color: {SURFACE_2}; border: none; }}

/* ---- 滚动条（细长现代款）---- */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HL}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_HL}; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}

/* ---- 弹窗 ---- */
QMessageBox {{ background-color: {SURFACE}; }}

/* ---- 顶部应用头栏 ---- */
QFrame#header {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QLabel#logoMark {{
    background-color: {ACCENT};
    color: {BG};
    border-radius: 8px;
    font-weight: bold;
    font-size: 18px;
    qproperty-alignment: AlignCenter;
}}
QLabel#headerTitle {{ font-size: 17px; font-weight: bold; color: {TEXT}; }}
QLabel#headerSub {{ font-size: 10px; color: {TEXT_MUTE}; letter-spacing: 2px; }}
QLabel#pill {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 5px 12px;
    color: {TEXT_DIM};
    font-family: {MONO};
    font-size: 12px;
}}

/* ---- 卡片 / 磁贴 ---- */
QFrame#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QFrame#statTile {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#statLabel {{
    color: {TEXT_MUTE};
    font-size: 10px;
    font-weight: bold;
}}
QLabel#sectionTitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: bold;
    letter-spacing: 1px;
}}
QLabel#baseline {{
    color: {TEXT_DIM};
    font-size: 12px;
    font-family: {MONO};
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
}}
QLabel#heroLevel {{ font-size: 30px; font-weight: bold; }}
"""


STYLESHEET = _qss()
