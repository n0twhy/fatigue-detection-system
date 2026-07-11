# -*- coding: utf-8 -*-
"""个性化基线校准（开发规格书 §6.8，对应 §3 延伸③）。

启动时采集前 calibration.duration_sec 秒的清醒态数据，统计：
    * 睁眼 EAR 均值/标准差（此时用户应睁眼）
    * 闭口 MAR 均值（此时用户应闭口）
    * 头部中性角均值（pitch/yaw/roll，作为头姿判定零点）
    * 静息 HR 均值（M4 有 rPPG 才有）
产出 Baseline，供各子分把"绝对阈值"改为"相对本人基线"。

使用约定：校准阶段请用户保持清醒、睁眼、闭口、头部中正、正对镜头。
"""

from typing import Optional

import numpy as np

from fatigue_system.core.types import Baseline


class BaselineCalibrator:
    """基线采集器。

    构造参数:
        cfg —— 完整配置字典（用到 calibration.duration_sec）。
    """

    # 校准所需的最少有效帧数（低于此判为失败，避免噪声基线）
    _MIN_SAMPLES = 10

    def __init__(self, cfg):
        cfg = cfg or {}
        cal = cfg.get("calibration", {})
        self._duration = float(cal.get("duration_sec", 30))
        self._ears = []
        self._mars = []
        self._pitches = []
        self._yaws = []
        self._rolls = []
        self._hrs = []
        self._t0: Optional[float] = None
        self._t_last: Optional[float] = None

    def push(self, ff, hr: Optional[float] = None) -> None:
        """压入一帧；仅统计检出人脸的帧。hr 可选（M4 提供）。"""
        if not ff.face_found:
            return
        if self._t0 is None:
            self._t0 = ff.ts
        self._t_last = ff.ts
        self._ears.append(ff.ear)
        self._mars.append(ff.mar)
        self._pitches.append(ff.pitch)
        self._yaws.append(ff.yaw)
        self._rolls.append(ff.roll)
        if hr is not None:
            self._hrs.append(hr)

    def elapsed(self) -> float:
        """已采集时长（秒），按帧时间戳计算。"""
        if self._t0 is None or self._t_last is None:
            return 0.0
        return self._t_last - self._t0

    def progress(self) -> float:
        """校准进度 0..1。"""
        if self._duration <= 0:
            return 1.0
        return min(1.0, self.elapsed() / self._duration)

    def is_done(self) -> bool:
        """是否已采够时长且样本充足。"""
        return self.elapsed() >= self._duration and len(self._ears) >= self._MIN_SAMPLES

    def finalize(self) -> Baseline:
        """结算基线；样本不足则返回 valid=False 的基线。"""
        if len(self._ears) < self._MIN_SAMPLES:
            return Baseline(valid=False, n_samples=len(self._ears))
        return Baseline(
            ear_open_mean=float(np.mean(self._ears)),
            ear_open_std=float(np.std(self._ears)),
            mar_closed_mean=float(np.mean(self._mars)),
            pitch=float(np.mean(self._pitches)),
            yaw=float(np.mean(self._yaws)),
            roll=float(np.mean(self._rolls)),
            hr_rest=float(np.mean(self._hrs)) if self._hrs else None,
            n_samples=len(self._ears),
            valid=True,
        )
