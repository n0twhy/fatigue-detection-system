# -*- coding: utf-8 -*-
"""实时 rPPG：滚动缓冲 + POS → HR / HRV（开发规格书 §6.6）。

数据流（辅线，约每 update_interval_sec 估计一次）：
    每帧 roi_rgb (面颊+鼻部 RGB 均值) ─► 滚动缓冲(buffer_sec)
        ─► 按时间戳重采样到均匀网格 ─► POS 投影得脉搏波(BVP)
        ─► 带通 0.7–3Hz ─► FFT 峰值频率 → HR(bpm)
        ─► BVP 峰检测 → 峰间期 → HRV(RMSSD, ms)

POS 核心算法移植自 Wang et al. 2017 "Algorithmic Principles of Remote PPG"
(IEEE TBME 64(7))，实现参考了 rPPG-Toolbox 项目的 POS_WANG.py（其实现只接受
原始视频帧、内部自算帧均值，无法直接喂 RGB 均值序列，故将核心移植至此、
输入改为逐帧 ROI 均值迹，本模块因此不依赖该项目、可独立运行）。

与离线实现的差异（实时化适配）：
  * 输入是"面颊+鼻部 ROI 均值"而非全帧均值——肤色占比高、信噪比更好；
  * 帧到达间隔不均匀（相机供帧波动），先按 ts 线性重采样到均匀网格，
    否则 FFT 频率轴失真、HR 系统性偏移；
  * 用 0.7Hz 高通(带通下沿)代替离线的 Tarvainen 去趋势（10s 短窗内等效）。

精度定位（务必如实写进报告，勿夸大）：
  * HR ——**主要生理输出**，实测对 UBFC 真值逐秒 MAE≈8bpm（POS 无监督的
    合理范围），并作为 physio 子分（相对静息基线的心率下降）汇入融合。
  * HRV(RMSSD) ——**仅粗略趋势参考**。10s 短窗 + CPU 网络摄像头下，逐搏峰
    定时噪声底就有 ~30-130ms，会淹没真实 HRV(~20-60ms)，故本模块对 HRV 施加
    "频谱 HR 一致性门控"抑制离谱值，但**不保证数值精度**，不参与融合判定，
    只在界面/CSV 作辅助显示。报告应说明此局限，不当临床 HRV 用。
"""

import math
from collections import deque
from typing import Optional, Tuple

import numpy as np
from scipy import signal


