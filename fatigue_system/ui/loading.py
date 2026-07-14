# -*- coding: utf-8 -*-
"""启动「载入中」窗口。

存在的理由：exe 双击后要等 1~2 分钟才出界面——Windows Defender 会逐个扫描
PyInstaller 包里数百个无签名 DLL（mediapipe / opencv / PyQt5 …）。这是 Defender
对无签名程序的正常行为，我们既关不掉、也不能要求使用者关掉杀软。

既然快不了，就**不要让用户面对一片空白**（那看起来像程序挂了）：先把这个小窗口
显示出来，说明"正在加载、首次启动约需 1~2 分钟"，再去 import 那些重库。

本模块**只能依赖 PyQt5**（不能 import cv2/mediapipe），否则就失去意义了。
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from fatigue_system.ui import theme


class _Progress(QFrame):
    """不确定进度条：一段圆角高亮在轨道上来回滑动（不假装知道进度到几成）。"""

    _W_RATIO = 0.32          # 高亮段占轨道的比例
    _PERIOD_MS = 1400        # 一个来回的周期

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)               # ≈60fps

    def _tick(self) -> None:
        self._t = (self._t + 16.0 / self._PERIOD_MS) % 1.0
        self.update()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TRACK))
        p.drawRoundedRect(0, 0, w, h, 2, 2)
        # 三角波：0→1→0，让高亮段来回滑
        pos = self._t * 2.0
        pos = pos if pos <= 1.0 else 2.0 - pos
        bw = w * self._W_RATIO
        x = (w - bw) * pos
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(int(x), 0, int(bw), h, 2, 2)
        p.end()


class LoadingWindow(QWidget):
    """启动加载窗口：标题 + 当前步骤 + 不确定进度条 + 首次启动慢的说明。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("疲劳检测")
        self.setFixedSize(440, 200)
        self.setStyleSheet("background-color:{};".format(theme.SURFACE))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 26)
        lay.setSpacing(10)

        title = QLabel("疲劳检测", self)
        title.setStyleSheet(
            "font-size:20px; font-weight:500; color:{}; background:transparent;".format(
                theme.TEXT))
        lay.addWidget(title)

        self._step = QLabel("正在启动…", self)
        self._step.setStyleSheet(
            "font-size:14px; color:{}; background:transparent;".format(theme.TEXT_DIM))
        lay.addWidget(self._step)

        lay.addSpacing(6)
        self._bar = _Progress(self)
        lay.addWidget(self._bar)
        lay.addSpacing(6)

        hint = QLabel(
            "首次启动约需 1~2 分钟：Windows 安全中心会先扫描程序文件（这是它对未签名"
            "程序的正常检查）。请耐心等待，不要重复双击。", self)
        hint.setWordWrap(True)
        hint.setStyleSheet(
            "font-size:12px; color:{}; background:transparent;".format(theme.TEXT_MUTE))
        lay.addWidget(hint)
        lay.addStretch(1)

    def set_step(self, text: str) -> None:
        """更新当前加载步骤（调用方记得随后 processEvents 强制重绘）。"""
        self._step.setText(text)

    def close(self) -> bool:      # noqa: A003（Qt 同名方法）
        self._bar.stop()
        return super().close()
