# -*- coding: utf-8 -*-
"""指标监测面板：实时曲线图 + 指标选择器 + 全指标数值表（开发规格书 §4 拓展）。

    MonitorPanel  —— 顶部一排指标 chip（每个指标都可选）；左侧坐标框画所选
                     指标的实时曲线；右侧表格实时列出全部指标的当前值。
    TimeSeriesChart —— 自绘的滚动折线图（QPainter），深色网格 + 彩色曲线 +
                       曲线下渐隐填充 + 末端当前值标记；随主题配色，无额外依赖。

只读展示，不改动检测逻辑：主窗口每帧把 (FrameFeatures, WindowFeatures,
FatigueResult) 喂进来，面板负责缓存历史并刷新曲线/表格。
"""

import math
from collections import deque
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QButtonGroup, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult, LEVEL_NAMES
from fatigue_system.ui import theme

_NAN = float("nan")
_HISTORY = 600   # 曲线缓存点数（约 30s @ 20fps）


def _num(value) -> float:
    """安全取数：None/无效 → NaN（曲线会在此处断开）。"""
    try:
        v = float(value)
        return v if math.isfinite(v) else _NAN
    except (TypeError, ValueError):
        return _NAN


# 可画曲线的指标：键, 中文名, 曲线色, 固定y范围(None=自适应), 取值函数, 数值格式
_METRICS = [
    ("ear", "EAR", theme.ACCENT, (0.0, 0.45),
     lambda ff, wf, r: _num(ff.ear) if ff and ff.face_found else _NAN, "{:.3f}"),
    ("mar", "MAR", theme.ACCENT_2, (0.0, 0.6),
     lambda ff, wf, r: _num(ff.mar) if ff and ff.face_found else _NAN, "{:.3f}"),
    ("pitch", "俯仰角", "#a78bfa", None,
     lambda ff, wf, r: _num(ff.pitch) if ff and ff.face_found else _NAN, "{:+.1f}°"),
    ("yaw", "偏航角", "#c084fc", None,
     lambda ff, wf, r: _num(ff.yaw) if ff and ff.face_found else _NAN, "{:+.1f}°"),
    ("roll", "翻滚角", "#818cf8", None,
     lambda ff, wf, r: _num(ff.roll) if ff and ff.face_found else _NAN, "{:+.1f}°"),
    ("perclos", "PERCLOS", "#d29922", (0.0, 1.0),
     lambda ff, wf, r: _num(wf.perclos) if wf else _NAN, "{:.0%}"),
    ("blink", "眨眼率", "#3fb950", None,
     lambda ff, wf, r: _num(wf.blink_rate) if wf else _NAN, "{:.1f}"),
    ("closed", "最长闭眼", "#2dd4bf", None,
     lambda ff, wf, r: _num(wf.eye_closed_dur) if wf else _NAN, "{:.1f}s"),
    ("yawn", "哈欠数", "#fbbf24", None,
     lambda ff, wf, r: _num(wf.yawn_count) if wf else _NAN, "{:.0f}"),
    ("nod", "点头数", "#f0883e", None,
     lambda ff, wf, r: _num(wf.nod_count) if wf else _NAN, "{:.0f}"),
    ("hr", "心率 HR", "#f85149", None,
     lambda ff, wf, r: _num(wf.hr) if wf and wf.hr is not None else _NAN, "{:.0f}"),
    ("hrv", "HRV", "#fb7185", None,
     lambda ff, wf, r: _num(wf.hrv) if wf and wf.hrv is not None else _NAN, "{:.0f}"),
    ("score", "融合分", "#f97316", (0.0, 1.0),
     lambda ff, wf, r: _num(r.score) if r else _NAN, "{:.3f}"),
]


