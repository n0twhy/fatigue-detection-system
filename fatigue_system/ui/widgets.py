# -*- coding: utf-8 -*-
"""可复用的现代 UI 组件：数据磁贴、评分进度条、状态胶囊。

这些是"仪表盘"观感的核心构件：指标以小卡片(磁贴)呈现（小字标签 + 等宽大
数字），疲劳分用自绘的圆角进度条，运行状态用带彩点的胶囊。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout

from fatigue_system.ui import theme


class StatTile(QFrame):
    """单个指标磁贴：上排小字标签，下排数值。

    参数:
        label —— 指标名（会转大写作为微标签）。
        mono  —— 数值是否用等宽字体（纯数字用 True；中文状态词用 False）。
    """

    def __init__(self, label: str, mono: bool = True, value_px: int = 20, parent=None):
        super().__init__(parent)
        self.setObjectName("statTile")
        self._mono = mono
        self._value_px = value_px
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(3)
        self._label = QLabel(label, self)
        self._label.setObjectName("statLabel")
        self._value = QLabel("—", self)
        self._last_color = None
        lay.addWidget(self._label)
        lay.addWidget(self._value)
        self.set_value("—")

    def set_value(self, text: str, color: str = None) -> None:
        color = color or theme.TEXT
        # 仅在颜色变化时重设样式表（每帧刷新，避免不必要的样式重算）
        if color != self._last_color:
            self._last_color = color
            fam = "font-family:{};".format(theme.MONO) if self._mono else ""
            self._value.setStyleSheet(
                "color:{c}; {f} font-size:{s}px; font-weight:bold; background:transparent;"
                .format(c=color, f=fam, s=self._value_px))
        self._value.setText(text)


class ScoreMeter(QFrame):
    """自绘的疲劳分进度条：圆角轨道 + 按等级配色的填充。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = 0.0
        self._color = QColor(theme.LEVEL_COLORS[0])
        self.setFixedHeight(14)
        self.setStyleSheet("background: transparent; border: none;")

    def set_score(self, score: float, level: int) -> None:
        self._score = max(0.0, min(1.0, float(score)))
        self._color = QColor(theme.LEVEL_COLORS[max(0, min(3, int(level)))])
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        h = r.height()
        radius = h / 2.0
        # 轨道
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.BORDER))
        p.drawRoundedRect(0, 0, r.width(), h, radius, radius)
        # 填充
        fill_w = int(r.width() * self._score)
        if fill_w > 0:
            p.setBrush(self._color)
            p.drawRoundedRect(0, 0, max(fill_w, h), h, radius, radius)
        p.end()


class StatusPill(QLabel):
    """状态胶囊：一个彩色圆点 + 文字，用于顶栏显示实时状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.set_status("待机", theme.TEXT_MUTE)

    def set_status(self, text: str, dot_color: str) -> None:
        self.setText('<span style="color:{}">&#9679;</span>&nbsp; {}'.format(dot_color, text))
