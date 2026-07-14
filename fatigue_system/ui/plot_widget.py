# -*- coding: utf-8 -*-
"""指标监测区（DESIGN.md §5.4）：左曲线卡片 + 右指标列表卡片。

    MonitorPanel    —— 左：曲线卡（标题 + 当前值大数字 + 平滑曲线）；
                       右：指标列表（点击行切换曲线；常用 8 项 / "更多指标"**换页**
                       看其余项，不撑长卡片）。
    TimeSeriesChart —— 自绘曲线：
      * **平滑**：先把序列重采样到均匀网格，再用 Catmull-Rom 样条绘制。慢指标
        （PERCLOS/心率/融合分等每秒才更新一次）额外做滑动平均——否则每帧重复
        写同一个值会画成"台阶"；EAR/MAR 是逐帧量，不平滑以保留眨眼细节。
      * **切换动画（§7.2 方案B 逐点插值）**：新旧曲线在同一批 x 上逐点插值
        y = 旧 + (新−旧)×ease(t)（归一化坐标），曲线真实"变形"过去，不是简单
        叠加透明度；y 轴刻度与阈值线同步插值/淡入淡出。220ms OutCubic，
        与列表选中态、卡片大数字**同起同止**。

只读展示，不改动检测逻辑。
"""

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult
from fatigue_system.ui import theme
from fatigue_system.ui.anim import animate

_NAN = float("nan")
WINDOW_SEC = 60.0          # 曲线时间窗（秒）
_HIST_MAXLEN = 3600        # 历史缓存上限
_GRID_N = 300              # 重采样网格点数（≈ 每 0.2 秒一个点，够画平滑曲线）


def _num(value) -> float:
    """安全取数：None/无效 → NaN（曲线在此断开）。"""
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


class Metric:
    """一个可展示的指标：列表行 + （可选）曲线的全部元数据。

    字段:
        key/name   —— 键与中文名。
        common     —— 是否属于"常用"页（第 1 页）。
        chartable  —— 是否可画曲线（可点击切换）。
        yrange     —— 固定 y 范围；None 表示按数据自适应。
        fn         —— 从 (ff, wf, result) 取曲线值。
        fmt        —— 大数字/列表值的格式（如 "{:.3f}"、"{:.0%}"）。
        smooth_sec —— 绘图平滑窗口（秒）。0 = 不平滑（逐帧量，如 EAR/MAR）；
                      慢指标（每秒更新一次）取 1.5s，抹掉"台阶"。
    """

    def __init__(self, key, name, common, chartable, yrange, fn, fmt, smooth_sec=0.0):
        self.key = key
        self.name = name
        self.common = common
        self.chartable = chartable
        self.yrange = yrange
        self.fn = fn
        self.fmt = fmt
        self.smooth_sec = smooth_sec


_SLOW = 1.5     # 慢指标（1Hz 更新）的绘图平滑窗口（秒）

METRICS: List[Metric] = [
    Metric("score", "融合分", True, True, (0.0, 1.0),
           lambda ff, wf, r: _num(r.score) if r else _NAN, "{:.3f}", _SLOW),
    Metric("ear", "EAR 眼纵横比", True, True, (0.0, 0.45),
           lambda ff, wf, r: _num(ff.ear) if ff and ff.face_found else _NAN, "{:.3f}", 0.0),
    Metric("perclos", "PERCLOS", True, True, (0.0, 1.0),
           lambda ff, wf, r: _num(wf.perclos) if wf else _NAN, "{:.0%}", _SLOW),
    Metric("blink", "眨眼率", True, True, None,
           lambda ff, wf, r: _num(wf.blink_rate) if wf else _NAN, "{:.0f}/分", _SLOW),
    Metric("closed", "最长闭眼", True, True, None,
           lambda ff, wf, r: _num(wf.eye_closed_dur) if wf else _NAN, "{:.1f} s", _SLOW),
    Metric("microsleep", "微睡眠", True, True, None,
           lambda ff, wf, r: _num(wf.microsleep_count) if wf else _NAN, "{:.0f}", _SLOW),
    Metric("hr", "心率 HR", True, True, None,
           lambda ff, wf, r: _num(wf.hr) if wf and wf.hr is not None else _NAN, "{:.0f}", _SLOW),
    Metric("kss", "KSS 嗜睡度", True, True, (1.0, 9.0),
           lambda ff, wf, r: _num(r.kss) if r else _NAN, "{:.0f}/9", _SLOW),
    # ---- 第 2 页（更多指标）----
    Metric("mar", "MAR 嘴纵横比", False, True, (0.0, 0.6),
           lambda ff, wf, r: _num(ff.mar) if ff and ff.face_found else _NAN, "{:.3f}", 0.0),
    Metric("avg_blink", "平均眨眼时长", False, True, None,
           lambda ff, wf, r: _num(wf.avg_blink_dur) if wf else _NAN, "{:.2f} s", _SLOW),
    Metric("yawn", "哈欠数", False, True, None,
           lambda ff, wf, r: _num(wf.yawn_count) if wf else _NAN, "{:.0f}", _SLOW),
    Metric("nod", "点头数", False, True, None,
           lambda ff, wf, r: _num(wf.nod_count) if wf else _NAN, "{:.0f}", _SLOW),
    Metric("hrv", "HRV", False, True, None,
           lambda ff, wf, r: _num(wf.hrv) if wf and wf.hrv is not None else _NAN, "{:.0f}", _SLOW),
    Metric("quality", "信号质量", False, True, (0.0, 1.0),
           lambda ff, wf, r: _num(wf.face_ratio) if wf else _NAN, "{:.0%}", _SLOW),
    Metric("pitch", "俯仰角", False, True, None,
           lambda ff, wf, r: _num(ff.pitch) if ff and ff.face_found else _NAN, "{:+.0f}°", _SLOW),
    Metric("head_state", "头部状态", False, False, None, None, "", 0.0),
    Metric("level", "疲劳等级", False, False, None, None, "", 0.0),
]

