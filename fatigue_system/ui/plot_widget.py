# -*- coding: utf-8 -*-
"""指标监测区（DESIGN.md §5.4）：左曲线卡片 + 右指标列表卡片。

    MonitorPanel    —— 左：曲线卡（小标签"某指标 · 近 60 秒"+ 白底曲线）；
                       右：指标列表（行=名称+当前值，点击行切换曲线，选中行
                       浅蓝底；超出常用 5 项折叠进"更多指标"）。
    TimeSeriesChart —— 自绘滚动折线（QPainter）：强调蓝 2px + 8% 填充；
                       仅"融合分"显示三条阈值虚线（右端标"轻/中/重 x.xx"）；
                       x 轴只标 "-60 s" 与 "现在"。

只读展示，不改动检测逻辑：主窗口每帧把 (FrameFeatures, WindowFeatures,
FatigueResult) 喂进来，面板缓存 60s 历史并刷新曲线/列表。
"""

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult
from fatigue_system.ui import theme
from fatigue_system.ui.anim import animate

_NAN = float("nan")
WINDOW_SEC = 60.0          # 曲线时间窗（秒），标题与 x 轴标注一致
_HIST_MAXLEN = 3600        # 历史缓存上限（60s × 高帧率富余）


def _num(value) -> float:
    """安全取数：None/无效 → NaN（曲线会在此处断开）。"""
    try:
        v = float(value)
        return v if math.isfinite(v) else _NAN
    except (TypeError, ValueError):
        return _NAN


_HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}
_HEAD_STATE_COLOR = {
    "normal": theme.GREEN_TEXT, "lowered": theme.ORANGE,
    "tilted": theme.ORANGE, "nodding": theme.RED,
}


# 指标行定义：key, 列表名, 常用(默认可见), 可画曲线, 固定y范围(None=自适应),
#             曲线取值函数(ff,wf,r)->float
_ROWS = [
    ("score",     "融合分",        True,  True,  (0.0, 1.0),
     lambda ff, wf, r: _num(r.score) if r else _NAN),
    ("ear",       "EAR 眼纵横比",  True,  True,  (0.0, 0.45),
     lambda ff, wf, r: _num(ff.ear) if ff and ff.face_found else _NAN),
    ("perclos",   "PERCLOS",       True,  True,  (0.0, 1.0),
     lambda ff, wf, r: _num(wf.perclos) if wf else _NAN),
    ("blink",     "眨眼率",        True,  True,  None,
     lambda ff, wf, r: _num(wf.blink_rate) if wf else _NAN),
    ("closed",    "最长闭眼",      True,  True,  None,
     lambda ff, wf, r: _num(wf.eye_closed_dur) if wf else _NAN),
    ("microsleep", "微睡眠",       True,  False, None, None),   # 创新②
    ("hr",        "心率 HR",       True,  True,  None,
     lambda ff, wf, r: _num(wf.hr) if wf and wf.hr is not None else _NAN),
    ("kss",       "KSS 嗜睡度",    True,  False, None, None),   # 创新④
    ("mar",       "MAR 嘴纵横比",  False, True,  (0.0, 0.6),
     lambda ff, wf, r: _num(ff.mar) if ff and ff.face_found else _NAN),
    ("avg_blink", "平均眨眼",      False, False, None, None),   # 创新②
    ("yawn",      "哈欠数",        False, True,  None,
     lambda ff, wf, r: _num(wf.yawn_count) if wf else _NAN),
    ("nod",       "点头数",        False, True,  None,
     lambda ff, wf, r: _num(wf.nod_count) if wf else _NAN),
    ("hrv",       "HRV",           False, True,  None,
     lambda ff, wf, r: _num(wf.hrv) if wf and wf.hrv is not None else _NAN),
    ("head_state", "头部状态",     False, False, None, None),
    ("quality",   "信号质量",      False, False, None, None),   # 创新①
    ("level",     "疲劳等级",      False, False, None, None),
]


