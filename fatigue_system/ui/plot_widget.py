# -*- coding: utf-8 -*-
"""指标监测区（DESIGN.md §5.4）：左曲线卡片 + 右指标列表卡片。

    MonitorPanel    —— 左：曲线卡（标题 + 当前值大数字 + 平滑曲线）；
                       右：指标列表（点击行切换曲线；常用 8 项 / "更多指标"**换页**
                       看其余项，不撑长卡片）。
    TimeSeriesChart —— 自绘曲线：
      * **平滑**：先把序列重采样到均匀网格，再用 Catmull-Rom 样条绘制。慢指标
        （PERCLOS/心率/融合分等每秒才更新一次）额外做滑动平均——否则每帧重复
        写同一个值会画成"台阶"；EAR/MAR 是逐帧量，不平滑以保留眨眼细节。
      * **切换动画（§7.2 方案B 逐点插值）**：新旧曲线在同一批 x 上逐点插值
        y = 旧 + (新−旧)×ease(t)（归一化坐标），曲线真实"变形"过去，不是简单
        叠加透明度；y 轴刻度与阈值线同步插值/淡入淡出。220ms OutCubic，
        与列表选中态、卡片大数字**同起同止**。

只读展示，不改动检测逻辑。
"""

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult
from fatigue_system.ui import theme
from fatigue_system.ui.anim import animate

_NAN = float("nan")
WINDOW_SEC = 60.0          # 曲线时间窗（秒）
_HIST_MAXLEN = 3600        # 历史缓存上限
_GRID_N = 300              # 重采样网格点数（≈ 每 0.2 秒一个点，够画平滑曲线）


def _num(value) -> float:
    """安全取数：None/无效 → NaN（曲线在此断开）。"""
    try:
        v = float(value)
        return v if math.isfinite(v) else _NAN
    except (TypeError, ValueError):
        return _NAN


_HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}
_HEAD_STATE_COLOR = {
    "normal": theme.GREEN_TEXT, "lowered": theme.ORANGE,
    "tilted": theme.ORANGE, "nodding": theme.RED,
}


class Metric:
    """一个可展示的指标：列表行 + 曲线的全部元数据。

    字段:
        key/name   —— 键与中文名。
        common     —— 是否属于第 1 页（核心指标）。
        chartable  —— 是否可画曲线（列表里**每一行都可点**，见 METRICS 注释）。
        yrange     —— 固定 y 范围；**None = 自适应量程**（见 min_span/bounds）。
        fn         —— 从 (ff, wf, result) 取曲线值。
        fmt        —— 大数字/列表值的格式。
        smooth_sec —— 绘图平滑窗口（秒）。0 = 不平滑（逐帧量，如 EAR/MAR，要保留
                      眨眼尖峰）；每秒才更新一次的量必须平滑，否则每帧重复写同一个
                      值 → 画出来就是台阶。
        min_span   —— 自适应量程的**最小跨度**：值稳定时若让量程紧贴数据，微小噪声
                      会被放大成剧烈波动；给个下限，曲线才"稳得住"。
        bounds     —— 该量的物理上下界（如 PERCLOS ∈ [0,1]），自适应量程不越界。
    """

    def __init__(self, key, name, common, chartable, yrange, fn, fmt,
                 smooth_sec=0.0, min_span=None, bounds=None):
        self.key = key
        self.name = name
        self.common = common
        self.chartable = chartable
        self.yrange = yrange
        self.fn = fn
        self.fmt = fmt
        self.smooth_sec = smooth_sec
        self.min_span = min_span
        self.bounds = bounds


# 绘图平滑窗口：更新节拍是 1 秒，窗口要明显大于它才能把台阶抹成斜坡；
# 计数类（微睡眠/哈欠/点头）是整数、一次跳一整格，需要更长的窗口。
_SLOW = 2.5
_COUNT = 4.0

