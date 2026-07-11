# -*- coding: utf-8 -*-
"""头部姿态：solvePnP 估计俯仰/偏航/翻滚 + 状态分类（开发规格书 §6.5）。

用 cv2.solvePnP 将 6 个通用 3D 人脸模型点与对应的 MediaPipe 2D 关键点对齐，
解出头部旋转，再经 Rodrigues + RQDecomp3x3 分解为欧拉角：
    pitch 俯仰（点头/低头）、yaw 偏航（左右转头）、roll 翻滚（歪头）。

6 点对应（MediaPipe 索引）：
    鼻尖 1、下巴 152、左眼外角 33、右眼外角 263、左嘴角 61、右嘴角 291。
相机内参用图像宽近似焦距、图像中心为主点、畸变置零。
"""

from typing import Optional, Tuple

import numpy as np
import cv2

# 头姿 6 点对应的 MediaPipe 关键点索引（顺序与下方 3D 模型点一一对应）
POSE_LANDMARK_IDX = [1, 152, 33, 263, 61, 291]

# 通用 3D 人脸模型点（单位近似毫米，鼻尖为原点），顺序同上
_MODEL_POINTS_3D = np.array([
    [0.0, 0.0, 0.0],        # 鼻尖 1
    [0.0, -63.6, -12.5],    # 下巴 152
    [-43.3, 32.7, -26.0],   # 左眼外角 33
    [43.3, 32.7, -26.0],    # 右眼外角 263
    [-28.9, -28.9, -24.1],  # 左嘴角 61
    [28.9, -28.9, -24.1],   # 右嘴角 291
], dtype=np.float64)


def _normalize_angle(a: float) -> float:
    """把 RQDecomp3x3 分解出的角度归一到 (-90, 90] 附近。

    solvePnP+RQDecomp 常把俯仰角解成 ±180° 翻转值（如正视时 pitch≈-167°），
    这里做 ±180° 折叠，使正视≈0°、便于阅读与相对基线判定。
    """
    while a > 90.0:
        a -= 180.0
    while a <= -90.0:
        a += 180.0
    return a


def estimate_head_pose(landmarks_px, image_shape) -> Tuple[float, float, float]:
    """估计头部欧拉角（度）。

    参数:
        landmarks_px —— np.ndarray (468, 2) 像素坐标关键点。
        image_shape  —— 图像 shape，(H, W) 或 (H, W, C)。
    返回:
        (pitch, yaw, roll)，单位度；失败或输入无效时返回 (0.0, 0.0, 0.0)。
    """
    if landmarks_px is None or len(landmarks_px) < 468:
        return 0.0, 0.0, 0.0
    h, w = image_shape[0], image_shape[1]
    image_pts = np.array([landmarks_px[i] for i in POSE_LANDMARK_IDX], dtype=np.float64)
    focal = float(w)
    cam_matrix = np.array([
        [focal, 0, w / 2.0],
        [0, focal, h / 2.0],
        [0, 0, 1],
    ], dtype=np.float64)
    dist = np.zeros((4, 1), dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(
        _MODEL_POINTS_3D, image_pts, cam_matrix, dist, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return 0.0, 0.0, 0.0
    rmat, _ = cv2.Rodrigues(rvec)
    angles, *_ = cv2.RQDecomp3x3(rmat)
    pitch = _normalize_angle(angles[0])
    yaw = _normalize_angle(angles[1])
    roll = _normalize_angle(angles[2])
    return pitch, yaw, roll


def _baseline_angle(baseline, name: str) -> float:
    """从基线对象/字典中取中性角；无基线时返回 0。"""
    if baseline is None:
        return 0.0
    # 兼容 dataclass（属性）与 dict（键）两种形态
    for attr in (name, "neutral_" + name, name + "_mean"):
        if hasattr(baseline, attr):
            return float(getattr(baseline, attr))
        if isinstance(baseline, dict) and attr in baseline:
            return float(baseline[attr])
    return 0.0


def classify_head_state(pitch: float, yaw: float, roll: float, baseline, cfg) -> str:
    """根据（相对基线的）头姿偏差分类为 normal/lowered/tilted。

    参数:
        pitch, yaw, roll —— 当前头姿角（度）。
        baseline —— 个性化基线（含中性角）；M1 阶段可为 None（以 0 为中性）。
        cfg      —— 完整配置或其 head 段，用到 pitch_lower_thresh_deg、
                    yaw_tilt_thresh_deg。
    返回:
        'normal' | 'lowered' | 'tilted'（点头 nodding 属时序模式，由滑窗模块判定）。

    约定：pitch 相对基线增大记为低头方向；该正负方向将在 M1 低头自测中最终确认，
    如相反只需调整符号。
    """
    head_cfg = cfg.get("head", cfg) if isinstance(cfg, dict) else {}
    pitch_thr = float(head_cfg.get("pitch_lower_thresh_deg", 15))
    yaw_thr = float(head_cfg.get("yaw_tilt_thresh_deg", 20))

    d_pitch = pitch - _baseline_angle(baseline, "pitch")
    d_yaw = yaw - _baseline_angle(baseline, "yaw")
    d_roll = roll - _baseline_angle(baseline, "roll")

    if d_pitch > pitch_thr:
        return "lowered"
    if abs(d_yaw) > yaw_thr or abs(d_roll) > yaw_thr:
        return "tilted"
    return "normal"
