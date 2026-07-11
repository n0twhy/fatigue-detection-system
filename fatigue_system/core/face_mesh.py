# -*- coding: utf-8 -*-
"""MediaPipe FaceMesh 关键点封装（开发规格书 §6.2）。

职责：把一帧 BGR 图像送入 MediaPipe FaceMesh，产出：
    * landmarks_px —— 468 个关键点的像素坐标 np.ndarray, shape=(468, 2)
    * roi_rgb      —— 面颊+鼻部肤色 ROI 的 (R, G, B) 均值，供实时 rPPG 使用

所有参数（最多人脸数、是否精修虹膜、检测/跟踪置信度）取自 config.yaml 的
facemesh 段，不写死。
"""

import os

# 降低 mediapipe/absl 的 INFO 日志噪声（须在导入 mediapipe 前设置）
os.environ.setdefault("GLOG_minloglevel", "2")

from typing import Dict, Optional, Tuple

import numpy as np
import cv2
import mediapipe as mp

# 面颊+鼻部 ROI 采样用的关键点索引（在这些点周围取小块肤色区求均值）。
# 选取避开眉毛/眼睛/嘴唇的稳定肤色区：左右脸颊各若干点 + 鼻梁鼻尖。
_ROI_LANDMARKS = [
    50, 101, 118, 205,      # 左脸颊
    280, 330, 347, 425,     # 右脸颊
    1, 4, 195, 5,           # 鼻部
]
# 每个 ROI 采样点周围取 (2*_ROI_HALF+1) 见方的小块
_ROI_HALF = 4


class FaceMeshDetector:
    """MediaPipe FaceMesh 检测器封装。

    典型用法：
        det = FaceMeshDetector(cfg)          # cfg 为完整配置字典或含 facemesh 段
        landmarks_px, roi_rgb = det.process(frame_bgr)
        ...
        det.close()
    """

    def __init__(self, cfg: Optional[Dict] = None):
        cfg = cfg or {}
        # 允许传入完整配置或直接传 facemesh 段
        fm_cfg = cfg.get("facemesh", cfg) if isinstance(cfg, dict) else {}
        self._mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=int(fm_cfg.get("max_num_faces", 1)),
            refine_landmarks=bool(fm_cfg.get("refine_landmarks", False)),
            min_detection_confidence=float(fm_cfg.get("min_detection_confidence", 0.5)),
            min_tracking_confidence=float(fm_cfg.get("min_tracking_confidence", 0.5)),
        )

    def process(self, frame_bgr) -> Tuple[Optional[np.ndarray], Optional[Tuple[float, float, float]]]:
        """处理一帧，返回 (landmarks_px, roi_rgb)。

        参数:
            frame_bgr —— OpenCV BGR 图像 (H, W, 3)。
        返回:
            landmarks_px —— np.ndarray (468, 2) 像素坐标；未检出人脸时为 None。
            roi_rgb      —— (R, G, B) 均值(0..255)；未检出或无有效像素时为 None。
        """
        if frame_bgr is None:
            return None, None
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        # mediapipe 要求输入为连续的 RGB 数组
        rgb = np.ascontiguousarray(rgb)
        result = self._mesh.process(rgb)
        if not result.multi_face_landmarks:
            return None, None
        lm = result.multi_face_landmarks[0].landmark
        pts = np.empty((len(lm), 2), dtype=np.float32)
        for i, p in enumerate(lm):
            pts[i, 0] = p.x * w
            pts[i, 1] = p.y * h
        roi_rgb = self._compute_roi_rgb(rgb, pts)
        return pts, roi_rgb

    @staticmethod
    def _compute_roi_rgb(rgb_img, pts) -> Optional[Tuple[float, float, float]]:
        """在面颊+鼻部若干关键点周围采样肤色小块，返回 (R,G,B) 均值。"""
        h, w = rgb_img.shape[:2]
        samples = []
        for idx in _ROI_LANDMARKS:
            if idx >= len(pts):
                continue
            cx, cy = int(round(pts[idx, 0])), int(round(pts[idx, 1]))
            x0, x1 = max(0, cx - _ROI_HALF), min(w, cx + _ROI_HALF + 1)
            y0, y1 = max(0, cy - _ROI_HALF), min(h, cy + _ROI_HALF + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            patch = rgb_img[y0:y1, x0:x1].reshape(-1, 3)
            samples.append(patch)
        if not samples:
            return None
        allpix = np.concatenate(samples, axis=0).astype(np.float32)
        r, g, b = allpix.mean(axis=0)
        return float(r), float(g), float(b)

    def close(self) -> None:
        """释放 MediaPipe 资源。"""
        if self._mesh is not None:
            self._mesh.close()
            self._mesh = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