# 指标顺序与分页（用户拍板 · 方案A）：
#   * 第 1 页「核心指标」= 看**结论与判据**：融合分（最终结论）固定第一，紧跟它的
#     国际刻度 KSS；再按 眼 → 嘴 → 生理 的因果链排。
#   * 第 2 页「更多指标」= 看**原始信号**：EAR/MAR/角度等，调参或排查时才翻。
#   * **每一行都可点、都有曲线**：原来的"头部状态""疲劳等级"没有曲线却夹在能点的
#     行中间（点了没反应，手感很怪），且这两条信息在界面上已各出现过一次——头部
#     状态见视频左下角胶囊"姿态 …"，疲劳等级见右上等级卡的胶囊——按 §1.1「同一
#     信息只出现一次」删除，不在此重复。
METRICS: List[Metric] = [
    # ---- 第 1 页：核心指标 ----
    Metric("score", "融合分", True, True, (0.0, 1.0),
           lambda ff, wf, r: _num(r.score) if r else _NAN, "{:.3f}", _SLOW),
    Metric("kss", "KSS 嗜睡度", True, True, (1.0, 9.0),
           lambda ff, wf, r: _num(r.kss) if r else _NAN, "{:.0f}/9", _SLOW),
    Metric("perclos", "PERCLOS", True, True, None,       # 自适应：正常时只有百分之几，
           lambda ff, wf, r: _num(wf.perclos) if wf else _NAN, "{:.0%}", _SLOW,
           min_span=0.10, bounds=(0.0, 1.0)),            # 固定 0~100% 会一直贴着底
    Metric("closed", "最长闭眼", True, True, None,
           lambda ff, wf, r: _num(wf.eye_closed_dur) if wf else _NAN, "{:.1f} s", _SLOW,
           min_span=1.0, bounds=(0.0, None)),
    Metric("microsleep", "微睡眠", True, True, None,
           lambda ff, wf, r: _num(wf.microsleep_count) if wf else _NAN, "{:.0f}", _COUNT,
           min_span=3.0, bounds=(0.0, None)),
    Metric("blink", "眨眼率", True, True, None,
           lambda ff, wf, r: _num(wf.blink_rate) if wf else _NAN, "{:.0f}/分", _SLOW,
           min_span=10.0, bounds=(0.0, None)),
    Metric("yawn", "哈欠数", True, True, None,
           lambda ff, wf, r: _num(wf.yawn_count) if wf else _NAN, "{:.0f}", _COUNT,
           min_span=3.0, bounds=(0.0, None)),
    Metric("hr", "心率 HR", True, True, None,
           lambda ff, wf, r: _num(wf.hr) if wf and wf.hr is not None else _NAN, "{:.0f}",
           _SLOW, min_span=10.0, bounds=(30.0, 200.0)),
    # ---- 第 2 页：更多指标（原始信号）----
    Metric("ear", "EAR 眼纵横比", False, True, (0.0, 0.45),
           lambda ff, wf, r: _num(ff.ear) if ff and ff.face_found else _NAN, "{:.3f}", 0.0),
    Metric("mar", "MAR 嘴纵横比", False, True, (0.0, 0.6),
           lambda ff, wf, r: _num(ff.mar) if ff and ff.face_found else _NAN, "{:.3f}", 0.0),
    Metric("avg_blink", "平均眨眼时长", False, True, None,
           lambda ff, wf, r: _num(wf.avg_blink_dur) if wf else _NAN, "{:.2f} s", _SLOW,
           min_span=0.4, bounds=(0.0, None)),
    Metric("nod", "点头数", False, True, None,
           lambda ff, wf, r: _num(wf.nod_count) if wf else _NAN, "{:.0f}", _COUNT,
           min_span=3.0, bounds=(0.0, None)),
    Metric("pitch", "俯仰角", False, True, None,
           lambda ff, wf, r: _num(ff.pitch) if ff and ff.face_found else _NAN, "{:+.0f}°",
           _SLOW, min_span=20.0),
    Metric("hrv", "HRV", False, True, None,
           lambda ff, wf, r: _num(wf.hrv) if wf and wf.hrv is not None else _NAN, "{:.0f}",
           _SLOW, min_span=20.0, bounds=(0.0, None)),
    Metric("quality", "信号质量", False, True, None,
           lambda ff, wf, r: _num(wf.face_ratio) if wf else _NAN, "{:.0%}", _SLOW,
           min_span=0.2, bounds=(0.0, 1.0)),
]

_BY_KEY: Dict[str, Metric] = {m.key: m for m in METRICS}


def _row_values(ff, wf, result, head_state) -> Dict[str, Tuple[str, str]]:
    """列表每行的 (显示文本, 颜色)。

    head_state 仍在签名里（主窗口按帧传入），但不再单独成行——头部状态已显示在
    视频左下角胶囊（§1.1 同一信息只出现一次）。
    """
    out: Dict[str, Tuple[str, str]] = {}
    dim = theme.TEXT_MUTE
    normal = theme.TEXT_DIM

    def put(key, val, color=normal):
        m = _BY_KEY[key]
        out[key] = (m.fmt.format(val), color) if val is not None else ("—", dim)

    face = bool(ff and ff.face_found)
    put("ear", ff.ear if face else None)
    put("mar", ff.mar if face else None)
    put("pitch", ff.pitch if face else None)
    if wf is not None:
        put("perclos", wf.perclos)
        put("blink", wf.blink_rate)
        put("closed", wf.eye_closed_dur)
        put("avg_blink", wf.avg_blink_dur)
        put("microsleep", wf.microsleep_count,
            theme.RED if wf.microsleep_count > 0 else normal)
        put("yawn", wf.yawn_count)
        put("nod", wf.nod_count)
        put("hr", wf.hr)
        put("hrv", wf.hrv)
        q = wf.face_ratio
        put("quality", q,
            theme.GREEN_TEXT if q >= 0.9 else (theme.ORANGE if q >= 0.5 else theme.RED))
    if result is not None:
        color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        put("score", result.score)
        put("kss", result.kss, color)     # KSS 用等级色，一眼看出严重程度
    return out


