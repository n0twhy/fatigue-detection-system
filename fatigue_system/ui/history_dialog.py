# -*- coding: utf-8 -*-
"""历史会话回看（v1.11 功能②，老师建议的"历史记录回看"）。

列出 logging.csv_dir 下历次会话（fatigue_log_*.csv），选中即可：
  * 看到该会话的概览（时长/报警次数/平均 KSS/各等级占比）与**等级时间线色带**；
  * 回看融合分曲线（**读 CSV 重绘，不重跑视频、不占摄像头**）；
  * 一键"导出报告"生成单文件 HTML（io/session_report.py），或直接打开报告。

界面遵循 DESIGN.md：覆盖层（压暗 + 白面板 + 0.95→1.00 浮现）、样式全走 theme。
"""

import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from fatigue_system.core.types import LEVEL_NAMES
from fatigue_system.io.session_report import build_report, read_session, _f
from fatigue_system.ui import theme
from fatigue_system.ui.anim import EASE_IN, animate
from fatigue_system.ui.widgets import IconButton

_PANEL_W = 860
_LEVEL_COLORS = (theme.GREEN, "#E9B949", theme.ORANGE, theme.RED)


class _TimelineBar(QWidget):
    """等级时间线色带 + 下方融合分曲线（自绘，样式同主界面）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self._levels: List[Tuple[float, int]] = []
        self._scores: List[Tuple[float, float]] = []

    def set_session(self, levels, scores) -> None:
        self._levels = list(levels)
        self._scores = list(scores)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        band_h = 22
        if not self._levels:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(self.rect(), Qt.AlignCenter, "选择左侧的会话查看")
            p.end()
            return
        t0, t1 = self._levels[0][0], self._levels[-1][0]
        span = (t1 - t0) or 1.0

        # 等级色带
        p.setPen(Qt.NoPen)
        for i, (t, lv) in enumerate(self._levels):
            nxt = self._levels[i + 1][0] if i + 1 < len(self._levels) else t1
            x = w * (t - t0) / span
            bw = max(1.0, w * (nxt - t) / span)
            p.setBrush(QColor(_LEVEL_COLORS[int(lv) % 4]))
            p.drawRect(int(x), 0, int(bw) + 1, band_h)

        # 融合分曲线
        top, bottom = band_h + 16, h - 18
        ph = bottom - top
        if len(self._scores) >= 2 and ph > 10:
            path = QPainterPath()
            for i, (t, s) in enumerate(self._scores):
                x = w * (t - t0) / span
                y = top + ph * (1.0 - max(0.0, min(1.0, s)))
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            pen = QPen(QColor(theme.ACCENT))
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(QColor(theme.TEXT_MUTE))
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(0, h - 14, 80, 14, Qt.AlignLeft, "0s")
        p.drawText(w - 80, h - 14, 80, 14, Qt.AlignRight, "{:.0f}s".format(t1 - t0))
        p.end()


class HistoryDialog(QWidget):
    """历史会话覆盖层：左列表 + 右概览/时间线/曲线 + 导出报告。"""

    def __init__(self, cfg: Dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg or {}
        self._dir = str(self._cfg.get("logging", {}).get("csv_dir", "fatigue_system/outputs"))
        self._dim_p = 0.0
        self._closing = False
        self._current: Optional[str] = None

        self.setFocusPolicy(Qt.StrongFocus)
        self._build()
        self._reload_list()
        if parent is not None:
            parent.installEventFilter(self)
            self.hide()

    # ------------------------------- 界面 ------------------------------------

    def _build(self) -> None:
        self._wrap = QWidget(self)
        self._opacity = QGraphicsOpacityEffect(self._wrap)
        self._wrap.setGraphicsEffect(self._opacity)
        wrap_lay = QVBoxLayout(self._wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)

        panel = QFrame(self._wrap)
        panel.setObjectName("historyPanel")
        panel.setStyleSheet(
            "QFrame#historyPanel {{ background-color: {bg}; border: 1px solid {bd}; "
            "border-radius: {r}px; }}".format(
                bg=theme.SURFACE, bd=theme.BORDER, r=theme.RADIUS_PANEL))
        wrap_lay.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("历史会话", panel)
        title.setStyleSheet("font-size:17px; font-weight:500; background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        btn_close = IconButton("close", "关闭", panel)
        btn_close.setFixedSize(32, 32)
        btn_close.clicked.connect(self.close_overlay)
        head.addWidget(btn_close)
        lay.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)
        self._list = QListWidget(panel)
        self._list.setFixedWidth(230)
        self._list.setStyleSheet(
            "QListWidget {{ background:{s}; border:1px solid {b}; border-radius:10px; "
            "outline:none; }} "
            "QListWidget::item {{ padding:9px 10px; border-bottom:1px solid {sep}; }} "
            "QListWidget::item:selected {{ background:{sel}; color:{selfg}; }}".format(
                s=theme.SURFACE, b=theme.BORDER, sep=theme.SEPARATOR,
                sel=theme.SELECT_BG, selfg=theme.SELECT_FG))
        self._list.currentItemChanged.connect(self._on_pick)
        body.addWidget(self._list)

        right = QVBoxLayout()
        right.setSpacing(10)
        self._summary = QLabel("选择左侧的会话查看", panel)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            "font-size:13px; color:{}; background:transparent;".format(theme.TEXT_DIM))
        right.addWidget(self._summary)
        self._chart = _TimelineBar(panel)
        right.addWidget(self._chart, stretch=1)
        self._levels_label = QLabel("", panel)
        self._levels_label.setStyleSheet(
            "font-size:12px; color:{}; background:transparent;".format(theme.TEXT_MUTE))
        right.addWidget(self._levels_label)
        body.addLayout(right, stretch=1)
        lay.addLayout(body, stretch=1)

        btns = QHBoxLayout()
        self._hint = QLabel("", panel)
        self._hint.setStyleSheet(
            "font-size:12px; color:{}; background:transparent;".format(theme.TEXT_MUTE))
        btns.addWidget(self._hint)
        btns.addStretch(1)
        self._btn_report = QPushButton("导出报告", panel)
        self._btn_report.setProperty("primary", True)
        self._btn_report.setEnabled(False)
        self._btn_report.clicked.connect(self._on_export)
        btns.addWidget(self._btn_report)
        lay.addLayout(btns)

    # ------------------------------- 数据 ------------------------------------

    def _reload_list(self) -> None:
        self._list.clear()
        if not os.path.isdir(self._dir):
            self._hint.setText("尚无历史会话（记录目录不存在）")
            return
        files = sorted((f for f in os.listdir(self._dir)
                        if f.startswith("fatigue_log_") and f.endswith(".csv")),
                       reverse=True)                    # 最新的排最前
        for name in files:
            stamp = name[len("fatigue_log_"):-len(".csv")]
            item = QListWidgetItem(self._pretty_stamp(stamp))
            item.setData(Qt.UserRole, os.path.join(self._dir, name))
            self._list.addItem(item)
        self._hint.setText("共 {} 次会话 · 目录 {}".format(len(files), self._dir)
                           if files else "尚无历史会话（点「记录」按钮开始记录）")

    @staticmethod
    def _pretty_stamp(stamp: str) -> str:
        """20260715_231045 → 2026-07-15 23:10:45。"""
        try:
            d, t = stamp.split("_")
            return "{}-{}-{}  {}:{}:{}".format(d[:4], d[4:6], d[6:8], t[:2], t[2:4], t[4:6])
        except Exception:
            return stamp

    def _on_pick(self, item, _prev=None) -> None:
        if item is None:
            return
        path = item.data(Qt.UserRole)
        self._current = path
        try:
            rows = read_session(path)
        except Exception as exc:
            self._summary.setText("读取失败：{}".format(exc))
            self._btn_report.setEnabled(False)
            return
        if not rows:
            self._summary.setText("该会话没有数据行。")
            self._btn_report.setEnabled(False)
            return

        levels, scores, kss = [], [], []
        alarms = 0
        prev_alarm = False
        level_dur = [0.0] * len(LEVEL_NAMES)
        for i, r in enumerate(rows):
            t, lv, s = _f(r, "timestamp"), _f(r, "level"), _f(r, "fatigue_score")
            if t is None:
                continue
            if lv is not None:
                levels.append((t, int(lv)))
                nxt = _f(rows[i + 1], "timestamp") if i + 1 < len(rows) else t
                dt = max(0.0, (nxt or t) - t)
                if dt < 5.0:
                    level_dur[int(lv) % len(LEVEL_NAMES)] += dt
            if s is not None:
                scores.append((t, s))
            k = _f(r, "kss")
            if k is not None:
                kss.append(k)
            a = bool(int(_f(r, "alarm") or 0))
            if a and not prev_alarm:
                alarms += 1
            prev_alarm = a

        total = sum(level_dur)
        avg_kss = sum(kss) / len(kss) if kss else None
        self._summary.setText(
            "时长 {:.0f} 分 {:.0f} 秒　·　报警 {} 次　·　平均 KSS {}　·　记录 {} 行".format(
                total // 60, total % 60, alarms,
                "{:.1f}/9".format(avg_kss) if avg_kss is not None else "—", len(rows)))
        self._levels_label.setText("　".join(
            "{} {:.0%}".format(n, (level_dur[i] / total) if total else 0)
            for i, n in enumerate(LEVEL_NAMES)))
        self._chart.set_session(levels, scores)
        self._btn_report.setEnabled(True)

    def _on_export(self) -> None:
        if not self._current:
            return
        try:
            path = build_report(self._current, cfg=self._cfg)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", "生成报告失败：{}".format(exc))
            return
        self._open_file(path)
        QMessageBox.information(self, "报告已生成", "报告已保存到：\n{}".format(path))

    @staticmethod
    def _open_file(path: str) -> None:
        """用系统默认程序打开报告（Windows/WSL/Linux 各自的方式，失败则静默）。"""
        try:
            if sys.platform == "win32":
                os.startfile(path)                       # noqa: S606
            elif os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
                subprocess.Popen(["wslview", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass                                          # 打不开就算了，路径已提示

    # ------------------------------ overlay ----------------------------------

    def open_over(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        self._reload_list()
        self._closing = False
        self.setGeometry(parent.rect())
        final = self._final_rect()
        self._wrap.setGeometry(self._scaled(final, 0.95))
        self.show()
        self.raise_()
        self.setFocus()
        self._opacity.setOpacity(0.0)
        animate(self, "_a_op", 0.0, 1.0, theme.ANIM_BASE, self._opacity.setOpacity)
        animate(self, "_a_geo", self._scaled(final, 0.95), final, theme.ANIM_BASE,
                self._wrap.setGeometry)
        animate(self, "_a_dim", 0.0, 0.28, theme.ANIM_BASE, self._on_dim)

    def close_overlay(self) -> None:
        if self._closing or self.parentWidget() is None:
            self.hide()
            return
        self._closing = True
        cur = self._wrap.geometry()
        animate(self, "_a_op", float(self._opacity.opacity()), 0.0, theme.ANIM_FAST,
                self._opacity.setOpacity, easing=EASE_IN)
        animate(self, "_a_geo", cur, self._scaled(cur, 0.97), theme.ANIM_FAST,
                self._wrap.setGeometry, easing=EASE_IN)
        animate(self, "_a_dim", self._dim_p, 0.0, theme.ANIM_FAST, self._on_dim,
                easing=EASE_IN, on_finish=self._after_close)

    def _after_close(self) -> None:
        self.hide()
        self._closing = False
        self.deleteLater()

    def _final_rect(self) -> QRect:
        w = min(_PANEL_W, self.width() - 48)
        h = min(560, self.height() - 48)
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    @staticmethod
    def _scaled(rect: QRect, factor: float) -> QRect:
        w, h = int(rect.width() * factor), int(rect.height() * factor)
        return QRect(rect.center().x() - w // 2, rect.center().y() - h // 2, w, h)

    def _on_dim(self, v) -> None:
        self._dim_p = float(v)
        self.update()

    def paintEvent(self, event) -> None:
        if self._dim_p > 0.001:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * self._dim_p)))
            p.end()

    def mousePressEvent(self, event) -> None:
        if not self._wrap.geometry().contains(event.pos()):
            self.close_overlay()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_overlay()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == event.Resize and self.isVisible():
            self.setGeometry(obj.rect())
            self._wrap.setGeometry(self._final_rect())
        return super().eventFilter(obj, event)
