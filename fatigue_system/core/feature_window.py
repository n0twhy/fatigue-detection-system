# -*- coding: utf-8 -*-
"""滑窗特征聚合（开发规格书 §6.7）。

把逐帧 FrameFeatures 在滑动时间窗内聚合为 WindowFeatures：
    * PERCLOS     —— 窗口内闭眼帧占比
    * 眨眼率       —— EAR 穿越闭眼阈值 + 时长去抖计数，换算为 次/分
    * 最长闭眼时长 —— 窗口内最长连续闭眼段（秒）
    * 哈欠计数/标志—— MAR 高于阈值且持续 ≥ 最短哈欠时长记为一次哈欠
    * 头部状态     —— normal/lowered/tilted/nodding
    * 点头计数     —— 俯仰角周期性摆动次数

所有时长/去抖一律用帧携带的时间戳(ts)计算，**不依赖名义帧率**——
usbip 摄像头暗光下实际供帧可低至 ~8fps（而驱动上报 30fps），任何
"按帧数×名义帧率"的判定在低帧率下都会失效（曾导致眨眼完全检不出）。
连续段时长采用"含帧间隔"语义：段 = 首个满足条件的帧 → 其后首个不满足
条件的帧，这样单帧事件在低帧率下代表一个完整帧间隔而非 0 秒。
闭眼阈值可被个性化基线覆盖（§3 延伸③）：set_baseline 后阈值 =
本人睁眼 EAR 均值 × eye.ear_closed_ratio。
"""

from collections import deque
from typing import Optional

import numpy as np

from fatigue_system.core.types import (
    WindowFeatures, Baseline,
    HEAD_STATE_NORMAL, HEAD_STATE_NODDING,
)
from fatigue_system.core.head_pose import classify_head_state