# ------------------------------- 曲线控件 ------------------------------------

class TimeSeriesChart(QWidget):
    """自绘曲线：重采样 + 平滑 + 逐点插值变形（§7.2 方案B）。"""

    # 绘图区边距：左侧留给 y 轴刻度、底部留给时间轴、右侧留给阈值标注
    _PAD_L, _PAD_T, _PAD_B = 54, 10, 26
    _PAD_R_TH, _PAD_R = 58, 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        # 归一化网格（0..1，NaN=无数据），以及对应的 y 轴范围与阈值线
        self._grid: List[float] = [_NAN] * _GRID_N
        self._yrange: Tuple[float, float] = (0.0, 1.0)
        self._thresholds: Optional[List[Tuple[float, str]]] = None
        self._x_frac = 0.0     # 曲线整体左移的子格偏移（见 _resample 的"固定时间格"）
        # y 量程的迟滞状态：_range_key 记录当前是哪个指标的量程
        self._range_key: Optional[str] = None
        self._range: Optional[Tuple[float, float]] = None       # 当前生效量程
        self._range_from: Optional[Tuple[float, float]] = None  # 量程切换动画起点
        self._range_p = 1.0
        # 变形动画的起止快照
        self._from_grid: Optional[List[float]] = None
        self._from_yrange: Optional[Tuple[float, float]] = None
        self._from_th: Optional[List[Tuple[float, str]]] = None
        self._p = 1.0        # 变形进度 0..1（1=已完成）

    # ------------------------------ 数据入口 ---------------------------------

    def set_series(self, points, metric: Metric,
                   thresholds=None, transition: bool = False) -> None:
        """更新曲线。transition=True（用户切换指标）时做 220ms 逐点插值变形。"""
        grid, yrange = self._prepare(points, metric, reset_range=transition)
        if transition:
            # 起点＝当前**正在显示**的形状（可能处于上一次变形中途），保证连续
            self._from_grid = self._blended_grid()
            self._from_yrange = self._blended_yrange()
            self._from_th = self._thresholds
            self._p = 0.0
            animate(self, "_anim_morph", 0.0, 1.0, theme.ANIM_BASE, self._on_morph)
        self._grid, self._yrange, self._thresholds = grid, yrange, thresholds
        self.update()

    # ------------------------------ y 量程迟滞 -------------------------------

    def _resolve_range(self, metric: Metric, lo: float, hi: float,
                       reset: bool) -> Tuple[float, float]:
        """决定本帧生效的 y 量程——**带迟滞**，这是曲线"不抖"的关键。

        每帧按数据重算量程会让曲线一直抖：窗口边缘的数据点一进一出，量程上界就在
        两个刻度之间来回跳（实测心率量程每帧波动 5bpm、曲线中点跳 10 像素）。
        因此：
          * 固定量程的指标直接用固定值；
          * 自适应量程只在**必要时**才改：数据顶到边界（需要扩），或数据只占了
            量程的一半以下（可以收）；其余情况维持现状，量程纹丝不动；
          * 真要改时用 300ms 缓动过渡（_range_p），不是瞬间跳。
        """
        if metric.yrange is not None:
            self._range_key = metric.key
            self._range = metric.yrange
            return metric.yrange

        target = self._nice_range(lo, hi, metric.min_span, metric.bounds)
        if reset or self._range is None or self._range_key != metric.key:
            self._range_key = metric.key
            self._range = target
            self._range_from = None
            self._range_p = 1.0
            return target

        cur = self._range
        span_cur = cur[1] - cur[0]
        need_expand = lo < cur[0] or hi > cur[1]                  # 数据超出当前量程
        need_shrink = (hi - lo) < 0.5 * span_cur and target != cur  # 量程明显过大
        if need_expand or need_shrink:
            self._range_from = self._displayed_range()
            self._range = target
            self._range_p = 0.0
            animate(self, "_anim_range", 0.0, 1.0, theme.ANIM_SLOW, self._on_range)
        return self._displayed_range()

    def _displayed_range(self) -> Tuple[float, float]:
        """量程切换过程中的插值结果（缓动，不跳变）。"""
        if self._range is None:
            return (0.0, 1.0)
        if self._range_from is None or self._range_p >= 1.0:
            return self._range
        (a0, a1), (b0, b1) = self._range_from, self._range
        p = self._range_p
        return (a0 + (b0 - a0) * p, a1 + (b1 - a1) * p)

    def _on_range(self, v) -> None:
        self._range_p = float(v)
        if self._range_p >= 1.0:
            self._range_from = None
        self.update()

    def _on_morph(self, v) -> None:
        self._p = float(v)
        if self._p >= 1.0:
            self._from_grid = None
            self._from_yrange = None
            self._from_th = None
        self.update()

    def _prepare(self, points, metric: Metric, reset_range: bool = False):
        """(ts,value) 序列 → 均匀网格上的归一化 y（0..1）+ y 轴范围。

        三步：① 按时间重采样到 _GRID_N 个等距点（线性插值）；② 慢指标做两遍滑动
        平均（每秒才更新一次的量在每帧被重复写入，原样画出来就是台阶）；③ 归一化。

        **网格锚在固定时间格上**（关键）：若把网格锚在"最新帧的时间戳"上，它每帧
        前进 0.05s，同一个屏幕位置每帧代表的时间都不同 → 曲线不是平移，而是原地
        重画，看起来一直在颤（实测每帧抖 1.4 像素）。改为把网格对齐到 step 的整数倍
        时间格：格点上的值一旦算出就不再变，曲线成为**刚体**；再把整条曲线按不足
        一格的余量（_x_frac）整体左移，得到连续顺滑的滚动。
        """
        pts = [(t, v) for t, v in points if math.isfinite(v)]
        if len(pts) < 2:
            self._x_frac = 0.0
            return [_NAN] * _GRID_N, (0.0, 1.0)

        step = WINDOW_SEC / (_GRID_N - 1)
        t_now = points[-1][0]
        t_end = math.floor(t_now / step) * step      # 固定时间格（相位不漂）
        self._x_frac = (t_now - t_end) / step        # 不足一格的余量 → 绘制时整体左移
        t_start = t_end - WINDOW_SEC

        # ① 重采样（pts 按时间递增，用游标线性插值）
        raw: List[float] = []
        j = 0
        for i in range(_GRID_N):
            t = t_start + i * step
            if t < pts[0][0] or t > pts[-1][0]:
                raw.append(_NAN)
                continue
            while j + 1 < len(pts) and pts[j + 1][0] < t:
                j += 1
            t0, v0 = pts[j]
            if j + 1 < len(pts):
                t1, v1 = pts[j + 1]
                r = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                raw.append(v0 + (v1 - v0) * r)
            else:
                raw.append(v0)

        # ② 慢指标平滑：**两遍**滑动平均（= 三角窗低通）。
        #    单遍平均把台阶变成折线，拐角依然生硬；两遍之后拐角变成 S 形，
        #    才是真正"看起来连续"的曲线（计数类窗口更长，见 _COUNT）。
        if metric.smooth_sec > 0:
            k = max(1, int(round(metric.smooth_sec / step)))
            raw = self._moving_average(self._moving_average(raw, k), k)

        # ③ 归一化（量程带迟滞，见 _resolve_range——这是曲线"不抖"的关键）
        finite = [v for v in raw if math.isfinite(v)]
        if not finite:
            return [_NAN] * _GRID_N, (0.0, 1.0)
        y0, y1 = self._resolve_range(metric, min(finite), max(finite), reset_range)
        span = (y1 - y0) or 1.0
        grid = [(_NAN if not math.isfinite(v) else max(0.0, min(1.0, (v - y0) / span)))
                for v in raw]
        return grid, (y0, y1)

    @staticmethod
    def _nice_range(lo: float, hi: float, min_span: Optional[float],
                    bounds: Optional[Tuple[Optional[float], Optional[float]]]):
        """自适应 y 量程：贴合数据但不"贴地"，且稳定不抖。

        三件事：
          * **最小跨度**：值很稳时若让量程紧贴数据，微小噪声会被放大成剧烈波动；
            用 min_span 兜底（如心率至少 10bpm、PERCLOS 至少 15 个百分点）。
          * **上下留白**：数据不贴边框，留 12% 呼吸空间。
          * **量化到整齐刻度**（1/2/5×10^k）：否则量程随每帧数据微调，曲线会持续
            轻微伸缩、看着发抖。刻度对齐后量程只在必要时整格跳变。
        并按 bounds 裁到物理范围内（如 PERCLOS 不会出现负值/超过 100%）。
        """
        span = max(hi - lo, min_span or 0.0, 1e-6)
        center = (lo + hi) / 2.0
        lo2, hi2 = center - span / 2.0, center + span / 2.0
        pad = span * 0.12
        lo2, hi2 = lo2 - pad, hi2 + pad

        # 量化：步长取 1/2/5 × 10^k（按四等分取，比二等分更贴合数据、不浪费高度），
        # 让上下界落在整齐刻度上
        raw_step = (hi2 - lo2) / 4.0
        exp = math.floor(math.log10(raw_step)) if raw_step > 0 else 0
        base = raw_step / (10 ** exp)
        step = (1 if base <= 1 else 2 if base <= 2 else 5 if base <= 5 else 10) * (10 ** exp)
        lo3 = math.floor(lo2 / step) * step
        hi3 = math.ceil(hi2 / step) * step

        if bounds:
            b_lo, b_hi = bounds
            if b_lo is not None:
                lo3 = max(lo3, b_lo)
            if b_hi is not None:
                hi3 = min(hi3, b_hi)
        if hi3 - lo3 < 1e-9:
            hi3 = lo3 + (min_span or 1.0)
        return lo3, hi3

    @staticmethod
    def _moving_average(values: List[float], k: int) -> List[float]:
        """居中滑动平均（NaN 跳过；窗口内全是 NaN 则保持 NaN）。"""
        if k <= 1:
            return values
        half = k // 2
        out: List[float] = []
        n = len(values)
        for i in range(n):
            lo, hi = max(0, i - half), min(n, i + half + 1)
            win = [v for v in values[lo:hi] if math.isfinite(v)]
            out.append(sum(win) / len(win) if win else _NAN)
        return out

    # ------------------------------ 变形混合 ---------------------------------

    def _blended_grid(self) -> List[float]:
        """当前应显示的归一化网格（变形中＝旧新逐点插值，§7.2 方案B 的核心）。"""
        if self._from_grid is None or self._p >= 1.0:
            return list(self._grid)
        p = self._p
        out = []
        for a, b in zip(self._from_grid, self._grid):
            if math.isfinite(a) and math.isfinite(b):
                out.append(a + (b - a) * p)      # 逐点数值插值 → 曲线真实"变形"
            elif math.isfinite(b):
                out.append(b)                     # 旧曲线此处无数据 → 直接用新值
            elif math.isfinite(a):
                out.append(a)
            else:
                out.append(_NAN)
        return out

    def _blended_yrange(self) -> Tuple[float, float]:
        """y 轴范围同步插值——刻度数字跟着曲线一起"滚"过去，不会突跳。"""
        if self._from_yrange is None or self._p >= 1.0:
            return self._yrange
        p = self._p
        (a0, a1), (b0, b1) = self._from_yrange, self._yrange
        return (a0 + (b0 - a0) * p, a1 + (b1 - a1) * p)

    # ------------------------------- 绘制 ------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 变形期间：新旧任一有阈值线就先把右边距留出来，避免绘图区宽度跳变
        has_th = bool(self._thresholds) or bool(self._from_th and self._p < 1.0)
        pad_r = self._PAD_R_TH if has_th else self._PAD_R
        left, top = self._PAD_L, self._PAD_T
        pw, ph = w - left - pad_r, h - top - self._PAD_B
        if pw <= 10 or ph <= 10:
            p.end()
            return

        font = p.font()
        font.setPointSize(9)
        p.setFont(font)

        grid = self._blended_grid()
        finite_idx = [i for i, v in enumerate(grid) if math.isfinite(v)]
        if len(finite_idx) < 2:
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRect(left, top, pw, ph), Qt.AlignCenter, "等待数据…")
            p.end()
            return

        y0, y1 = self._blended_yrange()

        def sy(norm):       # 归一化值 → 屏幕 y
            return top + ph * (1.0 - norm)

        # ---- 网格线与 y 轴刻度（画在左侧留白区，右对齐，不会被裁）----
        grid_pen = QPen(QColor(theme.SEPARATOR))
        for i in range(3):
            norm = 1.0 - i / 2.0
            gy = sy(norm)
            p.setPen(grid_pen)
            p.drawLine(int(left), int(gy), int(left + pw), int(gy))
            p.setPen(QColor(theme.TEXT_MUTE))
            p.drawText(QRect(0, int(gy) - 9, left - 8, 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       self._fmt_tick(y0 + (y1 - y0) * norm))

        # ---- 阈值虚线（仅融合分；变形期随进度淡入/淡出）----
        th_alpha = self._p if self._thresholds else (1.0 - self._p)
        th_list = self._thresholds or self._from_th
        if th_list and th_alpha > 0.01:
            span = (y1 - y0) or 1.0
            for tv, label in th_list:
                norm = (tv - y0) / span
                if not (0.0 <= norm <= 1.0):
                    continue
                gy = sy(norm)
                pen = QPen(self._fade(theme.BORDER, th_alpha))
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(int(left), int(gy), int(left + pw), int(gy))
                p.setPen(self._fade(theme.TEXT_MUTE, th_alpha))
                p.drawText(QRect(int(left + pw + 6), int(gy) - 9, pad_r - 8, 18),
                           Qt.AlignLeft | Qt.AlignVCenter, label)

        # ---- 曲线（Catmull-Rom 平滑）+ 下方填充 ----
        # 网格点的值锚在固定时间格上（不随帧漂移），整条曲线按不足一格的余量左移
        # → 视觉上是**刚体在匀速滚动**，而不是每帧原地重画产生的颤动。
        step_x = pw / (_GRID_N - 1)
        dx = -self._x_frac * step_x if self._p >= 1.0 else 0.0   # 变形动画期间不平移
        segments: List[List[QPointF]] = []
        cur: List[QPointF] = []
        for i, v in enumerate(grid):
            if math.isfinite(v):
                cur.append(QPointF(left + i * step_x + dx, sy(v)))
            elif cur:
                segments.append(cur)
                cur = []
        if cur:
            segments.append(cur)
        p.setClipRect(QRect(left, top, pw, ph))     # 左移后不越界到 y 轴刻度区

        for seg in segments:
            if len(seg) < 2:
                continue
            path = self._smooth_path(seg)
            area = QPainterPath(path)
            area.lineTo(seg[-1].x(), top + ph)
            area.lineTo(seg[0].x(), top + ph)
            area.closeSubpath()
            fill = QColor(theme.ACCENT)
            fill.setAlphaF(theme.CHART_FILL_ALPHA)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(area)
            pen = QPen(QColor(theme.ACCENT))
            pen.setWidth(2)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setClipping(False)                        # 恢复：坐标轴文字要画在绘图区外

        # ---- x 轴（只标两端，§5.4）----
        p.setPen(QColor(theme.TEXT_MUTE))
        p.drawText(QRect(left, int(top + ph + 6), 90, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, "-{:.0f} s".format(WINDOW_SEC))
        p.drawText(QRect(int(left + pw - 90), int(top + ph + 6), 90, 16),
                   Qt.AlignRight | Qt.AlignVCenter, "现在")
        p.end()

    @staticmethod
    def _fade(color: str, alpha: float) -> QColor:
        c = QColor(color)
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c

    @staticmethod
    def _fmt_tick(v: float) -> str:
        """y 轴刻度格式：大数取整、小数留两位（避免 90.73 这类挤不下）。"""
        av = abs(v)
        if av >= 100:
            return "{:.0f}".format(v)
        if av >= 10:
            return "{:.1f}".format(v)
        return "{:.2f}".format(v)

    @staticmethod
    def _smooth_path(pts: List[QPointF]) -> QPainterPath:
        """Catmull-Rom 样条 → 三次贝塞尔，画出平滑曲线（不再是折线/台阶）。"""
        path = QPainterPath(pts[0])
        n = len(pts)
        for i in range(n - 1):
            p0 = pts[i - 1] if i > 0 else pts[i]
            p1, p2 = pts[i], pts[i + 1]
            p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0,
                         p1.y() + (p2.y() - p0.y()) / 6.0)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0,
                         p2.y() - (p3.y() - p1.y()) / 6.0)
            path.cubicTo(c1, c2, p2)
        return path