_BY_KEY: Dict[str, Metric] = {m.key: m for m in METRICS}


def _row_values(ff, wf, result, head_state) -> Dict[str, Tuple[str, str]]:
    """列表每行的 (显示文本, 颜色)。"""
    out: Dict[str, Tuple[str, str]] = {}
    dim = theme.TEXT_MUTE
    normal = theme.TEXT_DIM

    def put(key, val, color=normal):
        m = _BY_KEY[key]
        out[key] = (m.fmt.format(val), color) if val is not None else ("—", dim)

    face = bool(ff and ff.face_found)
    put("ear", ff.ear if face else None)
    put("mar", ff.mar if face else None)
    put("pitch", ff.pitch if face else None)
    out["head_state"] = ((_HEAD_STATE_CN.get(head_state, "—"),
                          _HEAD_STATE_COLOR.get(head_state, dim)) if face else ("—", dim))
    if wf is not None:
        put("perclos", wf.perclos)
        put("blink", wf.blink_rate)
        put("closed", wf.eye_closed_dur)
        put("avg_blink", wf.avg_blink_dur)
        put("microsleep", wf.microsleep_count,
            theme.RED if wf.microsleep_count > 0 else normal)
        put("yawn", wf.yawn_count)
        put("nod", wf.nod_count)
        put("hr", wf.hr)
        put("hrv", wf.hrv)
        q = wf.face_ratio
        put("quality", q,
            theme.GREEN_TEXT if q >= 0.9 else (theme.ORANGE if q >= 0.5 else theme.RED))
    if result is not None:
        color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        put("score", result.score)
        put("kss", result.kss, color)
        out["level"] = (result.level_name, color)
    return out


# ------------------------------- 曲线控件 ------------------------------------