def _row_values(ff, wf, result, head_state) -> Dict[str, Tuple[str, str]]:
    """算出列表每行的 (显示文本, 颜色)。"""
    out: Dict[str, Tuple[str, str]] = {}
    face = bool(ff and ff.face_found)
    dim = theme.TEXT_MUTE
    out["ear"] = ("{:.3f}".format(ff.ear), theme.TEXT_DIM) if face else ("—", dim)
    out["mar"] = ("{:.3f}".format(ff.mar), theme.TEXT_DIM) if face else ("—", dim)
    out["head_state"] = (_HEAD_STATE_CN.get(head_state, "—"),
                         _HEAD_STATE_COLOR.get(head_state, dim)) if face else ("—", dim)
    if wf is not None:
        out["perclos"] = ("{:.0%}".format(wf.perclos), theme.TEXT_DIM)
        out["blink"] = ("{:.0f}/分".format(wf.blink_rate), theme.TEXT_DIM)
        out["closed"] = ("{:.1f} s".format(wf.eye_closed_dur), theme.TEXT_DIM)
        out["yawn"] = (str(wf.yawn_count), theme.TEXT_DIM)
        out["nod"] = (str(wf.nod_count), theme.TEXT_DIM)
        out["hr"] = ("{:.0f}".format(wf.hr), theme.TEXT_DIM) if wf.hr is not None else ("—", dim)
        out["hrv"] = ("{:.0f}".format(wf.hrv), theme.TEXT_DIM) if wf.hrv is not None else ("—", dim)
        out["avg_blink"] = ("{:.2f} s".format(wf.avg_blink_dur), theme.TEXT_DIM)
        out["microsleep"] = (str(wf.microsleep_count),
                             theme.RED if wf.microsleep_count > 0 else theme.TEXT_DIM)
        q = wf.face_ratio
        q_color = theme.GREEN_TEXT if q >= 0.9 else (theme.ORANGE if q >= 0.5 else theme.RED)
        out["quality"] = ("{:.0%}".format(q), q_color)
    if result is not None:
        color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        out["score"] = ("{:.3f}".format(result.score), theme.TEXT_DIM)
        out["kss"] = ("{}/9".format(result.kss), color)
        out["level"] = (result.level_name, color)
    return out


