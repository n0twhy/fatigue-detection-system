# -*- coding: utf-8 -*-
"""眼部特征：EAR 眼纵横比（开发规格书 §6.3）。

EAR (Eye Aspect Ratio, Soukupová & Čech 2016) 用 6 个眼周关键点度量眼睛
张开程度：睁眼时约 0.25~0.35，闭眼时骤降至 ~0.1 以下。

MediaPipe FaceMesh 468 关键点索引（已用叠加可视化核对落点正确）：
    左眼 p1..p6 = [33, 160, 158, 133, 153, 144]
    右眼 p1..p6 = [362, 385, 387, 263, 373, 380]
其中 p1、p4 为眼睛水平内外角，(p2,p6)、(p3,p5) 为上下眼睑对应点。
    EAR = (|p2-p6| + |p3-p5|) / (2*|p1-p4|)
"""

from typing import Tuple

import numpy as np

# 规格书 §6.3 指定的左右眼 6 点索引
LEFT_EYE_IDX = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]


def _single_ear(landmarks_px, idx) -> float:
    """按 6 点计算单眼 EAR。"""
    p = [landmarks_px[i] for i in idx]
    vertical = np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])
    horizontal = np.linalg.norm(p[0] - p[3])
    if horizontal < 1e-6:
        return 0.0
    return float(vertical / (2.0 * horizontal))


def compute_ear(landmarks_px) -> Tuple[float, float, float]:
    """计算双眼平均 EAR 与左右眼 EAR。

    参数:
        landmarks_px —— np.ndarray (468, 2) 像素坐标关键点。
    返回:
        (ear, left_ear, right_ear)，均为无量纲；输入无效时返回 (0,0,0)。
    """
    if landmarks_px is None or len(landmarks_px) < 468:
        return 0.0, 0.0, 0.0
    left = _single_ear(landmarks_px, LEFT_EYE_IDX)
    right = _single_ear(landmarks_px, RIGHT_EYE_IDX)
    return (left + right) / 2.0, left, right