class FeatureAggregator:
    """滑窗聚合器。

    构造参数:
        cfg —— 完整配置字典（用到 eye/mouth/head 段）。
        fps —— 当前源帧率，作为时长换算兜底。
    """

    def __init__(self, cfg, fps):
        cfg = cfg or {}
        self._cfg = cfg
        self._fps = float(fps) if fps and fps > 0 else 20.0
        self._load_params(cfg)
        self._ear_smooth = deque(maxlen=self._ear_smooth_n)
        self._cur_thresh = self._ear_closed_thresh   # 当前生效阈值
        # 缓冲：每项 = (ts, face_found, ear, mar, pitch, yaw, roll)
        self._buf = deque()
        self._baseline: Optional[Baseline] = None

    def _load_params(self, cfg) -> None:
        """从配置读入全部可调参数（__init__ 与 reconfigure 共用）。"""
        eye = cfg.get("eye", {})
        mouth = cfg.get("mouth", {})
        head = cfg.get("head", {})

        # 眼部
        self._ear_closed_thresh = float(eye.get("ear_closed_thresh", 0.21))  # 绝对回退阈值
        self._ear_closed_ratio = float(eye.get("ear_closed_ratio", 0.6))
        self._blink_min_dur = float(eye.get("blink_min_duration_sec", 0.06))
        self._perclos_win = float(eye.get("perclos_window_sec", 30))
        self._blink_win = float(eye.get("blink_window_sec", 60))
        # 眨眼率最小归一化窗口：启动时窗口未填满，若直接除以极小的已过时长会让
        # 眨眼率虚高爆表（反馈#3）。用此最小分母兜底，早期宁可略低不高估。
        self._blink_rate_min_win = float(eye.get("blink_rate_min_window_sec", 15))
        # 创新②：微睡眠判定阈（连续闭眼超此秒数记一次微睡眠）
        self._microsleep_dur = float(eye.get("microsleep_dur_sec", 0.5))
        # 个性化闭眼阈：校准后按"睁眼均值 − k×睁眼标准差"设定（见 set_baseline）。
        # 参考 PeerJ 2022《Adjusting EAR...》与 Soukupová&Čech 2016 的思路：固定阈值
        # 因人而异不可靠，应贴合本人的睁眼水平与信号噪声。EAR 先做滑动平滑去抖。
        self._ear_smooth_n = max(1, int(eye.get("ear_smooth_frames", 3)))
        self._ear_k_std = float(eye.get("ear_closed_k_std", 2.0))
        # 嘴部
        self._mar_yawn = float(mouth.get("mar_yawn_thresh", 0.6))
        self._yawn_min_dur = float(mouth.get("yawn_min_duration_sec", 1.5))
        self._yawn_win = float(mouth.get("yawn_window_sec", 60))
        # 头部
        self._nod_win = float(head.get("nod_window_sec", 10))
        self._nod_amp = float(head.get("nod_amplitude_deg", 8))
        # 判为"点头"状态所需的最少点头次数：单次偏离(如哈欠头微动)不算点头（反馈#2）
        self._nod_min_count = int(head.get("nod_min_count", 2))

        self._max_win = max(self._perclos_win, self._blink_win, self._yawn_win, self._nod_win)

    def reconfigure(self, cfg) -> None:
        """运行时重载可调参数（「参数设置」面板调参后调用）。

        保留滑窗缓冲与已注入的基线——调参不清统计数据、不打断监测；
        个性化闭眼阈按新的 k 值基于原基线重新推导。
        """
        self._cfg = cfg or {}
        old_smooth_n = self._ear_smooth_n
        self._load_params(self._cfg)
        if self._ear_smooth_n != old_smooth_n:   # 平滑窗长变化时保留最近样本
            self._ear_smooth = deque(self._ear_smooth, maxlen=self._ear_smooth_n)
        self.set_baseline(self._baseline)

    # ------------------------------ 基线注入 ---------------------------------

    def set_baseline(self, baseline: Optional[Baseline]) -> None:
        """注入个性化基线：据此把闭眼阈值设为"睁眼均值 − k×睁眼标准差"。

        这样阈值同时贴合本人的睁眼水平与信号噪声：睁眼越稳(std小)阈值越贴近
        睁眼、能捕捉更小的闭合(如戴眼镜差值小的情况)；越抖则阈值下移、要求
        更明显的闭合才算数。夹到 [50%, 92%]×睁眼均值：过低会漏检、过高(逼近
        睁眼)会误报。
        """
        self._baseline = baseline
        if baseline is not None and getattr(baseline, "valid", False) and baseline.ear_open_mean > 0:
            mean = baseline.ear_open_mean
            std = max(baseline.ear_open_std, 1e-4)
            raw = mean - self._ear_k_std * std
            self._ear_closed_thresh = min(0.92 * mean, max(0.5 * mean, raw))

    @property
    def ear_closed_thresh(self) -> float:
        """当前生效的闭眼阈值（自适应时为个性化中点阈；供界面显示）。"""
        return self._cur_thresh

    # ------------------------------- 数据流 ----------------------------------

    def push(self, ff) -> None:
        """压入一帧逐帧特征，并按最大窗口长度裁剪缓冲。

        EAR 存入前先做滑动均值平滑（仅对检出人脸的帧），抑制关键点抖动，
        避免睁闭眼差值很小时噪声造成的误判/闪烁。
        """
        # 时间戳回退（视频文件循环播放回绕/seek）→ 清空缓冲重新累计，
        # 否则旧一轮的帧会滞留在滑窗里污染统计
        if self._buf and ff.ts < self._buf[-1][0]:
            self._buf.clear()
            self._ear_smooth.clear()
        if ff.face_found:
            self._ear_smooth.append(ff.ear)
            ear = sum(self._ear_smooth) / len(self._ear_smooth)
        else:
            ear = ff.ear
        self._buf.append((ff.ts, ff.face_found, ear, ff.mar, ff.pitch, ff.yaw, ff.roll))
        t_min = ff.ts - self._max_win
        while self._buf and self._buf[0][0] < t_min:
            self._buf.popleft()

    def _window(self, sec):
        """取最近 sec 秒内的帧列表。"""
        if not self._buf:
            return []
        now = self._buf[-1][0]
        lo = now - sec
        return [f for f in self._buf if f[0] >= lo]

    def _is_closed(self, frame) -> bool:
        """该帧是否判为闭眼（检出人脸且 EAR 低于当前生效阈值）。"""
        return frame[1] and frame[2] < self._cur_thresh

    # ------------------------------- 结果聚合 --------------------------------

    def result(self) -> WindowFeatures:
        """聚合当前缓冲，产出 WindowFeatures。"""
        wf = WindowFeatures()
        if not self._buf:
            return wf

        # 生效阈值：校准后为个性化统计阈，否则为绝对回退阈值
        self._cur_thresh = self._ear_closed_thresh
        wf.perclos = self._compute_perclos()
        wf.blink_count, wf.blink_rate = self._compute_blinks()
        wf.eye_closed_dur = self._compute_longest_closed()
        wf.current_closed_dur = self._compute_current_closed()
        wf.avg_blink_dur, wf.microsleep_count = self._compute_blink_dynamics()
        wf.face_ratio, wf.mean_abs_yaw = self._compute_signal_quality()
        yc, yflag, mar_mean = self._compute_yawn()
        wf.yawn_count = yc
        wf.yawn_flag = yflag
        wf.mar_mean = mar_mean
        wf.nod_count = self._count_nods(self._window(self._nod_win))
        wf.head_state = self._compute_head_state(wf.nod_count)
        wf.hr = None      # M4 由 rPPG 填充
        wf.hrv = None
        return wf

    def _compute_perclos(self) -> float:
        w = self._window(self._perclos_win)
        face = [f for f in w if f[1]]
        if not face:
            return 0.0
        closed = sum(1 for f in face if self._is_closed(f))
        return closed / len(face)

    def _compute_blinks(self):
        """眨眼统计：闭眼连续段按"闭眼时长"去抖后计数。

        去抖用时间戳而非帧数，对帧率自适应：30fps 下 0.06s ≈ 旧的 2 帧去抖
        （单帧 33ms 毛刺仍被滤除）；~8fps 低帧率下单帧闭眼(125ms)也能计数，
        否则正常眨眼（全闭合仅 100~150ms）在低帧率下永远凑不满 2 帧。

        返回:
            (count, rate) —— 窗口内眨眼次数（原始计数，便于人工核对）
            与换算的眨眼率（次/分）。
        """
        w = self._window(self._blink_win)
        if len(w) < 2:
            return 0, 0.0
        blinks = 0
        run_start = None
        for f in w:
            if self._is_closed(f):
                if run_start is None:
                    run_start = f[0]
            else:
                # 含帧间隔语义：闭眼段持续到首个非闭眼帧的时刻
                if run_start is not None and f[0] - run_start >= self._blink_min_dur:
                    blinks += 1
                run_start = None
        # 收尾：窗口末仍闭眼（无后续帧，保守用末帧时刻）
        if run_start is not None and w[-1][0] - run_start >= self._blink_min_dur:
            blinks += 1
        # 归一化到"次/分"。用最小分母避免启动时窗口未填满、分母过小导致虚高。
        elapsed = w[-1][0] - w[0][0]
        denom = max(elapsed, self._blink_rate_min_win)
        if denom <= 0:
            return blinks, 0.0
        return blinks, blinks / denom * 60.0

    def _compute_longest_closed(self) -> float:
        """最长连续闭眼时长(秒)，用时间戳、含帧间隔语义计算。

        闭眼段 = 首个闭眼帧 → 其后首个非闭眼帧；低帧率下单帧闭眼
        即代表一个帧间隔的时长，不再是 0。
        """
        w = self._window(self._perclos_win)
        longest = 0.0
        run_start = None
        for ts, face, ear, mar, p, y, r in w:
            if face and ear < self._cur_thresh:
                if run_start is None:
                    run_start = ts
            else:
                if run_start is not None:
                    longest = max(longest, ts - run_start)
                run_start = None
        if run_start is not None:   # 收尾：窗口末仍闭眼，保守用末帧时刻
            longest = max(longest, w[-1][0] - run_start)
        return longest

    def _compute_current_closed(self) -> float:
        """当前正在进行的连续闭眼时长（秒）；最新帧未闭眼则为 0。

        反映"此刻已经连续闭了多久"，无历史老化、不受统计窗口长度限制（仅受缓冲
        长度限制），比窗口内"最长闭眼"即时得多——供微睡眠直接报警用（反馈#1/#5）。
        """
        if not self._buf:
            return 0.0
        last = self._buf[-1]
        if not (last[1] and last[2] < self._cur_thresh):
            return 0.0
        now = last[0]
        start = now
        for frame in reversed(self._buf):     # 从末尾往前扩展连续闭眼段
            if frame[1] and frame[2] < self._cur_thresh:
                start = frame[0]
            else:
                break
        return now - start

    def _compute_blink_dynamics(self):
        """创新②：平均眨眼时长 + 微睡眠计数（连续闭眼 > microsleep_dur_sec）。

        依据 Oxford SLEEP Advances 2023：平均眨眼时长是微睡眠(>500ms 闭眼)的最佳
        判别指标之一，比单纯眨眼频率更能反映疲劳。返回 (平均眨眼时长秒, 微睡眠次数)。
        """
        w = self._window(self._blink_win)
        durs = []
        micro = 0
        run_start = None
        for f in w:
            if self._is_closed(f):
                if run_start is None:
                    run_start = f[0]
            else:
                if run_start is not None:
                    d = f[0] - run_start
                    if d >= self._blink_min_dur:
                        durs.append(d)
                    if d >= self._microsleep_dur:
                        micro += 1
                run_start = None
        if run_start is not None:      # 收尾：窗口末仍闭眼
            d = w[-1][0] - run_start
            if d >= self._blink_min_dur:
                durs.append(d)
            if d >= self._microsleep_dur:
                micro += 1
        avg = float(sum(durs) / len(durs)) if durs else 0.0
        return avg, micro

    def _compute_signal_quality(self):
        """创新①：信号质量代理——近窗内检出人脸帧占比、平均|偏航角|。

        供质量感知融合动态加权：人脸时有时无(占比低)→整体降权；头转得多
        (|yaw|大)→眼部特征此时不可靠、降权。返回 (人脸占比0..1, 平均|yaw|度)。
        """
        w = self._window(self._perclos_win)
        if not w:
            return 1.0, 0.0
        face = [f for f in w if f[1]]
        face_ratio = len(face) / len(w)
        mean_yaw = float(np.mean([abs(f[5]) for f in face])) if face else 0.0
        return face_ratio, mean_yaw

    def _compute_yawn(self):
        """哈欠：MAR>阈值且持续≥最短哈欠时长记一次；返回(计数, 当前是否哈欠, MAR均值)。

        持续时长为含帧间隔语义（张口段 = 首个张口帧 → 其后首个闭口帧）。
        旧的"首帧→末帧"算法少算一个帧间隔，8fps 下 1.5s 的哈欠只能算出
        1.375s，恰好低于 1.5s 阈值而漏检。
        """
        w = self._window(self._yawn_win)
        face = [f for f in w if f[1]]
        mar_mean = float(np.mean([f[3] for f in face])) if face else 0.0
        count = 0
        flag = False
        run_start = None
        for ts, fface, ear, mar, p, y, r in w:
            if fface and mar > self._mar_yawn:
                if run_start is None:
                    run_start = ts
            else:
                if run_start is not None and ts - run_start >= self._yawn_min_dur:
                    count += 1
                run_start = None
        # 收尾：窗口末仍在张口（保守用末帧时刻），若已持续足够则既计数又置 flag
        if run_start is not None and w[-1][0] - run_start >= self._yawn_min_dur:
            count += 1
            flag = True
        return count, flag, mar_mean

    def _count_nods(self, w) -> int:
        """点头计数：只有"偏离→回归"的完整摆动才算一次点头（反馈#2）。

        旧法只要俯仰角偏离均值超阈值就+1，导致打哈欠时头部一次缓慢前倾被连续
        误判为多次点头。现用状态机：俯仰角先越过 ±nod_amp（进入偏离态），再回到
        ±nod_amp×0.4 以内（回归）才计一次——一次持续前倾（如哈欠）至多计 1 次，
        周期性点头才会累计多次。基准用中位数，抗持续前倾把均值带偏。
        """
        face = [f for f in w if f[1]]
        if len(face) < 5:
            return 0
        pitches = np.asarray([f[4] for f in face], dtype=np.float64)
        base = float(np.median(pitches))
        nods = 0
        deviated = False
        for p in pitches:
            d = abs(p - base)
            if not deviated and d > self._nod_amp:
                deviated = True
            elif deviated and d < self._nod_amp * 0.4:
                nods += 1
                deviated = False
        return nods

    def _compute_head_state(self, nod_count: int) -> str:
        """头部状态：达到最少点头次数才判 nodding；否则按近 1s 平均角分类。"""
        if nod_count >= self._nod_min_count:
            return HEAD_STATE_NODDING
        recent = [f for f in self._window(1.0) if f[1]]
        if not recent:
            return HEAD_STATE_NORMAL
        pitch = float(np.mean([f[4] for f in recent]))
        yaw = float(np.mean([f[5] for f in recent]))
        roll = float(np.mean([f[6] for f in recent]))
        return classify_head_state(pitch, yaw, roll, self._baseline, self._cfg)