class TimeSeriesChart(QWidget):
    """自绘滚动折线（§5.4）：无边框无深色底，直接画在白卡片上。

    §7.2：用户切换指标时新旧曲线交叉淡入淡出 220ms（方案A）；阈值虚线随
    融合分切入淡入/切走淡出。实时数据追加不加动画（§7.0 纪律3）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self._points: List[Tuple[float, float]] = []   # (ts, value)
        self._yrange = None
        self._thresholds: Optional[List[Tuple[float, str]]] = None
        # 交叉淡化：上一条曲线的快照 + 淡化进度（1=只显示新曲线）
        self._prev: Optional[Tuple[list, object, object]] = None
        self._fade = 1.0

    def set_series(self, points, yrange, thresholds=None, transition=False) -> None:
        """更新曲线：points=[(ts,val)]；thresholds=[(y, "轻 0.25"), ...] 或 None。

        transition=True（用户点击切换指标）时旧新曲线交叉淡化 220ms；
        False（数据流刷新）直接重绘。
        """
        if transition and self._points:
            self._prev = (list(self._points), self._yrange, self._thresholds)
            self._fade = 0.0
            animate(self, "_anim_fade", 0.0, 1.0, theme.ANIM_BASE, self._on_fade)
        self._points = list(points)
        self._yrange = yrange
        self._thresholds = thresholds
        self.update()

    def _on_fade(self, v) -> None:
        self._fade = float(v)
        if self._fade >= 1.0:
            self._prev = None
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 阈值标注在右侧留白；交叉淡化期间新旧任一带阈值都留出右边距，防跳动
        has_th = bool(self._thresholds) or bool(self._prev and self._prev[2])
        left, top, bottom = 8, 8, 22
        right = 56 if has_th else 14
        pw, ph = w - left - right, h - top - bottom
        if pw <= 10 or ph <= 10:
            p.end()
            return

        font = p.font()
        font.setPointSize(8)
        p.setFont(font)

        drew = False
        # 旧曲线淡出（§7.2 交叉淡化，方案A）
        if self._prev is not None and self._fade < 1.0:
            drew |= self._draw_series(p, left, top, pw, ph, self._prev[0],
                                      self._prev[1], self._prev[2], 1.0 - self._fade)
        alpha = self._fade if self._prev is not None else 1.0
        drew |= self._draw_series(p, left, top, pw, ph, self._points,
                                  self._yrange, self._thresholds, alpha)
        if not drew:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(left, top, pw, ph, Qt.AlignCenter, "等待数据…")
            p.end()
            return

        # x 轴只标 "-60 s" 与 "现在"（§5.4），不随淡化闪动
        p.setPen(QColor(theme.TEXT_MUTE))
        p.drawText(int(left), int(top + ph + 4), 80, 14,
                   Qt.AlignLeft | Qt.AlignTop, "-{:.0f} s".format(WINDOW_SEC))
        p.drawText(int(left + pw - 80), int(top + ph + 4), 80, 14,
                   Qt.AlignRight | Qt.AlignTop, "现在")
        p.end()

    def _draw_series(self, p, left, top, pw, ph, pts, yrange, thresholds,
                     alpha: float) -> bool:
        """按给定不透明度画一组曲线（网格/阈值线/填充/线条）。返回是否画了东西。"""
        if alpha <= 0.01:
            return False
        finite = [v for _t, v in pts if math.isfinite(v)]
        if len(finite) < 2:
            return False

        # y 范围
        if yrange is not None:
            ymin, ymax = yrange
        else:
            ymin, ymax = min(finite), max(finite)
            if ymin == ymax:
                ymin, ymax = ymin - 1.0, ymax + 1.0
            pad = (ymax - ymin) * 0.15
            ymin, ymax = ymin - pad, ymax + pad
        span = (ymax - ymin) or 1.0

        # x 范围：以最新点为"现在"，窗口固定 WINDOW_SEC
        t_end = pts[-1][0]
        t_start = t_end - WINDOW_SEC

        def _x(t):
            return left + pw * (t - t_start) / WINDOW_SEC

        def _y(v):
            return top + ph * (1.0 - (v - ymin) / span)

        def _c(color, base_alpha=255):
            c = QColor(color)
            c.setAlpha(int(base_alpha * alpha))
            return c

        if thresholds:
            # 阈值虚线 + 右端标注（仅融合分，§5.4；随曲线一起淡入淡出 §7.2.4）
            dash_pen = QPen(_c(theme.BORDER))
            dash_pen.setStyle(Qt.DashLine)
            for tv, label in thresholds:
                if not (ymin <= tv <= ymax):
                    continue
                gy = _y(tv)
                p.setPen(dash_pen)
                p.drawLine(int(left), int(gy), int(left + pw), int(gy))
                p.setPen(_c(theme.TEXT_MUTE))
                p.drawText(int(left + pw + 6), int(gy - 8), 50, 16,
                           Qt.AlignLeft | Qt.AlignVCenter, label)
        else:
            # 无阈值：三条极浅网格 + 刻度文字
            grid_pen = QPen(_c(theme.SEPARATOR))
            for i in range(3):
                gy = top + ph * i / 2.0
                p.setPen(grid_pen)
                p.drawLine(int(left), int(gy), int(left + pw), int(gy))
                val = ymax - span * i / 2.0
                p.setPen(_c(theme.TEXT_MUTE))
                p.drawText(int(left + 2), int(gy) + 2, 60, 14,
                           Qt.AlignLeft | Qt.AlignTop, "{:.2f}".format(val))

        # 曲线路径（NaN 断开），只画窗口内的点
        line = QPainterPath()
        pen_down = False
        first_x = last_x = None
        for t, v in pts:
            if t < t_start:
                continue
            if not math.isfinite(v):
                pen_down = False
                continue
            x, y = _x(t), _y(max(ymin, min(ymax, v)))
            if not pen_down:
                line.moveTo(x, y)
                pen_down = True
                if first_x is None:
                    first_x = x
            else:
                line.lineTo(x, y)
            last_x = x

        # 曲线下方填充：强调蓝 8% 不透明度（§2.2）
        if last_x is not None and first_x is not None and last_x > first_x:
            area = QPainterPath(line)
            area.lineTo(last_x, top + ph)
            area.lineTo(first_x, top + ph)
            area.closeSubpath()
            fill = _c(theme.ACCENT, int(255 * theme.CHART_FILL_ALPHA))
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(area)

        pen = QPen(_c(theme.ACCENT))
        pen.setWidth(2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)
        return True


class MetricRow(QWidget):
    """指标列表行（§5.4）：名称 + 当前值；可选中（浅蓝底圆角）；
    未选中行之间由列表容器画细分隔线。
    §7.1/§7.2：悬停底色 150ms、选中态底色/文字色 180ms OutCubic 过渡。"""

    clicked = pyqtSignal(str)

    _TRANSPARENT = QColor(0, 0, 0, 0)

    def __init__(self, key: str, name: str, selectable: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self._selectable = selectable
        self._selected = False
        self._bg = QColor(self._TRANSPARENT)      # 当前底色（动画插值）
        self.setFixedHeight(44)
        if selectable:
            self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._name = QLabel(name, self)
        lay.addWidget(self._name)
        lay.addStretch(1)
        self._value = QLabel("—", self)
        lay.addWidget(self._value)
        self._set_name_color(QColor(theme.TEXT))
        self._set_value_color(QColor(theme.TEXT_DIM))

    # ------------------------------ 颜色驱动 ---------------------------------

    def _set_name_color(self, c: QColor) -> None:
        self._name_color = QColor(c)
        self._name.setStyleSheet(
            "color:{}; font-size:14px; background:transparent;".format(c.name()))

    def _set_value_color(self, c: QColor) -> None:
        self._value_color = QColor(c)
        self._value.setStyleSheet(
            "color:{}; font-size:14px; font-family:{}; background:transparent;".format(
                c.name(), theme.MONO))

    def _set_bg(self, c) -> None:
        self._bg = QColor(c)
        self.update()

    # ------------------------------ 状态切换 ---------------------------------

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        # §7.2：选中态三项同步过渡（底色、名称色、数值色），180ms OutCubic
        bg_to = QColor(theme.SELECT_BG) if selected else QColor(self._TRANSPARENT)
        name_to = QColor(theme.SELECT_FG if selected else theme.TEXT)
        val_to = QColor(theme.SELECT_FG if selected else theme.TEXT_DIM)
        animate(self, "_anim_bg", QColor(self._bg), bg_to, 180, self._set_bg)
        animate(self, "_anim_nc", QColor(self._name_color), name_to, 180,
                self._set_name_color)
        animate(self, "_anim_vc", QColor(self._value_color), val_to, 180,
                self._set_value_color)

    def set_value(self, text: str, color: str) -> None:
        # 数据流更新不做动画（§7.0 纪律3）；选中态文字色由选中动画管
        if not self._selected:
            c = QColor(color)
            if c != self._value_color:
                self._set_value_color(c)
        self._value.setText(text)

    # ------------------------------ 交互事件 ---------------------------------

    def enterEvent(self, event) -> None:
        if self._selectable and not self._selected:
            animate(self, "_anim_bg", QColor(self._bg), QColor(theme.SURFACE_2),
                    theme.ANIM_FAST, self._set_bg)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._selected:
            animate(self, "_anim_bg", QColor(self._bg), QColor(self._TRANSPARENT),
                    theme.ANIM_FAST, self._set_bg)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._selectable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        if self._bg.alpha() > 0:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(self._bg)
            p.drawRoundedRect(self.rect(), theme.RADIUS_CTRL, theme.RADIUS_CTRL)
            p.end()
        super().paintEvent(event)


class _MoreRow(QWidget):
    """「更多指标」折叠行：文字 + 手绘 chevron（禁 emoji，§9）。"""

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._label = QLabel("更多指标", self)
        self._label.setStyleSheet(
            "color:{}; font-size:14px; background:transparent;".format(theme.TEXT_DIM))
        lay.addWidget(self._label)
        lay.addStretch(1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._expanded = not self._expanded
            self.toggled.emit(self._expanded)
            self.update()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.TEXT_DIM))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx = self.width() - 22
        cy = self.height() / 2.0
        if self._expanded:      # 上箭头
            p.drawLine(int(cx - 4), int(cy + 2), int(cx), int(cy - 2))
            p.drawLine(int(cx), int(cy - 2), int(cx + 4), int(cy + 2))
        else:                   # 下箭头
            p.drawLine(int(cx - 4), int(cy - 2), int(cx), int(cy + 2))
            p.drawLine(int(cx), int(cy + 2), int(cx + 4), int(cy - 2))
        p.end()


class _Separator(QFrame):
    """列表行间 1px 分隔线（#F0F0F2，§4）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background-color:{}; border:none;".format(theme.SEPARATOR))


