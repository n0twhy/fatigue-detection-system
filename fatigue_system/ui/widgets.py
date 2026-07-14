# -*- coding: utf-8 -*-
"""可复用的现代 UI 组件：数据磁贴、评分进度条、状态胶囊。

这些是"仪表盘"观感的核心构件：指标以小卡片(磁贴)呈现（小字标签 + 等宽大
数字），疲劳分用自绘的圆角进度条，运行状态用带彩点的胶囊。
"""

import math

from PyQt5.QtCore import Qt, QPointF, QRect
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QAbstractButton, QFrame, QLabel, QPushButton, QVBoxLayout

from fatigue_system.ui import theme
from fatigue_system.ui.anim import animate


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


class ThinBar(QFrame):
    """细分量进度条（DESIGN.md §5.3）：默认灰色填充，仅当该分量超过阈值时
    填充变橙（状态色仅限等级/报警语义，§2.3）。
    高度按样图尺度取 6px（2026-07-14 用户校准：与样图冲突时以样图为准）。
    §7.4：数值宽度变化 200ms OutCubic 平滑推移；灰↔橙颜色插值 200ms。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disp_value = None                    # 当前显示值（动画插值）
        self._disp_color = QColor(theme.TEXT_MUTE)  # 当前显示色（动画插值）
        self._over = False
        self.setFixedHeight(6)
        self.setStyleSheet("background: transparent; border: none;")

    def set_value(self, value: float, over_threshold: bool) -> None:
        target = max(0.0, min(1.0, float(value)))
        color = QColor(theme.ORANGE if over_threshold else theme.TEXT_MUTE)
        if self._disp_value is None:               # 首次直接就位，不播动画
            self._disp_value = target
            self._disp_color = color
            self.update()
            return
        if abs(target - self._disp_value) > 1e-4:
            animate(self, "_anim_v", float(self._disp_value), target, 200,
                    self._on_value_frame)
        if over_threshold != self._over:
            animate(self, "_anim_c", QColor(self._disp_color), color, 200,
                    self._on_color_frame)
        self._over = bool(over_threshold)

    def _on_value_frame(self, v) -> None:
        self._disp_value = float(v)
        self.update()

    def _on_color_frame(self, c) -> None:
        self._disp_color = QColor(c)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TRACK))
        p.drawRoundedRect(0, 0, w, h, 3, 3)
        fill_w = int(w * (self._disp_value or 0.0))
        if fill_w > 0:
            p.setBrush(self._disp_color)
            p.drawRoundedRect(0, 0, max(fill_w, h), h, 3, 3)
        p.end()


class StatusPill(QLabel):
    """状态胶囊：一个彩色圆点 + 文字，用于顶栏显示实时状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        self.set_status("待机", theme.TEXT_MUTE)

    def set_status(self, text: str, dot_color: str) -> None:
        self.setText('<span style="color:{}">&#9679;</span>&nbsp; {}'.format(dot_color, text))