class TimeSeriesChart(QWidget):
    """自绘曲线：重采样 + 平滑 + 逐点插值变形（§7.2 方案B）。"""

    # 绘图区边距：左侧留给 y 轴刻度、底部留给时间轴、右侧留给阈值标注
    _PAD_L, _PAD_T, _PAD_B = 54, 10, 26
    _PAD_R_TH, _PAD_R = 58, 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        # 归一化网格（0..1，NaN=无数据），以及对应的 y 轴范围与阈值线
        self._grid: List[float] = [_NAN] * _GRID_N
        self._yrange: Tuple[float, float] = (0.0, 1.0)
        self._thresholds: Optional[List[Tuple[float, str]]] = None
        # 变形动画的起止快照
        self._from_grid: Optional[List[float]] = None
        self._from_yrange: Optional[Tuple[float, float]] = None
        self._from_th: Optional[List[Tuple[float, str]]] = None
        self._p = 1.0        # 变形进度 0..1（1=已完成）

    # ------------------------------ 数据入口 ---------------------------------

    def set_series(self, points, metric: Metric,
                   thresholds=None, transition: bool = False) -> None:
        """更新曲线。transition=True（用户切换指标）时做 220ms 逐点插值变形。"""
        grid, yrange = self._prepare(points, metric)
        if transition:
            # 起点＝当前**正在显示**的形状（可能处于上一次变形中途），保证连续
            self._from_grid = self._blended_grid()
            self._from_yrange = self._blended_yrange()
            self._from_th = self._thresholds
            self._p = 0.0
            animate(self, "_anim_morph", 0.0, 1.0, theme.ANIM_BASE, self._on_morph)
        self._grid, self._yrange, self._thresholds = grid, yrange, thresholds
        self.update()

    def _on_morph(self, v) -> None:
        self._p = float(v)
        if self._p >= 1.0:
            self._from_grid = None
            self._from_yrange = None
            self._from_th = None
        self.update()

    def _prepare(self, points, metric: Metric):
        """(ts,value) 序列 → 均匀网格上的归一化 y（0..1）+ y 轴范围。

        三步：① 按时间重采样到 _GRID_N 个等距点（线性插值）；② 慢指标做滑动平均
        （每秒才更新一次的量在每帧被重复写入，原样画出来就是台阶）；③ 按 y 范围归一化。
        """
        pts = [(t, v) for t, v in points if math.isfinite(v)]
        if len(pts) < 2:
            return [_NAN] * _GRID_N, (0.0, 1.0)

        t_end = points[-1][0]
        t_start = t_end - WINDOW_SEC
        step = WINDOW_SEC / (_GRID_N - 1)

        # ① 重采样（pts 按时间递增，用游标线性插值）
        raw: List[float] = []
        j = 0
        for i in range(_GRID_N):
            t = t_start + i * step
            if t < pts[0][0] or t > pts[-1][0]:
                raw.append(_NAN)
                continue
            while j + 1 < len(pts) and pts[j + 1][0] < t:
                j += 1
            t0, v0 = pts[j]
            if j + 1 < len(pts):
                t1, v1 = pts[j + 1]
                r = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                raw.append(v0 + (v1 - v0) * r)
            else:
                raw.append(v0)

        # ② 慢指标滑动平均（抹掉 1Hz 阶梯）
        if metric.smooth_sec > 0:
            k = max(1, int(round(metric.smooth_sec / step)))
            raw = self._moving_average(raw, k)

        # ③ 归一化
        finite = [v for v in raw if math.isfinite(v)]
        if not finite:
            return [_NAN] * _GRID_N, (0.0, 1.0)
        if metric.yrange is not None:
            y0, y1 = metric.yrange
        else:
            y0, y1 = min(finite), max(finite)
            if y1 - y0 < 1e-9:
                y0, y1 = y0 - 1.0, y1 + 1.0
            pad = (y1 - y0) * 0.15
            y0, y1 = y0 - pad, y1 + pad
        span = (y1 - y0) or 1.0
        grid = [(_NAN if not math.isfinite(v) else max(0.0, min(1.0, (v - y0) / span)))
                for v in raw]
        return grid, (y0, y1)

    @staticmethod
    def _moving_average(values: List[float], k: int) -> List[float]:
        """居中滑动平均（NaN 跳过；窗口内全是 NaN 则保持 NaN）。"""
        if k <= 1:
            return values
        half = k // 2
        out: List[float] = []
        n = len(values)
        for i in range(n):
            lo, hi = max(0, i - half), min(n, i + half + 1)
            win = [v for v in values[lo:hi] if math.isfinite(v)]
            out.append(sum(win) / len(win) if win else _NAN)
        return out

    # ------------------------------ 变形混合 ---------------------------------

    def _blended_grid(self) -> List[float]:
        """当前应显示的归一化网格（变形中＝旧新逐点插值，§7.2 方案B 的核心）。"""
        if self._from_grid is None or self._p >= 1.0:
            return list(self._grid)
        p = self._p
        out = []
        for a, b in zip(self._from_grid, self._grid):
            if math.isfinite(a) and math.isfinite(b):
                out.append(a + (b - a) * p)      # 逐点数值插值 → 曲线真实"变形"
            elif math.isfinite(b):
                out.append(b)                     # 旧曲线此处无数据 → 直接用新值
            elif math.isfinite(a):
                out.append(a)
            else:
                out.append(_NAN)
        return out

    def _blended_yrange(self) -> Tuple[float, float]:
        """y 轴范围同步插值——刻度数字跟着曲线一起"滚"过去，不会突跳。"""
        if self._from_yrange is None or self._p >= 1.0:
            return self._yrange
        p = self._p
        (a0, a1), (b0, b1) = self._from_yrange, self._yrange
        return (a0 + (b0 - a0) * p, a1 + (b1 - a1) * p)

    # ------------------------------- 绘制 ------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 变形期间：新旧任一有阈值线就先把右边距留出来，避免绘图区宽度跳变
        has_th = bool(self._thresholds) or bool(self._from_th and self._p < 1.0)
        pad_r = self._PAD_R_TH if has_th else self._PAD_R
        left, top = self._PAD_L, self._PAD_T
        pw, ph = w - left - pad_r, h - top - self._PAD_B
        if pw <= 10 or ph <= 10:
            p.end()
            return

        font = p.font()
        font.setPointSize(9)
        p.setFont(font)

        grid = self._blended_grid()
        finite_idx = [i for i, v in enumerate(grid) if math.isfinite(v)]
        if len(finite_idx) < 2:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRect(left, top, pw, ph), Qt.AlignCenter, "等待数据…")
            p.end()
            return

        y0, y1 = self._blended_yrange()

        def sy(norm):       # 归一化值 → 屏幕 y
            return top + ph * (1.0 - norm)

        # ---- 网格线与 y 轴刻度（画在左侧留白区，右对齐，不会被裁）----
        grid_pen = QPen(QColor(theme.SEPARATOR))
        for i in range(3):
            norm = 1.0 - i / 2.0
            gy = sy(norm)
            p.setPen(grid_pen)
            p.drawLine(int(left), int(gy), int(left + pw), int(gy))
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRect(0, int(gy) - 9, left - 8, 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       self._fmt_tick(y0 + (y1 - y0) * norm))

        # ---- 阈值虚线（仅融合分；变形期随进度淡入/淡出）----
        th_alpha = self._p if self._thresholds else (1.0 - self._p)
        th_list = self._thresholds or self._from_th
        if th_list and th_alpha > 0.01:
            span = (y1 - y0) or 1.0
            for tv, label in th_list:
                norm = (tv - y0) / span
                if not (0.0 <= norm <= 1.0):
                    continue
                gy = sy(norm)
                pen = QPen(self._fade(theme.BORDER, th_alpha))
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(int(left), int(gy), int(left + pw), int(gy))
                p.setPen(self._fade(theme.TEXT_MUTE, th_alpha))
                p.drawText(QRect(int(left + pw + 6), int(gy) - 9, pad_r - 8, 18),
                           Qt.AlignLeft | Qt.AlignVCenter, label)

        # ---- 曲线（Catmull-Rom 平滑）+ 下方填充 ----
        step_x = pw / (_GRID_N - 1)
        segments: List[List[QPointF]] = []
        cur: List[QPointF] = []
        for i, v in enumerate(grid):
            if math.isfinite(v):
                cur.append(QPointF(left + i * step_x, sy(v)))
            elif cur:
                segments.append(cur)
                cur = []
        if cur:
            segments.append(cur)

        for seg in segments:
            if len(seg) < 2:
                continue
            path = self._smooth_path(seg)
            area = QPainterPath(path)
            area.lineTo(seg[-1].x(), top + ph)
            area.lineTo(seg[0].x(), top + ph)
            area.closeSubpath()
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(theme.CHART_FILL_ALPHA)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            pen = QPen(QColor(theme.ACCENT))
            pen.setWidth(2)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # ---- x 轴（只标两端，§5.4）----
        p.setPen(QColor(theme.TEXT_MUTE))
        p.drawText(QRect(left, int(top + ph + 6), 90, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, "-{:.0f} s".format(WINDOW_SEC))
        p.drawText(QRect(int(left + pw - 90), int(top + ph + 6), 90, 16),
                   Qt.AlignRight | Qt.AlignVCenter, "现在")
        p.end()

    @staticmethod
    def _fade(color: str, alpha: float) -> QColor:
        c = QColor(color)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c

    @staticmethod
    def _fmt_tick(v: float) -> str:
        """y 轴刻度格式：大数取整、小数留两位（避免 90.73 这类挤不下）。"""
        av = abs(v)
        if av >= 100:
            return "{:.0f}".format(v)
        if av >= 10:
            return "{:.1f}".format(v)
        return "{:.2f}".format(v)

    @staticmethod
    def _smooth_path(pts: List[QPointF]) -> QPainterPath:
        """Catmull-Rom 样条 → 三次贝塞尔，画出平滑曲线（不再是折线/台阶）。"""
        path = QPainterPath(pts[0])
        n = len(pts)
        for i in range(n - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0,
                         p1.y() + (p2.y() - p0.y()) / 6.0)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0,
                         p2.y() - (p3.y() - p1.y()) / 6.0)
            path.cubicTo(c1, c2, p2)
        return path