# ------------------------------- 列表控件 ------------------------------------

class MetricRow(QWidget):
    """指标行：名称 + 当前值；悬停/按下/选中态过渡（§7.1/§7.2）。

    **底色只做"不透明度"渐变，绝不做"到透明色"的颜色插值。**
    Qt 的 QColor 逐通道插值里，"透明"是 rgba(0,0,0,0) ——**透明的黑**；
    从它插值到浅灰会中途经过 rgb(122,122,123)@50%，视觉上就是"深灰闪一下"
    （用户实测反馈）。苹果的悬停填充也是恒定颜色 + 透明度淡入淡出，正是为了
    避免这种跨色插值产生的脏中间色。因此这里：
      * 填充色（悬停灰 / 按下深灰 / 选中蓝）只在**两个不透明色之间**插值——安全；
      * 出现/消失一律走 alpha 0↔1。
    """

    clicked = pyqtSignal(str)

    def __init__(self, metric: Metric, parent=None):
        super().__init__(parent)
        self.key = metric.key
        self._selectable = metric.chartable
        self._selected = False
        self._hovered = False
        self._bg_color = QColor(theme.SURFACE_2)   # 当前填充色（始终不透明）
        self._bg_alpha = 0.0                       # 当前填充不透明度 0..1
        self.setFixedHeight(44)
        if self._selectable:
            self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._name = QLabel(metric.name, self)
        lay.addWidget(self._name)
        lay.addStretch(1)
        self._value = QLabel("—", self)
        lay.addWidget(self._value)
        self._set_name_color(QColor(theme.TEXT))
        self._set_value_color(QColor(theme.TEXT_DIM))

    # ------------------------------ 文字颜色 ---------------------------------

    def _set_name_color(self, c: QColor) -> None:
        self._name_color = QColor(c)
        self._name.setStyleSheet(
            "color:{}; font-size:14px; background:transparent;".format(c.name()))

    def _set_value_color(self, c: QColor) -> None:
        self._value_color = QColor(c)
        self._value.setStyleSheet(
            "color:{}; font-size:14px; font-family:{}; background:transparent;".format(
                c.name(), theme.MONO))

    # ------------------------------ 底色驱动 ---------------------------------

    def _set_alpha(self, v) -> None:
        self._bg_alpha = float(v)
        self.update()

    def _set_color(self, c) -> None:
        self._bg_color = QColor(c)
        self.update()

    def _apply_bg(self, color: Optional[QColor], ms: int) -> None:
        """把底色过渡到目标：color=None 表示淡出（保持当前色，只降 alpha）。"""
        if color is None:
            animate(self, "_a_alpha", self._bg_alpha, 0.0, ms, self._set_alpha)
            return
        if self._bg_alpha <= 0.01:
            self._set_color(color)      # 不可见时直接换色，不会看到跳变
        else:
            # 两个不透明色之间插值（灰→蓝），不经过黑色，安全
            animate(self, "_a_color", QColor(self._bg_color), color, ms, self._set_color)
        animate(self, "_a_alpha", self._bg_alpha, 1.0, ms, self._set_alpha)

    def _refresh_bg(self, ms: int) -> None:
        """按当前状态（选中 > 悬停 > 无）决定底色；按下态由 mousePressEvent 直给。"""
        if self._selected:
            self._apply_bg(QColor(theme.SELECT_BG), ms)
        elif self._hovered and self._selectable:
            self._apply_bg(QColor(theme.SURFACE_2), ms)
        else:
            self._apply_bg(None, ms)

    # ------------------------------ 状态切换 ---------------------------------

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        name_to = QColor(theme.SELECT_FG if selected else theme.TEXT)
        val_to = QColor(theme.SELECT_FG if selected else theme.TEXT_DIM)
        # 与曲线变形、大数字滚动同一时长，三者同起同止（§7.2）
        self._refresh_bg(theme.ANIM_BASE)
        animate(self, "_a_nc", QColor(self._name_color), name_to, theme.ANIM_BASE,
                self._set_name_color)
        animate(self, "_a_vc", QColor(self._value_color), val_to, theme.ANIM_BASE,
                self._set_value_color)

    def set_value(self, text: str, color: str) -> None:
        if not self._selected:
            c = QColor(color)
            if c != self._value_color:
                self._set_value_color(c)
        self._value.setText(text)

    # ------------------------------ 交互事件 ---------------------------------

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._refresh_bg(theme.ANIM_FAST)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._refresh_bg(theme.ANIM_FAST)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._selectable and event.button() == Qt.LeftButton:
            # 按下：更深一档的灰，立即反馈（苹果列表行的按压态，不做缩放）
            if not self._selected:
                self._apply_bg(QColor(theme.TRACK), 80)
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._refresh_bg(theme.ANIM_FAST)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        if self._bg_alpha > 0.004:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            c = QColor(self._bg_color)
            c.setAlphaF(max(0.0, min(1.0, self._bg_alpha)))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(self.rect(), theme.RADIUS_CTRL, theme.RADIUS_CTRL)
            p.end()
        super().paintEvent(event)