class TimeSeriesChart(QWidget):
    """自绘滚动折线图：网格 + 曲线 + 渐隐填充 + 末端当前值标记。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._data: List[float] = []
        self._color = QColor(theme.ACCENT)
        self._label = "EAR"
        self._unit_fmt = "{:.3f}"
        self._yrange = None

    def set_series(self, data, color: str, label: str, unit_fmt: str, yrange) -> None:
        self._data = list(data)
        self._color = QColor(color)
        self._label = label
        self._unit_fmt = unit_fmt
        self._yrange = yrange
        self.update()

    # ------------------------------- 绘制 ------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, top, right, bottom = 52, 30, 12, 22
        pw, ph = w - left - right, h - top - bottom
        if pw <= 10 or ph <= 10:
            p.end()
            return

        # 绘图区背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0b0e13"))
        p.drawRoundedRect(left, top, pw, ph, 6, 6)

        finite = [v for v in self._data if math.isfinite(v)]
        # 顶部标题 + 当前值
        p.setPen(QColor(theme.TEXT_DIM))
        f = p.font(); f.setPointSize(9); f.setBold(True); p.setFont(f)
        p.drawText(left, 8, pw, 16, Qt.AlignLeft, "{} · 曲线".format(self._label))
        if finite:
            cur = finite[-1]
            p.setPen(self._color)
            f2 = p.font(); f2.setPointSize(12); f2.setBold(True); p.setFont(f2)
            p.drawText(left, 6, pw, 18, Qt.AlignRight, self._unit_fmt.format(cur))

        if len(finite) < 2:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(left, top, pw, ph, Qt.AlignCenter, "等待数据…")
            p.end()
            return

        # y 轴范围
        if self._yrange is not None:
            ymin, ymax = self._yrange
        else:
            ymin, ymax = min(finite), max(finite)
            if ymin == ymax:
                ymin, ymax = ymin - 1.0, ymax + 1.0
            pad = (ymax - ymin) * 0.15
            ymin, ymax = ymin - pad, ymax + pad
        span = (ymax - ymin) or 1.0

        # 水平网格 + y 标签
        grid_pen = QPen(QColor(theme.BORDER)); grid_pen.setWidth(1)
        fy = p.font(); fy.setPointSize(8); fy.setBold(False); p.setFont(fy)
        for i in range(4):
            gy = top + ph * i / 3.0
            p.setPen(grid_pen)
            p.drawLine(int(left), int(gy), int(left + pw), int(gy))
            val = ymax - span * i / 3.0
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(0, int(gy - 8), left - 6, 16, Qt.AlignRight | Qt.AlignVCenter,
                       "{:.2f}".format(val))

        n = len(self._data)
        def _x(idx):
            return left + pw * idx / (n - 1)
        def _y(val):
            return top + ph * (1.0 - (val - ymin) / span)

        # 曲线路径（NaN 处断开）
        line = QPainterPath()
        pen_down = False
        last_pt = None
        for i, v in enumerate(self._data):
            if not math.isfinite(v):
                pen_down = False
                continue
            x, y = _x(i), _y(max(ymin, min(ymax, v)))
            if not pen_down:
                line.moveTo(x, y)
                pen_down = True
            else:
                line.lineTo(x, y)
            last_pt = (x, y)

        # 曲线下渐隐填充（取整段有限区间近似）
        if last_pt is not None:
            area = QPainterPath(line)
            area.lineTo(last_pt[0], top + ph)
            first_x = _x(next(i for i, v in enumerate(self._data) if math.isfinite(v)))
            area.lineTo(first_x, top + ph)
            area.closeSubpath()
            fill = QColor(self._color); fill.setAlpha(38)
            p.setPen(Qt.NoPen); p.setBrush(fill)
            p.drawPath(area)

        # 曲线
        pen = QPen(self._color); pen.setWidth(2); pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        p.drawPath(line)

        # 末端当前值圆点
        if last_pt is not None:
            p.setPen(Qt.NoPen); p.setBrush(self._color)
            p.drawEllipse(int(last_pt[0]) - 3, int(last_pt[1]) - 3, 6, 6)
        p.end()


class MonitorPanel(QGroupBox):
    """检测记录/指标监测：选择器 + 曲线 + 全指标数值表。"""

    def __init__(self, parent=None):
        super().__init__("指标监测 · MONITOR", parent)
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 历史缓存（每个可画曲线的指标一条）
        self._hist: Dict[str, deque] = {k: deque(maxlen=_HISTORY) for k, *_ in _METRICS}
        self._selected = _METRICS[0][0]

        # 顶部：指标选择 chip（每个指标一颗，可切换）
        chips = QGridLayout()
        chips.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, (key, label, color, *_rest) in enumerate(_METRICS):
            btn = QPushButton(label, self)
            btn.setProperty("chip", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            if key == self._selected:
                btn.setChecked(True)
            btn.clicked.connect(lambda _c, k=key: self._select(k))
            self._group.addButton(btn)
            chips.addWidget(btn, i // 7, i % 7)
        root.addLayout(chips)

        # 中部：左曲线 + 右数值表
        mid = QHBoxLayout()
        mid.setSpacing(12)
        self._chart = TimeSeriesChart(self)
        mid.addWidget(self._chart, stretch=3)

        self._table = QTableWidget(len(_TABLE_ROWS), 2, self)
        self._table.setHorizontalHeaderLabels(["指标", "当前值"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for row, (_k, name) in enumerate(_TABLE_ROWS):
            it = QTableWidgetItem(name)
            it.setForeground(QColor(theme.TEXT_DIM))
            self._table.setItem(row, 0, it)
            self._table.setItem(row, 1, QTableWidgetItem("—"))
        mid.addWidget(self._table, stretch=2)
        root.addLayout(mid, stretch=1)

        # 底部：基线信息
        self._baseline = QLabel("基线：未校准 · 使用默认阈值", self)
        self._baseline.setObjectName("baseline")
        root.addWidget(self._baseline)

        self._refresh_chart()

    # ------------------------------- 对外 ------------------------------------

    def append(self, ff: FrameFeatures, wf: WindowFeatures,
               result: Optional[FatigueResult], head_state: str) -> None:
        """每帧调用：把各指标当前值入历史缓存，刷新曲线与数值表。"""
        for key, _label, _c, _yr, fn, _fmt in _METRICS:
            self._hist[key].append(fn(ff, wf, result))
        self._update_table(ff, wf, result, head_state)
        self._refresh_chart()

    def reset(self) -> None:
        for dq in self._hist.values():
            dq.clear()
        for row in range(len(_TABLE_ROWS)):
            self._table.item(row, 1).setText("—")
            self._table.item(row, 1).setForeground(QColor(theme.TEXT_MUTE))
        self._refresh_chart()

    def set_baseline_text(self, text: str) -> None:
        self._baseline.setText(text)

    # ------------------------------- 内部 ------------------------------------

    def _select(self, key: str) -> None:
        self._selected = key
        self._refresh_chart()

    def _refresh_chart(self) -> None:
        meta = next(m for m in _METRICS if m[0] == self._selected)
        _k, label, color, yrange, _fn, fmt = meta
        self._chart.set_series(self._hist[_k], color, label, fmt, yrange)

    def _update_table(self, ff, wf, result, head_state) -> None:
        vals = _table_values(ff, wf, result, head_state)
        for row, (key, _name) in enumerate(_TABLE_ROWS):
            text, color = vals.get(key, ("—", theme.TEXT_MUTE))
            item = self._table.item(row, 1)
            item.setText(text)
            item.setForeground(QColor(color))


# 数值表行（含不适合画曲线的分类项：头部状态、疲劳等级）
_TABLE_ROWS = [
    ("ear", "EAR 眼纵横比"), ("mar", "MAR 嘴纵横比"),
    ("pitch", "俯仰角"), ("yaw", "偏航角"), ("roll", "翻滚角"),
    ("head_state", "头部状态"),
    ("perclos", "PERCLOS"), ("blink", "眨眼率 (次/分)"),
    ("closed", "最长闭眼 (s)"), ("yawn", "哈欠数"), ("nod", "点头数"),
    ("hr", "心率 (bpm)"), ("hrv", "HRV (ms)"),
    ("score", "融合分"), ("level", "疲劳等级"),
]

_HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}
_HEAD_STATE_COLOR = {
    "normal": theme.LEVEL_COLORS[0], "lowered": theme.LEVEL_COLORS[2],
    "tilted": theme.LEVEL_COLORS[2], "nodding": theme.LEVEL_COLORS[3],
}


def _table_values(ff, wf, result, head_state) -> Dict[str, tuple]:
    """算出数值表每行的 (显示文本, 颜色)。"""
    out: Dict[str, tuple] = {}
    face = bool(ff and ff.face_found)
    out["ear"] = ("{:.3f}".format(ff.ear), theme.TEXT) if face else ("—", theme.TEXT_MUTE)
    out["mar"] = ("{:.3f}".format(ff.mar), theme.TEXT) if face else ("—", theme.TEXT_MUTE)
    out["pitch"] = ("{:+.1f}°".format(ff.pitch), theme.TEXT) if face else ("—", theme.TEXT_MUTE)
    out["yaw"] = ("{:+.1f}°".format(ff.yaw), theme.TEXT) if face else ("—", theme.TEXT_MUTE)
    out["roll"] = ("{:+.1f}°".format(ff.roll), theme.TEXT) if face else ("—", theme.TEXT_MUTE)
    out["head_state"] = (_HEAD_STATE_CN.get(head_state, "—"),
                         _HEAD_STATE_COLOR.get(head_state, theme.TEXT_MUTE)) if face \
        else ("—", theme.TEXT_MUTE)
    if wf is not None:
        out["perclos"] = ("{:.0%}".format(wf.perclos), theme.TEXT)
        out["blink"] = ("{:.1f}".format(wf.blink_rate), theme.TEXT)
        out["closed"] = ("{:.1f}".format(wf.eye_closed_dur), theme.TEXT)
        out["yawn"] = (str(wf.yawn_count), theme.TEXT)
        out["nod"] = (str(wf.nod_count), theme.TEXT)
        out["hr"] = ("{:.0f}".format(wf.hr), theme.ACCENT_2) if wf.hr is not None else ("—", theme.TEXT_MUTE)
        out["hrv"] = ("{:.0f}".format(wf.hrv), theme.TEXT) if wf.hrv is not None else ("—", theme.TEXT_MUTE)
    if result is not None:
        color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        out["score"] = ("{:.3f}".format(result.score), color)
        out["level"] = (result.level_name, color)
    return out
