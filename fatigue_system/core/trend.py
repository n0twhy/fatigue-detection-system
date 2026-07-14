# -*- coding: utf-8 -*-
"""疲劳趋势预警（v1.11 功能③）：在"还没到重度"之前提前提醒。

思路：报警是**事后**的（已经重度了才响铃），而疲劳是**逐渐累积**的。本模块对
融合分做滑窗**线性回归**，斜率持续为正（且达到阈值）说明疲劳正在上升——此时
给一次温和提示"疲劳正在累积，建议休息"，体现任务书要求的"预**警**"。

与报警的区别（重要，别把它变成新的误报源）：
  * 不走 AlarmFSM、不响铃、不弹窗，只在界面上给一行温和提示；
  * 触发后有冷却期（trend.cooldown_sec），不会反复刷；
  * 只看趋势不看绝对值，且要求**分数已离开清醒区**（min_score），避免在
    0.05→0.10 这种毫无意义的低分区间瞎提醒。

不训练任何模型：最小二乘斜率是闭式解，配置驱动。
"""

from collections import deque
from typing import Dict, Optional


class TrendMonitor:
    """融合分上升趋势监测器（每个融合节拍喂一次）。"""

    def __init__(self, cfg: Dict):
        self.reconfigure(cfg)
        self.reset()

    def reconfigure(self, cfg: Dict) -> None:
        """读取/热更新配置（「参数设置」改参后调用）。"""
        t = (cfg or {}).get("trend", {})
        self._enable = bool(t.get("enable", True))
        self._window_sec = float(t.get("window_sec", 300))      # 回归窗口（秒）
        self._min_samples = int(t.get("min_samples", 60))       # 窗口内最少样本
        self._slope_per_min = float(t.get("slope_per_min", 0.05))  # 上升斜率阈（分/分钟）
        self._min_score = float(t.get("min_score", 0.20))       # 低于此分不提醒（噪声区）
        self._cooldown_sec = float(t.get("cooldown_sec", 300))  # 提醒冷却（秒）
        self._hold_windows = int(t.get("hold_windows", 3))      # 连续 N 窗满足才提醒

    def reset(self) -> None:
        """复位（切换视频源/重新开始检测时调用）。"""
        self._buf = deque()            # [(ts, score)]
        self._hit_run = 0
        self._last_alert_ts: Optional[float] = None
        self._slope = 0.0

    @property
    def slope_per_min(self) -> float:
        """当前拟合斜率（融合分 / 分钟），供界面或日志展示。"""
        return self._slope

    def update(self, ts: float, score: float) -> bool:
        """喂入一个融合节拍的 (时间戳, 融合分)；返回本次是否应给出趋势提醒。

        提醒条件（同时满足）：
          ① 窗口内样本足够；
          ② 最小二乘斜率 ≥ slope_per_min（疲劳在上升）；
          ③ 当前分数已离开清醒噪声区（≥ min_score）；
          ④ 连续 hold_windows 个节拍都满足（防抖）；
          ⑤ 不在冷却期内。
        """
        if not self._enable:
            return False
        # 时间戳回退（换源/视频循环）→ 清空重来
        if self._buf and ts < self._buf[-1][0]:
            self.reset()
        self._buf.append((ts, float(score)))
        t_min = ts - self._window_sec
        while self._buf and self._buf[0][0] < t_min:
            self._buf.popleft()

        self._slope = self._fit_slope()
        rising = (len(self._buf) >= self._min_samples
                  and self._slope >= self._slope_per_min
                  and float(score) >= self._min_score)
        self._hit_run = self._hit_run + 1 if rising else 0

        if self._hit_run < self._hold_windows:
            return False
        if (self._last_alert_ts is not None
                and ts - self._last_alert_ts < self._cooldown_sec):
            return False
        self._last_alert_ts = ts
        self._hit_run = 0
        return True

    def _fit_slope(self) -> float:
        """最小二乘拟合 score 对时间的斜率，换算为「分/分钟」。

        闭式解 slope = Σ(t-t̄)(s-s̄) / Σ(t-t̄)²；分母为 0（样本挤在同一时刻）时返回 0。
        """
        n = len(self._buf)
        if n < 2:
            return 0.0
        t_mean = sum(t for t, _s in self._buf) / n
        s_mean = sum(s for _t, s in self._buf) / n
        num = sum((t - t_mean) * (s - s_mean) for t, s in self._buf)
        den = sum((t - t_mean) ** 2 for t, _s in self._buf)
        if den <= 0:
            return 0.0
        return (num / den) * 60.0      # 每秒 → 每分钟