class _PagerRow(QWidget):
    """翻页行："更多指标 ⌄" ⇄ "返回常用指标 ⌃"（换页，不撑长列表）。"""

    toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on_page2 = False
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        self._label = QLabel("更多指标", self)
        self._label.setStyleSheet(
            "color:{}; font-size:14px; background:transparent;".format(theme.TEXT_DIM))
        lay.addWidget(self._label)
        lay.addStretch(1)

    def set_page2(self, on: bool) -> None:
        self._on_page2 = on
        self._label.setText("返回常用指标" if on else "更多指标")
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.toggled.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(theme.TEXT_DIM))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx, cy = self.width() - 22, self.height() / 2.0
        if self._on_page2:      # 上箭头（返回）
            p.drawLine(int(cx - 4), int(cy + 2), int(cx), int(cy - 2))
            p.drawLine(int(cx), int(cy - 2), int(cx + 4), int(cy + 2))
        else:                   # 下箭头（更多）
            p.drawLine(int(cx - 4), int(cy - 2), int(cx), int(cy + 2))
            p.drawLine(int(cx), int(cy + 2), int(cx + 4), int(cy - 2))
        p.end()


class _Separator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet("background-color:{}; border:none;".format(theme.SEPARATOR))


class MonitorPanel(QWidget):
    """指标监测区：左曲线卡（标题+大数字） + 右指标列表卡（两页）。"""

    def __init__(self, cfg: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self._cfg = cfg or {}
        self._selected = "score"
        self._hist: Dict[str, deque] = {
            m.key: deque(maxlen=_HIST_MAXLEN) for m in METRICS if m.chartable}
        self._big_value = 0.0          # 卡片大数字当前显示值（切换时插值滚动）

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.GAP)

        # ---- 左：曲线卡 ----
        chart_card = QFrame(self)
        chart_card.setObjectName("card")
        cl = QVBoxLayout(chart_card)
        cl.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, 10)
        cl.setSpacing(2)
        head = QHBoxLayout()
        self._chart_title = QLabel("", chart_card)
        self._chart_title.setObjectName("sectionTitle")
        head.addWidget(self._chart_title)
        head.addStretch(1)
        self._chart_value = QLabel("—", chart_card)     # 当前值大数字（§7.2 滚动）
        self._chart_value.setStyleSheet(
            "font-size:24px; font-weight:500; font-family:{}; color:{}; "
            "background:transparent;".format(theme.MONO, theme.ACCENT))
        head.addWidget(self._chart_value)
        cl.addLayout(head)
        self._chart = TimeSeriesChart(chart_card)
        cl.addWidget(self._chart, stretch=1)
        root.addWidget(chart_card, stretch=3)

        # ---- 右：指标列表卡（两页）----
        list_card = QFrame(self)
        list_card.setObjectName("card")
        list_card.setMinimumWidth(400)
        list_card.setMaximumWidth(480)
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(8, 8, 8, 8)
        ll.setSpacing(0)
        self._rows: Dict[str, MetricRow] = {}
        self._page_widgets: Dict[int, List[QWidget]] = {1: [], 2: []}
        for page, metrics in ((1, [m for m in METRICS if m.common]),
                              (2, [m for m in METRICS if not m.common])):
            for i, m in enumerate(metrics):
                if i > 0:
                    sep = _Separator(list_card)
                    ll.addWidget(sep)
                    self._page_widgets[page].append(sep)
                row = MetricRow(m, list_card)
                row.clicked.connect(self._select)
                self._rows[m.key] = row
                ll.addWidget(row)
                self._page_widgets[page].append(row)
        self._pager = _PagerRow(list_card)
        self._pager.toggled.connect(self._toggle_page)
        ll.addWidget(self._pager)
        ll.addStretch(1)
        root.addWidget(list_card, stretch=1)

        self._show_page(1)
        self._rows[self._selected].set_selected(True)
        self._refresh_chart()

    # ------------------------------- 对外 ------------------------------------

    def append(self, ff: FrameFeatures, wf: WindowFeatures,
               result: Optional[FatigueResult], head_state: str) -> None:
        ts = ff.ts if ff is not None else 0.0
        for m in METRICS:
            if m.chartable:
                self._hist[m.key].append((ts, m.fn(ff, wf, result)))
        vals = _row_values(ff, wf, result, head_state)
        for key, row in self._rows.items():
            text, color = vals.get(key, ("—", theme.TEXT_MUTE))
            row.set_value(text, color)
        self._refresh_chart()

    def reset(self) -> None:
        for dq in self._hist.values():
            dq.clear()
        for row in self._rows.values():
            row.set_value("—", theme.TEXT_MUTE)
        self._chart_value.setText("—")
        self._refresh_chart()

    # ------------------------------- 内部 ------------------------------------

    def _show_page(self, page: int) -> None:
        for pg, widgets in self._page_widgets.items():
            for wgt in widgets:
                wgt.setVisible(pg == page)
        self._page = page
        self._pager.set_page2(page == 2)

    def _toggle_page(self) -> None:
        self._show_page(2 if self._page == 1 else 1)

    def _select(self, key: str) -> None:
        if key == self._selected:
            return
        old_val = self._latest(self._selected)
        self._rows[self._selected].set_selected(False)
        self._selected = key
        self._rows[key].set_selected(True)
        self._refresh_chart(transition=True)
        # 大数字滚动（§7.2.3）：与曲线变形、选中态同起同止
        new_val = self._latest(key)
        if old_val is not None and new_val is not None:
            animate(self, "_a_big", float(old_val), float(new_val), theme.ANIM_BASE,
                    self._on_big_frame)

    def _on_big_frame(self, v) -> None:
        self._big_value = float(v)
        self._chart_value.setText(_BY_KEY[self._selected].fmt.format(self._big_value))

    def _latest(self, key: str) -> Optional[float]:
        for _t, v in reversed(self._hist.get(key, ())):
            if math.isfinite(v):
                return v
        return None

    def _thresholds(self) -> Optional[List[Tuple[float, str]]]:
        if self._selected != "score":
            return None
        th = self._cfg.get("fusion", {}).get("level_thresholds", {})
        return [(float(th.get("severe", 0.70)), "重 {:.2f}".format(float(th.get("severe", 0.70)))),
                (float(th.get("moderate", 0.50)), "中 {:.2f}".format(float(th.get("moderate", 0.50)))),
                (float(th.get("mild", 0.25)), "轻 {:.2f}".format(float(th.get("mild", 0.25))))]

    def _refresh_chart(self, transition: bool = False) -> None:
        m = _BY_KEY[self._selected]
        self._chart_title.setText("{} · 近 {:.0f} 秒".format(m.name, WINDOW_SEC))
        self._chart.set_series(self._hist[m.key], m, self._thresholds(),
                               transition=transition)
        # 数据流刷新时直接显示当前值（不加动画，§7.0 纪律3）；切换时由滚动动画接管
        if not transition:
            v = self._latest(m.key)
            self._chart_value.setText(m.fmt.format(v) if v is not None else "—")
