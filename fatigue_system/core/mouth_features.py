# -*- coding: utf-8 -*-
"""嘴部特征：MAR 嘴纵横比（开发规格书 §6.4）。

MAR (Mouth Aspect Ratio) 度量嘴巴张开程度：闭口时接近 0，张口时升高。

数值尺度注意：本公式对三组垂直对取均值，而两侧对（81/178、311/402 靠近
嘴角）的张开幅度远小于中央对（13/14），故整体数值比"仅中央对"的教科书
公式低得多——实测 闭嘴≈0.02~0.03、打哈欠峰值≈0.3（因人而异）。哈欠阈值
见 config 的 mouth.mar_yawn_thresh，按此尺度设定，勿照搬文献里的 0.6。

MediaPipe FaceMesh 468 关键点索引（已用叠加可视化核对落点正确）：
    水平（嘴角）pm1, pm5 = [61, 291]
    垂直三对         = (81, 178), (13, 14), (311, 402)
    MAR = (Σ|垂直对|) / (3 * |pm1 - pm5|)
"""

import numpy as np

# 规格书 §6.4 指定索引
MOUTH_H_IDX = [61, 291]                       # 左右嘴角（水平）
MOUTH_V_PAIRS = [(81, 178), (13, 14), (311, 402)]  # 三组上下唇垂直对


def compute_mar(landmarks_px) -> float:
    """计算 MAR 嘴纵横比。

    参数:
        landmarks_px —— np.ndarray (468, 2) 像素坐标关键点。
    返回:
        MAR（无量纲）；输入无效时返回 0.0。
    """
    if landmarks_px is None or len(landmarks_px) < 468:
        return 0.0
    landmarks_px = landmarks_px[:, :2]   # MAR 用 2D（保持既有尺度与哈欠阈值）
    horizontal = np.linalg.norm(landmarks_px[MOUTH_H_IDX[0]] - landmarks_px[MOUTH_H_IDX[1]])
    if horizontal < 1e-6:
        return 0.0
    vertical = 0.0
    for a, b in MOUTH_V_PAIRS:
        vertical += np.linalg.norm(landmarks_px[a] - landmarks_px[b])
    return float(vertical / (3.0 * horizontal))