# ------------------------------- 列表控件 ------------------------------------

class MetricRow(QWidget):
    """指标行：名称 + 当前值；悬停 150ms、选中态 180ms 过渡（§7.1/§7.2）。"""

    clicked = pyqtSignal(str)
    _TRANSPARENT = QColor(0, 0, 0, 0)

    def __init__(self, metric: Metric, parent=None):
        super().__init__(parent)
        self.key = metric.key
        self._selectable = metric.chartable
        self._selected = False
        self._bg = QColor(self._TRANSPARENT)
        self.setFixedHeight(44)
        if self._selectable:
            self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._name = QLabel(metric.name, self)
        lay.addWidget(self._name)
        lay.addStretch(1)
        self._value = QLabel("—", self)
        lay.addWidget(self._value)
        self._set_name_color(QColor(theme.TEXT))
        self._set_value_color(QColor(theme.TEXT_DIM))

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

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        bg_to = QColor(theme.SELECT_BG) if selected else QColor(self._TRANSPARENT)
        name_to = QColor(theme.SELECT_FG if selected else theme.TEXT)
        val_to = QColor(theme.SELECT_FG if selected else theme.TEXT_DIM)
        # 与曲线变形、大数字滚动同一时长，三者同起同止（§7.2）
        animate(self, "_a_bg", QColor(self._bg), bg_to, theme.ANIM_BASE, self._set_bg)
        animate(self, "_a_nc", QColor(self._name_color), name_to, theme.ANIM_BASE,
                self._set_name_color)
        animate(self, "_a_vc", QColor(self._value_color), val_to, theme.ANIM_BASE,
                self._set_value_color)

    def set_value(self, text: str, color: str) -> None:
        if not self._selected:
            c = QColor(color)
            if c != self._value_color:
                self._set_value_color(c)
        self._value.setText(text)

    def enterEvent(self, event) -> None:
        if self._selectable and not self._selected:
            animate(self, "_a_bg", QColor(self._bg), QColor(theme.SURFACE_2),
                    theme.ANIM_FAST, self._set_bg)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._selected:
            animate(self, "_a_bg", QColor(self._bg), QColor(self._TRANSPARENT),
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


class _PagerRow(QWidget):
    """翻页行："更多指标 ⌄" ⇄ "返回常用指标 ⌃"（换页，不撑长列表）。"""

    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on_page2 = False
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._label = QLabel("更多指标", self)
        self._label.setStyleSheet(
            "color:{}; font-size:14px; background:transparent;".format(theme.TEXT_DIM))
        lay.addWidget(self._label)
        lay.addStretch(1)

    def set_page2(self, on: bool) -> None:
        self._on_page2 = on
        self._label.setText("返回常用指标" if on else "更多指标")
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.toggled.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.TEXT_DIM))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx, cy = self.width() - 22, self.height() / 2.0
        if self._on_page2:      # 上箭头（返回）
            p.drawLine(int(cx - 4), int(cy + 2), int(cx), int(cy - 2))
            p.drawLine(int(cx), int(cy - 2), int(cx + 4), int(cy + 2))
        else:                   # 下箭头（更多）
            p.drawLine(int(cx - 4), int(cy - 2), int(cx), int(cy + 2))
            p.drawLine(int(cx), int(cy + 2), int(cx + 4), int(cy - 2))
        p.end()