class StatusDot(QLabel):
    """运行状态：彩色小圆点(7px) + 12px 次级灰文字，无底色（DESIGN.md §5.1）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusDot")
        self.set_status("待机", theme.TEXT_MUTE)

    def set_status(self, text: str, dot_color: str) -> None:
        self.setText(
            '<span style="color:{}; font-size:13px;">&#9679;</span>&nbsp; {}'.format(
                dot_color, text))


class IconButton(QPushButton):
    """细线条图标按钮（DESIGN.md §5.1/§6）：无底色，悬停浅灰底，线性图标。

    kind: 'target'（校准）| 'record'（记录，可 check）| 'gear'（设置）
          | 'close'（关闭 ×）。
    图标用 QPainter 手绘（§9 禁 emoji/彩色图标），颜色统一次级灰；
    禁用态用弱化灰；record 选中态用强调蓝表示"记录中"。
    §7.1：悬停底色 150ms 过渡（手绘，不走 QSS 即时切换）；按下图标缩到 0.92。
    """

    def __init__(self, kind: str, tooltip: str, parent=None, checkable: bool = False,
                 label: str = ""):
        super().__init__(parent)
        self._kind = kind
        self._hover_p = 0.0        # 悬停底色进度 0..1（动画插值）
        self._label = label        # 图标右侧的文字标签（空则纯图标）
        self.setProperty("iconbtn", True)
        self.setToolTip(tooltip)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        if label:
            # 带标签：图标 + 文字（老师建议——纯图标第一眼看不出是什么功能）
            self.setFixedHeight(48)
            fm = self.fontMetrics()
            self.setFixedWidth(40 + fm.width(label) + 14)
        else:
            self.setFixedSize(48, 48)

    def enterEvent(self, event) -> None:
        animate(self, "_anim_h", float(self._hover_p), 1.0, theme.ANIM_FAST,
                self._on_hover_frame)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        animate(self, "_anim_h", float(self._hover_p), 0.0, theme.ANIM_FAST,
                self._on_hover_frame)
        super().leaveEvent(event)

    def _on_hover_frame(self, v) -> None:
        self._hover_p = float(v)
        self.update()

    def _icon_color(self) -> QColor:
        if not self.isEnabled():
            return QColor(theme.TEXT_MUTE)
        if self.isChecked():
            return QColor(theme.ACCENT)
        return QColor(theme.TEXT_DIM)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)          # QSS 画选中态底色
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 悬停底色：150ms 动画插值（选中态已有底色则不再叠加）
        if self._hover_p > 0.01 and not self.isChecked():
            bg = QColor(theme.TRACK if self.isDown() else theme.SURFACE_2)
            bg.setAlphaF(self._hover_p)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(self.rect(), theme.RADIUS_CTRL, theme.RADIUS_CTRL)
        c = self._icon_color()
        pen = QPen(c)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        # 带标签时图标靠左、文字居右；纯图标时居中
        cx = 24.0 if self._label else self.width() / 2.0
        cy = self.height() / 2.0
        r = 12.0 * (0.92 if self.isDown() else 1.0)   # §7.1 按下轻微缩小
        if self._kind == "target":         # 校准：圆 + 四刻度 + 中心点
            p.drawEllipse(QPointF(cx, cy), r - 1.5, r - 1.5)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p.drawLine(QPointF(cx + dx * (r - 4), cy + dy * (r - 4)),
                           QPointF(cx + dx * (r + 1.5), cy + dy * (r + 1.5)))
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy), 1.5, 1.5)
        elif self._kind == "record":       # 记录：外圈 + 内实心点
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy), 3.0, 3.0)
        elif self._kind == "gear":         # 设置：内外圈 + 8 齿
            p.drawEllipse(QPointF(cx, cy), r - 2.5, r - 2.5)
            p.setBrush(Qt.NoBrush)
            for i in range(8):
                a = math.pi / 4 * i
                x1 = cx + (r - 2.0) * math.cos(a)
                y1 = cy + (r - 2.0) * math.sin(a)
                x2 = cx + (r + 1.5) * math.cos(a)
                y2 = cy + (r + 1.5) * math.sin(a)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.drawEllipse(QPointF(cx, cy), 2.0, 2.0)
        elif self._kind == "close":        # 关闭：× 两条斜线
            d = r * 0.55
            p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
        elif self._kind == "history":      # 历史：时钟（圆 + 指针）
            p.drawEllipse(QPointF(cx, cy), r - 1.0, r - 1.0)
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy - r * 0.5))       # 时针
            p.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.42, cy))      # 分针
        if self._label:                    # 文字标签（层级靠字号/颜色不喧宾夺主）
            p.setPen(c)
            f = p.font()
            f.setPointSize(10)
            p.setFont(f)
            p.drawText(QRect(40, 0, self.width() - 46, self.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, self._label)
        p.end()


class Switch(QAbstractButton):
    """开关 Switch（DESIGN.md §6/§7.6，替代 QCheckBox）：全圆轨道 + 白色滑块。

    轨道 关闭 TRACK / 开启 GREEN，滑块位移与轨道颜色同步 180ms OutCubic 过渡。
    尺寸按样图尺度 48×28（§0.2 校准：与样图冲突以样图为准）。
    """

    _W, _H = 48, 28
    _KNOB = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self._W, self._H)
        self._p = 0.0            # 开关进度 0(关)..1(开)，动画插值
        self.toggled.connect(self._on_toggled)

    def setChecked(self, checked: bool) -> None:   # noqa: N802（Qt 命名）
        """程序化设值（如从配置载入）：直接就位，不播动画。"""
        blocked = self.blockSignals(True)
        super().setChecked(checked)
        self.blockSignals(blocked)
        self._p = 1.0 if checked else 0.0
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        animate(self, "_anim_p", float(self._p), 1.0 if checked else 0.0, 180,
                self._on_frame)

    def _on_frame(self, v) -> None:
        self._p = float(v)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 轨道颜色随进度插值（§7.6：与滑块同步 180ms）
        off, on = QColor(theme.TRACK), QColor(theme.GREEN)
        t = self._p
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, self._W, self._H, self._H / 2.0, self._H / 2.0)
        # 白色滑块 + 微影
        margin = (self._H - self._KNOB) / 2.0
        x = margin + (self._W - self._KNOB - 2 * margin) * t
        shadow = QColor(0, 0, 0, 50)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(x + self._KNOB / 2.0, margin + self._KNOB / 2.0 + 1),
                      self._KNOB / 2.0, self._KNOB / 2.0)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QPointF(x + self._KNOB / 2.0, margin + self._KNOB / 2.0),
                      self._KNOB / 2.0, self._KNOB / 2.0)
        p.end()
