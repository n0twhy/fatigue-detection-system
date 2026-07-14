# -*- coding: utf-8 -*-
"""① 视频显示区（开发规格书 §4 / §6.12；浮层样式见 DESIGN.md §2.4/§5.2）。

包含：
    * draw_landmarks —— 关键点叠加（画在帧上，可由右下角开关切换）
    * draw_hud —— 旧版 ASCII 调试框（§5.2 已废弃不再调用，保留仅供
      dev_tools 兼容引用）
    * VideoWidget —— 深色视频容器 + 三个浮层：右上"人脸 正常/丢失"胶囊、
      左下 EAR/MAR/姿态 数值胶囊、右下关键点显示开关

注意：cv2.putText 不支持中文；中文一律放 Qt 浮层控件。
"""

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QLabel, QPushButton, QSizePolicy

from fatigue_system.core.eye_features import LEFT_EYE_IDX, RIGHT_EYE_IDX
from fatigue_system.core.mouth_features import MOUTH_H_IDX, MOUTH_V_PAIRS
from fatigue_system.core.types import FrameFeatures
from fatigue_system.ui import theme

# 旧 HUD 配色（BGR），仅 draw_hud 兼容保留
_HUD_TEXT = (191, 212, 45)

# 头部状态英文 → 中文（浮层展示用）
_HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}


def draw_landmarks(frame, landmarks_px) -> None:
    """叠加 468 关键点，高亮眼部(绿)/嘴部(红)。原地修改 frame。"""
    for pt in landmarks_px:      # 关键点现为 (N,3)，画点只用 x,y
        cv2.circle(frame, (int(pt[0]), int(pt[1])), 1, (170, 170, 170), -1)
    for i in LEFT_EYE_IDX + RIGHT_EYE_IDX:
        cv2.circle(frame, (int(landmarks_px[i][0]), int(landmarks_px[i][1])), 2, (0, 255, 0), -1)
    mouth_idx = list(MOUTH_H_IDX) + [i for pair in MOUTH_V_PAIRS for i in pair]
    for i in mouth_idx:
        cv2.circle(frame, (int(landmarks_px[i][0]), int(landmarks_px[i][1])), 2, (0, 0, 255), -1)