class RealtimeRPPG:
    """滚动缓冲 POS 心率/HRV 估计器。

    构造参数:
        fps —— 视频源名义帧率（仅作参考；实际计算按帧时间戳重采样）。
        cfg —— 完整配置字典（用 rppg 段）。
    """

    def __init__(self, fps: float, cfg):
        rp = (cfg or {}).get("rppg", {})
        self._fps = float(fps) if fps and fps > 0 else 20.0
        self._buffer_sec = float(rp.get("buffer_sec", 10))
        self._interval = float(rp.get("update_interval_sec", 1.0))
        self._low_hz = float(rp.get("bandpass_low_hz", 0.7))
        self._high_hz = float(rp.get("bandpass_high_hz", 3.0))
        self._resample_fs = float(rp.get("resample_fs", 30))
        self._pos_win_sec = float(rp.get("pos_win_sec", 1.6))
        self._min_ratio = float(rp.get("min_buffer_ratio", 0.8))
        self._max_gap = float(rp.get("max_gap_sec", 1.0))
        # 心率时间域平滑：对最近 N 次逐秒估计做中值滤波，压制"谐波/噪声峰
        # 抢占"导致的单次跳变（如 100→60→100）。中值对孤立异常值最鲁棒。
        self._hr_smooth_n = max(1, int(rp.get("hr_smooth_window", 5)))
        self._hr_history = deque(maxlen=self._hr_smooth_n)
        # 缓冲：(ts, r, g, b)
        self._buf = deque()
        # 估计缓存（按 update_interval_sec 节拍重算，其余时刻返回缓存）
        self._last_est_ts: Optional[float] = None
        self._cached: Tuple[Optional[float], Optional[float]] = (None, None)

    # ------------------------------- 数据流 ----------------------------------

    def update(self, roi_rgb, ts: float) -> None:
        """压入一帧 ROI RGB 均值；按 buffer_sec 裁剪滚动缓冲。

        参数:
            roi_rgb —— (R, G, B) 均值(0..255)；None（未检出人脸）时忽略。
            ts      —— 帧时间戳（秒）。
        """
        if roi_rgb is None:
            return
        # 时间戳回退（视频文件循环播放回绕/seek）→ 旧缓冲作废，
        # 否则非单调 ts 会毁掉重采样网格
        if self._buf and ts < self._buf[-1][0]:
            self.reset()
        self._buf.append((float(ts), float(roi_rgb[0]), float(roi_rgb[1]), float(roi_rgb[2])))
        t_min = ts - self._buffer_sec
        while self._buf and self._buf[0][0] < t_min:
            self._buf.popleft()

    def reset(self) -> None:
        """清空缓冲与缓存（切换视频源时调用）。"""
        self._buf.clear()
        self._hr_history.clear()
        self._last_est_ts = None
        self._cached = (None, None)

    # ------------------------------- 估计 ------------------------------------

    def estimate(self) -> Tuple[Optional[float], Optional[float]]:
        """返回 (hr_bpm, hrv_rmssd_ms)；数据不足/质量不够时对应项为 None。

        内部按 update_interval_sec 节拍重算（以缓冲末帧 ts 计），
        其余调用返回上次结果，可放心在 GUI 帧循环里高频调用。
        """
        if not self._buf:
            return self._cached
        now = self._buf[-1][0]
        if self._last_est_ts is not None and now - self._last_est_ts < self._interval:
            return self._cached
        self._last_est_ts = now
        self._cached = self._estimate_once()
        return self._cached

    def _estimate_once(self) -> Tuple[Optional[float], Optional[float]]:
        data = np.asarray(self._buf, dtype=np.float64)   # (N, 4): ts, r, g, b
        if len(data) < 4:
            return None, None
        ts = data[:, 0]
        span = ts[-1] - ts[0]
        # 缓冲未填满 / 中断（丢脸）过长 → 不出数
        if span < self._buffer_sec * self._min_ratio:
            return None, None
        if np.max(np.diff(ts)) > self._max_gap:
            return None, None

        # 1) 按时间戳重采样到均匀网格（相机供帧不均匀，直接 FFT 频率轴会失真）
        fs = self._resample_fs
        n = int(span * fs)
        if n < int(self._pos_win_sec * fs) + 2:
            return None, None
        grid = ts[0] + np.arange(n) / fs
        rgb = np.column_stack([np.interp(grid, ts, data[:, c]) for c in (1, 2, 3)])

        # 2) POS 投影（Wang 2017；滑窗 overlap-add，窗长 pos_win_sec）
        bvp = self._pos(rgb, fs)

        # 3) 带通 0.7–3Hz（高通沿同时承担去趋势）
        try:
            b, a = signal.butter(
                1, [self._low_hz / fs * 2, self._high_hz / fs * 2], btype="bandpass")
            bvp = signal.filtfilt(b, a, bvp)
        except ValueError:
            return None, None
        if not np.any(np.isfinite(bvp)) or np.std(bvp) < 1e-12:
            return None, None

        raw_hr = self._hr_from_fft(bvp, fs)
        # HRV 用本窗口的原始 HR 做门控（更贴合当前 BVP），HR 显示值再做平滑
        hrv = self._hrv_from_peaks(bvp, fs, raw_hr)
        hr = self._smooth_hr(raw_hr)
        return hr, hrv

    def _smooth_hr(self, raw_hr: Optional[float]) -> Optional[float]:
        """对逐秒 HR 做滑动中值滤波，抑制单次跳变。"""
        if raw_hr is None:
            return float(np.median(self._hr_history)) if self._hr_history else None
        self._hr_history.append(raw_hr)
        return float(np.median(self._hr_history))

    def _pos(self, rgb: np.ndarray, fs: float) -> np.ndarray:
        """POS 核心：滑窗归一化 + 投影 + overlap-add，输出脉搏波。"""
        n_total = rgb.shape[0]
        h_out = np.zeros(n_total)
        l = int(math.ceil(self._pos_win_sec * fs))
        proj = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])
        for n in range(l, n_total + 1):
            m = n - l
            win = rgb[m:n, :]
            mean = win.mean(axis=0)
            if np.any(mean <= 0):
                continue
            cn = (win / mean).T                    # (3, l)
            s = proj @ cn                          # (2, l)
            std1 = np.std(s[1])
            alpha = (np.std(s[0]) / std1) if std1 > 1e-12 else 0.0
            h = s[0] + alpha * s[1]
            h_out[m:n] += h - h.mean()
        return h_out

    def _hr_from_fft(self, bvp: np.ndarray, fs: float) -> Optional[float]:
        """FFT 频谱峰 → HR(bpm)。零填充细化频率分辨率（10s 窗原生仅 0.1Hz≈6bpm）。"""
        n = len(bvp)
        nfft = 1 << max(12, (n - 1).bit_length() + 3)   # ≥8×零填充
        spec = np.abs(np.fft.rfft(bvp * np.hanning(n), nfft))
        freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        band = (freqs >= self._low_hz) & (freqs <= self._high_hz)
        if not np.any(band):
            return None
        peak_f = freqs[band][int(np.argmax(spec[band]))]
        return float(peak_f * 60.0)

    def _hrv_from_peaks(self, bvp: np.ndarray, fs: float, hr: Optional[float]) -> Optional[float]:
        """BVP 峰间期 → RMSSD(ms)。质量不达标时返回 None（宁缺勿滥）。

        HRV 对峰检测质量极敏感：漏检一个搏动就让某个间期翻倍、RMSSD 爆表。
        故采用"频谱 HR 一致性门控"——只有当逐搏峰间期与 FFT 估计的 HR 相互
        印证时才输出 HRV，否则判定本窗口峰检测不可靠、返回 None。

        注意：即便通过门控，10s 短窗 + CPU 网络摄像头 rPPG 的 HRV 仍只具
        参考意义（存在 ~30-50ms 级的峰定时噪声底），精度远不及 ECG，
        报告中须如实说明，不可当临床 HRV 用。
        """
        if hr is None or hr <= 0:
            return None
        expected_ibi = 60000.0 / hr                     # 由频谱 HR 推得的期望间期(ms)
        hr_hz = hr / 60.0
        min_dist = max(1, int(fs / (hr_hz * 1.4)))      # 峰间距下限≈期望的 0.7 倍
        peaks, _ = signal.find_peaks(bvp, distance=min_dist,
                                     prominence=np.std(bvp) * 0.6)
        if len(peaks) < 5:
            return None
        # 抛物线插值：用峰两侧样本细化峰位到亚样本，消除整数量化抖动(±33ms@30fps)
        refined = []
        for p in peaks:
            if 0 < p < len(bvp) - 1:
                y0, y1, y2 = bvp[p - 1], bvp[p], bvp[p + 1]
                denom = y0 - 2 * y1 + y2
                offset = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
                refined.append(p + max(-0.5, min(0.5, offset)))
            else:
                refined.append(float(p))
        ibis_ms = np.diff(refined) / fs * 1000.0
        # 门控①：逐间期须落在期望的 [0.7, 1.4] 倍内（挡漏检/假峰）
        keep = ibis_ms[(ibis_ms > 0.7 * expected_ibi) & (ibis_ms < 1.4 * expected_ibi)]
        # 门控②：合格间期须占多数（>70%），否则整窗判为不可靠
        if len(keep) < 4 or len(keep) < 0.7 * len(ibis_ms):
            return None
        # 门控③：合格间期均值须与期望 HR 相符（±12%），确认峰序列即搏动序列
        if abs(np.mean(keep) - expected_ibi) > 0.12 * expected_ibi:
            return None
        diff = np.diff(keep)
        if len(diff) < 2:
            return None
        rmssd = float(np.sqrt(np.mean(diff ** 2)))
        # 门控④ 生理上限：真实 RMSSD 极少超过平均间期的 ~20%（72bpm→~166ms）。
        # 超过即说明峰序列被检测噪声主导（漏检/切迹假峰），本窗口 HRV 不可信，
        # 返回 None（诚实标"不可靠"）而非给出错误数值。
        if rmssd > 0.20 * expected_ibi:
            return None
        return rmssd