class _Separator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background-color:{}; border:none;".format(theme.SEPARATOR))


class MonitorPanel(QWidget):
    """指标监测区：左曲线卡（标题+大数字） + 右指标列表卡（两页）。"""

    def __init__(self, cfg: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self._cfg = cfg or {}
        self._selected = "score"
        self._hist: Dict[str, deque] = {
            m.key: deque(maxlen=_HIST_MAXLEN) for m in METRICS if m.chartable}
        self._big_value = 0.0          # 卡片大数字当前显示值（切换时插值滚动）

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.GAP)

        # ---- 左：曲线卡 ----
        chart_card = QFrame(self)
        chart_card.setObjectName("card")
        cl = QVBoxLayout(chart_card)
        cl.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, 10)
        cl.setSpacing(2)
        head = QHBoxLayout()
        self._chart_title = QLabel("", chart_card)
        self._chart_title.setObjectName("sectionTitle")
        head.addWidget(self._chart_title)
        head.addStretch(1)
        self._chart_value = QLabel("—", chart_card)     # 当前值大数字（§7.2 滚动）
        self._chart_value.setStyleSheet(
            "font-size:24px; font-weight:500; font-family:{}; color:{}; "
            "background:transparent;".format(theme.MONO, theme.ACCENT))
        head.addWidget(self._chart_value)
        cl.addLayout(head)
        self._chart = TimeSeriesChart(chart_card)
        cl.addWidget(self._chart, stretch=1)
        root.addWidget(chart_card, stretch=3)

        # ---- 右：指标列表卡（两页）----
        list_card = QFrame(self)
        list_card.setObjectName("card")
        list_card.setMinimumWidth(400)
        list_card.setMaximumWidth(480)
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(8, 8, 8, 8)
        ll.setSpacing(0)
        self._rows: Dict[str, MetricRow] = {}
        self._page_widgets: Dict[int, List[QWidget]] = {1: [], 2: []}
        for page, metrics in ((1, [m for m in METRICS if m.common]),
                              (2, [m for m in METRICS if not m.common])):
            for i, m in enumerate(metrics):
                if i > 0:
                    sep = _Separator(list_card)
                    ll.addWidget(sep)
                    self._page_widgets[page].append(sep)
                row = MetricRow(m, list_card)
                row.clicked.connect(self._select)
                self._rows[m.key] = row
                ll.addWidget(row)
                self._page_widgets[page].append(row)
        self._pager = _PagerRow(list_card)
        self._pager.toggled.connect(self._toggle_page)
        ll.addWidget(self._pager)
        ll.addStretch(1)
        root.addWidget(list_card, stretch=1)

        self._show_page(1)
        self._rows[self._selected].set_selected(True)
        self._refresh_chart()

    # ------------------------------- 对外 ------------------------------------

    def append(self, ff: FrameFeatures, wf: WindowFeatures,
               result: Optional[FatigueResult], head_state: str) -> None:
        ts = ff.ts if ff is not None else 0.0
        for m in METRICS:
            if m.chartable:
                self._hist[m.key].append((ts, m.fn(ff, wf, result)))
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
        self._chart_value.setText("—")
        self._refresh_chart()

    # ------------------------------- 内部 ------------------------------------

    def _show_page(self, page: int) -> None:
        for pg, widgets in self._page_widgets.items():
            for wgt in widgets:
                wgt.setVisible(pg == page)
        self._page = page
        self._pager.set_page2(page == 2)

    def _toggle_page(self) -> None:
        self._show_page(2 if self._page == 1 else 1)

    def _select(self, key: str) -> None:
        if key == self._selected:
            return
        old_val = self._latest(self._selected)
        self._rows[self._selected].set_selected(False)
        self._selected = key
        self._rows[key].set_selected(True)
        self._refresh_chart(transition=True)
        # 大数字滚动（§7.2.3）：与曲线变形、选中态同起同止
        new_val = self._latest(key)
        if old_val is not None and new_val is not None:
            animate(self, "_a_big", float(old_val), float(new_val), theme.ANIM_BASE,
                    self._on_big_frame)

    def _on_big_frame(self, v) -> None:
        self._big_value = float(v)
        self._chart_value.setText(_BY_KEY[self._selected].fmt.format(self._big_value))

    def _latest(self, key: str) -> Optional[float]:
        for _t, v in reversed(self._hist.get(key, ())):
            if math.isfinite(v):
                return v
        return None

    def _thresholds(self) -> Optional[List[Tuple[float, str]]]:
        if self._selected != "score":
            return None
        th = self._cfg.get("fusion", {}).get("level_thresholds", {})
        return [(float(th.get("severe", 0.70)), "重 {:.2f}".format(float(th.get("severe", 0.70)))),
                (float(th.get("moderate", 0.50)), "中 {:.2f}".format(float(th.get("moderate", 0.50)))),
                (float(th.get("mild", 0.25)), "轻 {:.2f}".format(float(th.get("mild", 0.25))))]

    def _refresh_chart(self, transition: bool = False) -> None:
        m = _BY_KEY[self._selected]
        self._chart_title.setText("{} · 近 {:.0f} 秒".format(m.name, WINDOW_SEC))
        self._chart.set_series(self._hist[m.key], m, self._thresholds(),
                               transition=transition)
        # 数据流刷新时直接显示当前值（不加动画，§7.0 纪律3）；切换时由滚动动画接管
        if not transition:
            v = self._latest(m.key)
            self._chart_value.setText(m.fmt.format(v) if v is not None else "—")