def draw_hud(frame, ff: FrameFeatures, measured_fps: float) -> None:
    """【已废弃，§5.2 改为 Qt 胶囊浮层】旧版左上角 ASCII HUD。保留仅供兼容。"""
    lines = []
    if ff.face_found:
        lines.append("FACE: OK")
        lines.append("EAR : %.3f" % ff.ear)
        lines.append("MAR : %.3f" % ff.mar)
        lines.append("POSE: P%+.1f Y%+.1f R%+.1f" % (ff.pitch, ff.yaw, ff.roll))
    else:
        lines.append("FACE: NOT FOUND")
    lines.append("FPS : %.1f" % measured_fps)
    x0, y0, line_h, box_w = 10, 10, 22, 232
    box_h = line_h * len(lines) + 12
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + box_w, y0 + box_h), (18, 20, 14), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
    cv2.rectangle(frame, (x0, y0), (x0 + 3, y0 + box_h), _HUD_TEXT, -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (x0 + 12, y0 + 22 + i * line_h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, _HUD_TEXT, 1, cv2.LINE_AA)


class _OverlayPill(QLabel):
    """视频浮层胶囊（§2.4）：半透明深底、全圆角；ok=True 用"正常"绿变体。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.set_ok(False)

    def set_ok(self, ok: bool) -> None:
        bg = theme.PILL_OK_BG if ok else theme.PILL_BG
        fg = theme.PILL_OK_FG if ok else theme.PILL_FG
        self.setStyleSheet(
            "background-color:{bg}; color:{fg}; border-radius: 13px; "
            "padding: 0 12px; font-size: 12px;".format(bg=bg, fg=fg))


class _LandmarkToggle(QPushButton):
    """关键点显示开关（§5.2 右下角）：深色圆形浮钮 + 3×3 点阵图标。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("显示/隐藏关键点叠加")
        self.setStyleSheet(
            "QPushButton {{ background-color: {bg}; border: none; border-radius: 15px; }}"
            .format(bg=theme.PILL_BG))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 选中(显示关键点)时点阵亮，关闭时点阵暗
        c = QColor(theme.PILL_OK_FG if self.isChecked() else theme.PILL_FG)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        cx, cy, gap = self.width() / 2.0, self.height() / 2.0, 4.5
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                p.drawEllipse(QPointF(cx + dx * gap, cy + dy * gap), 1.2, 1.2)
        p.end()


class VideoWidget(QLabel):
    """深色视频容器（§2.4）：show_frame 显示 BGR 帧（等比缩放）、show_message
    显示占位文案；三个浮层子控件贴角布置（§5.2），有画面时才显示。"""

    landmarks_toggled = pyqtSignal(bool)

    _MARGIN = 12   # 浮层与容器边缘间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        # 最小尺寸放宽：布局伸缩由主窗口的比例(55:45)决定，视频内容等比缩放
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 视频容器固定深色（DESIGN.md §2.4）：暗色画面嵌在浅色界面中作视觉锚点
        self.setStyleSheet(
            "background-color:{bg}; color:{dim}; font-size:14px; "
            "border:none; border-radius:12px;".format(
                bg=theme.VIDEO_BG, dim=theme.PILL_FG))

        self._face_pill = _OverlayPill(self)
        self._metrics_pill = _OverlayPill(self)
        self._lm_btn = _LandmarkToggle(self)
        self._lm_btn.toggled.connect(self.landmarks_toggled.emit)

        self.show_message("无视频源\n\n请选择摄像头，或打开视频文件")

    # ------------------------------- 画面 ------------------------------------

    def show_frame(self, frame_bgr) -> None:
        """显示一帧 BGR 图像（转 RGB → QPixmap，按控件大小等比缩放）。"""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        self.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self._set_overlays_visible(True)

    def show_message(self, text: str) -> None:
        """清除画面并显示占位文字（未打开源/已停止/播放结束等），浮层隐藏。"""
        self.setPixmap(QPixmap())
        self.setText(text)
        self._set_overlays_visible(False)

    # ------------------------------- 浮层 ------------------------------------

    def set_overlay(self, ff: FrameFeatures, head_state: str) -> None:
        """每帧更新浮层：右上人脸状态胶囊、左下 EAR/MAR/姿态 数值胶囊。"""
        if ff.face_found:
            self._face_pill.setText("人脸 正常")
            self._face_pill.set_ok(True)
            self._metrics_pill.setText(
                "EAR {:.3f} · MAR {:.3f} · 姿态 {}".format(
                    ff.ear, ff.mar, _HEAD_STATE_CN.get(head_state, "—")))
        else:
            self._face_pill.setText("人脸 丢失")
            self._face_pill.set_ok(False)
            self._metrics_pill.setText("EAR — · MAR — · 姿态 —")
        self._reposition()

    def landmarks_enabled(self) -> bool:
        return self._lm_btn.isChecked()

    def _set_overlays_visible(self, visible: bool) -> None:
        for wgt in (self._face_pill, self._metrics_pill, self._lm_btn):
            wgt.setVisible(visible)

    def _reposition(self) -> None:
        """把三个浮层贴到容器三个角（§5.2）。"""
        m = self._MARGIN
        self._face_pill.adjustSize()
        self._face_pill.move(self.width() - self._face_pill.width() - m, m)
        self._metrics_pill.adjustSize()
        self._metrics_pill.move(m, self.height() - self._metrics_pill.height() - m)
        self._lm_btn.move(self.width() - self._lm_btn.width() - m,
                          self.height() - self._lm_btn.height() - m)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition()