class MonitorPanel(QWidget):
    """指标监测区：左曲线卡 + 右指标列表卡（§5.4）。

    对外接口与旧版一致：append(ff, wf, result, head_state) / reset()；
    基线文案已按 §5.5 移入主窗口状态行，不再由本面板显示。
    """

    def __init__(self, cfg: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self._cfg = cfg or {}
        self._selected = "score"           # 默认选中融合分（样图）
        self._hist: Dict[str, deque] = {
            key: deque(maxlen=_HIST_MAXLEN)
            for key, _n, _c, chartable, _yr, _fn in _ROWS if chartable
        }

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.GAP)

        # 左：曲线卡
        chart_card = QFrame(self)
        chart_card.setObjectName("card")
        cl = QVBoxLayout(chart_card)
        cl.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, 10)
        cl.setSpacing(6)
        self._chart_title = QLabel("", chart_card)
        self._chart_title.setObjectName("sectionTitle")
        cl.addWidget(self._chart_title)
        self._chart = TimeSeriesChart(chart_card)
        cl.addWidget(self._chart, stretch=1)
        root.addWidget(chart_card, stretch=3)

        # 右：指标列表卡（宽度与右上信息栏一致，纵向对齐）
        list_card = QFrame(self)
        list_card.setObjectName("card")
        list_card.setMinimumWidth(400)
        list_card.setMaximumWidth(480)
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(8, 8, 8, 8)
        ll.setSpacing(0)
        self._rows: Dict[str, MetricRow] = {}
        self._more_widgets: List[QWidget] = []
        primary = [r for r in _ROWS if r[2]]
        extra = [r for r in _ROWS if not r[2]]
        for i, (key, name, _common, chartable, _yr, _fn) in enumerate(primary):
            row = MetricRow(key, name, selectable=chartable, parent=list_card)
            row.clicked.connect(self._select)
            self._rows[key] = row
            ll.addWidget(row)
            sep = _Separator(list_card)
            ll.addWidget(sep)
        self._more_row = _MoreRow(list_card)
        self._more_row.toggled.connect(self._toggle_more)
        ll.addWidget(self._more_row)
        for key, name, _common, chartable, _yr, _fn in extra:
            sep = _Separator(list_card)
            row = MetricRow(key, name, selectable=chartable, parent=list_card)
            row.clicked.connect(self._select)
            self._rows[key] = row
            ll.addWidget(sep)
            ll.addWidget(row)
            self._more_widgets.extend([sep, row])
        for wgt in self._more_widgets:      # 默认折叠
            wgt.hide()
        ll.addStretch(1)
        root.addWidget(list_card, stretch=1)

        self._rows[self._selected].set_selected(True)
        self._refresh_chart()

    # ------------------------------- 对外 ------------------------------------

    def append(self, ff: FrameFeatures, wf: WindowFeatures,
               result: Optional[FatigueResult], head_state: str) -> None:
        """每帧调用：入历史缓存（带时间戳），刷新曲线与列表当前值。"""
        ts = ff.ts if ff is not None else 0.0
        for key, _n, _c, chartable, _yr, fn in _ROWS:
            if chartable:
                self._hist[key].append((ts, fn(ff, wf, result)))
        vals = _row_values(ff, wf, result, head_state)
        for key, row in self._rows.items():
            text, color = vals.get(key, ("—", theme.TEXT_MUTE))
            row.set_value(text, color)
        self._refresh_chart()

    def reset(self) -> None:
        for dq in self._hist.values():
            dq.clear()
        for row in self._rows.values():
            row.set_value("—", theme.TEXT_MUTE)
        self._refresh_chart()

    # ------------------------------- 内部 ------------------------------------

    def _select(self, key: str) -> None:
        if key == self._selected:
            return
        self._rows[self._selected].set_selected(False)
        self._selected = key
        self._rows[key].set_selected(True)
        self._refresh_chart(transition=True)   # §7.2：切换指标曲线交叉淡化

    def _toggle_more(self, expanded: bool) -> None:
        for wgt in self._more_widgets:
            wgt.setVisible(expanded)

    def _thresholds(self) -> Optional[List[Tuple[float, str]]]:
        """融合分曲线的三条阈值线（取自配置，调参后即时反映）。"""
        if self._selected != "score":
            return None
        th = self._cfg.get("fusion", {}).get("level_thresholds", {})
        return [
            (float(th.get("severe", 0.70)), "重 {:.2f}".format(float(th.get("severe", 0.70)))),
            (float(th.get("moderate", 0.50)), "中 {:.2f}".format(float(th.get("moderate", 0.50)))),
            (float(th.get("mild", 0.25)), "轻 {:.2f}".format(float(th.get("mild", 0.25)))),
        ]

    def _refresh_chart(self, transition: bool = False) -> None:
        meta = next(r for r in _ROWS if r[0] == self._selected)
        _key, name, _common, _chartable, yrange, _fn = meta
        self._chart_title.setText("{} · 近 {:.0f} 秒".format(name, WINDOW_SEC))
        self._chart.set_series(self._hist[self._selected], yrange,
                               self._thresholds(), transition=transition)
