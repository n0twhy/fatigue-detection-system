# -*- coding: utf-8 -*-
"""① 视频显示区（开发规格书 §4 / §6.12）。

包含：
    * draw_landmarks / draw_hud —— 关键点与 HUD 叠加（M1 起使用，
      M3 从 main_window 迁入本模块）
    * VideoWidget —— 承载视频画面的 QLabel 封装（BGR→QPixmap、等比缩放、
      占位文案）

注意：cv2.putText 不支持中文，HUD 只用 ASCII；中文一律放 Qt 控件。
"""

import cv2
import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QSizePolicy

from fatigue_system.core.eye_features import LEFT_EYE_IDX, RIGHT_EYE_IDX
from fatigue_system.core.mouth_features import MOUTH_H_IDX, MOUTH_V_PAIRS
from fatigue_system.core.types import FrameFeatures
from fatigue_system.ui import theme

# HUD 配色（BGR）：青色主调，呼应界面点缀色
_HUD_TEXT = (191, 212, 45)      # ≈ teal #2dd4bf
_HUD_DIM = (150, 150, 140)


def draw_landmarks(frame, landmarks_px) -> None:
    """叠加 468 关键点，高亮眼部(绿)/嘴部(红)。原地修改 frame。"""
    for (x, y) in landmarks_px:
        cv2.circle(frame, (int(x), int(y)), 1, (170, 170, 170), -1)
    for i in LEFT_EYE_IDX + RIGHT_EYE_IDX:
        cv2.circle(frame, (int(landmarks_px[i][0]), int(landmarks_px[i][1])), 2, (0, 255, 0), -1)
    mouth_idx = list(MOUTH_H_IDX) + [i for pair in MOUTH_V_PAIRS for i in pair]
    for i in mouth_idx:
        cv2.circle(frame, (int(landmarks_px[i][0]), int(landmarks_px[i][1])), 2, (0, 0, 255), -1)


def draw_hud(frame, ff: FrameFeatures, measured_fps: float) -> None:
    """左上角半透明 HUD：EAR/MAR/头姿/FPS（ASCII，避免中文乱码）。原地修改。"""
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
    # 左侧青色竖条点缀
    cv2.rectangle(frame, (x0, y0), (x0 + 3, y0 + box_h), _HUD_TEXT, -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (x0 + 12, y0 + 22 + i * line_h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, _HUD_TEXT, 1, cv2.LINE_AA)


class VideoWidget(QLabel):
    """视频画面控件：show_frame 显示 BGR 帧（等比缩放），show_message 显示文案。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(640, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background-color:#05070a; color:{dim}; font-size:14px; "
            "border:1px solid {border}; border-radius:12px;".format(
                dim=theme.TEXT_MUTE, border=theme.BORDER))
        self.show_message("无视频源\n\n请选择摄像头，或打开视频文件")

    def show_frame(self, frame_bgr) -> None:
        """显示一帧 BGR 图像（转 RGB → QPixmap，按控件大小等比缩放）。"""
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        self.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def show_message(self, text: str) -> None:
        """清除画面并显示占位文字（未打开源/已停止/播放结束等）。"""
        self.setPixmap(QPixmap())
        self.setText(text)
