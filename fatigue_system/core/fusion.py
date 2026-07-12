# -*- coding: utf-8 -*-
"""多特征加权融合与防误报状态机（开发规格书 §6.9）。

处理链（每个融合节拍执行一次，节拍见 config fusion.update_interval_sec）：
    WindowFeatures ─► 各子分(0..1) ─► fuse 加权融合 S ─► AlarmFSM(EMA平滑
    + 四级判定 + 连续N窗口重度才报警 + 迟滞清除) ─► FatigueResult

设计要点：
  * 子分归一化锚点全部来自 config fusion.subscore（无魔法数字）；
    每个子分取"多指标 max"而非均值——任一指标显著异常即可拉高该子分，
    避免被正常指标稀释（如持续闭眼时不该被正常眨眼率平均掉）。
  * physio 子分在 rPPG 缺失（M4 之前 / 基线无静息心率）时返回 None，
    fuse 会按剩余权重归一化 —— 即退化为任务书的基础模型。
  * EMA 平滑放在 AlarmFSM 内：对外呈现的评分/等级都是平滑后的值，
    与报警判定保持一致。
"""

from typing import Dict, Optional, Tuple

from fatigue_system.core.types import (
    FatigueResult, LEVEL_NAMES,
    HEAD_STATE_LOWERED, HEAD_STATE_TILTED,
)

# 疲劳等级编号（索引与 LEVEL_NAMES 对应，语义常量非可调参数）
LEVEL_AWAKE, LEVEL_MILD, LEVEL_MODERATE, LEVEL_SEVERE = 0, 1, 2, 3


def _clamp01(x: float) -> float:
    """截断到 [0, 1]。"""
    return max(0.0, min(1.0, float(x)))


def _sub_cfg(cfg) -> Dict:
    """取子分归一化锚点配置段 fusion.subscore。"""
    return (cfg or {}).get("fusion", {}).get("subscore", {})


# --------------------------------- 各子分 -------------------------------------

def eye_subscore(wf, baseline, cfg) -> float:
    """眼部子分 0..1：PERCLOS / 最长闭眼时长 / 眨眼率异常，三者取 max。

    参数:
        wf       —— WindowFeatures（其闭眼判定已含个性化阈值）。
        baseline —— Baseline 或 None（个性化已在滑窗层生效，此处仅按接口保留）。
        cfg      —— 完整配置字典。
    """
    sub = _sub_cfg(cfg)
    full_sec = float(sub.get("eye_closed_full_sec", 2.0))
    perclos_s = _clamp01(wf.perclos / float(sub.get("perclos_full", 0.40)))
    # 闭眼时长子分：取"窗口内最长"与"当前正在进行"的较大者——后者更即时，
    # 让持续闭眼一开始就把眼部子分推高（反馈#1：闭眼对分数影响要明显）。
    closed_s = _clamp01(max(wf.eye_closed_dur, wf.current_closed_dur) / full_sec)
    br_normal = float(sub.get("blink_rate_normal", 20))
    br_full = float(sub.get("blink_rate_full", 40))
    blink_s = 0.0
    if br_full > br_normal:
        blink_s = _clamp01((wf.blink_rate - br_normal) / (br_full - br_normal))
    return max(perclos_s, closed_s, blink_s)


def mouth_subscore(wf, baseline, cfg) -> float:
    """嘴部子分 0..1：哈欠计数归一（进行中的哈欠已计入 yawn_count）。"""
    sub = _sub_cfg(cfg)
    return _clamp01(wf.yawn_count / float(sub.get("yawn_count_full", 3)))


def head_subscore(wf, baseline, cfg) -> float:
    """头部子分 0..1：异常姿态(低头/偏头)固定分 与 点头计数归一 取 max。"""
    sub = _sub_cfg(cfg)
    state_score = {
        HEAD_STATE_LOWERED: float(sub.get("head_lowered_score", 0.7)),
        HEAD_STATE_TILTED: float(sub.get("head_tilted_score", 0.5)),
    }.get(wf.head_state, 0.0)
    nod_s = _clamp01(wf.nod_count / float(sub.get("nod_count_full", 3)))
    return max(state_score, nod_s)


def physio_subscore(wf, baseline, cfg) -> Optional[float]:
    """生理子分 0..1：心率相对本人静息基线的下降比例（疲劳时心率下降）。

    rPPG 缺失（wf.hr 为 None）或基线无静息心率时返回 None——
    fuse 将按剩余权重归一化，退化为任务书基础模型（M4 之前恒为 None）。
    """
    if wf.hr is None or baseline is None or not getattr(baseline, "valid", False):
        return None
    hr_rest = getattr(baseline, "hr_rest", None)
    if not hr_rest or hr_rest <= 0:
        return None
    sub = _sub_cfg(cfg)
    drop_ratio = (hr_rest - wf.hr) / hr_rest
    return _clamp01(drop_ratio / float(sub.get("hr_drop_full_ratio", 0.15)))


# --------------------------------- 融合/分级 ----------------------------------

def fuse(sub_scores: Dict[str, Optional[float]], weights: Dict[str, float]) -> float:
    """加权融合 S = Σ w_i·s_i；子分为 None（缺失）时按剩余权重归一化。

    参数:
        sub_scores —— {'eye','mouth','head','physio'} → 0..1 或 None。
        weights    —— 同键权重（config fusion.weights，Σw=1）。
    返回:
        融合疲劳分 S ∈ [0, 1]；全部缺失时为 0。
    """
    total_w = 0.0
    acc = 0.0
    for key, w in (weights or {}).items():
        s = sub_scores.get(key)
        if s is None:
            continue
        acc += float(w) * _clamp01(s)
        total_w += float(w)
    return acc / total_w if total_w > 0 else 0.0


def to_level(score: float, cfg) -> Tuple[int, str]:
    """融合分 S → (等级 0..3, 中文名)。阈值来自 config fusion.level_thresholds。"""
    th = (cfg or {}).get("fusion", {}).get("level_thresholds", {})
    mild = float(th.get("mild", 0.25))
    moderate = float(th.get("moderate", 0.50))
    severe = float(th.get("severe", 0.70))
    if score >= severe:
        level = LEVEL_SEVERE
    elif score >= moderate:
        level = LEVEL_MODERATE
    elif score >= mild:
        level = LEVEL_MILD
    else:
        level = LEVEL_AWAKE
    return level, LEVEL_NAMES[level]


class AlarmFSM:
    """防误报报警状态机：EMA 平滑 + 连续 N 窗口重度才报警 + 迟滞清除。

    "窗口"指一次融合评估（节拍 = config fusion.update_interval_sec，默认 1s），
    连续窗口按调用次数计——调用方必须按该节拍调用 update()，不能按帧调用
    （帧率会变，见交接文档 §4.12 的教训）。
    """

    def __init__(self, cfg):
        fusion_cfg = (cfg or {}).get("fusion", {})
        self._cfg = cfg
        self._alpha = float(fusion_cfg.get("smoothing_alpha", 0.3))
        self._n_alarm = int(fusion_cfg.get("alarm_consecutive_windows", 3))
        self._n_clear = int(fusion_cfg.get("alarm_clear_windows", 2))
        self.reset()

    def reset(self) -> None:
        """复位（切换视频源/重新开始检测时调用）。"""
        self._ema: Optional[float] = None
        self._level: int = LEVEL_AWAKE
        self._severe_run = 0
        self._calm_run = 0
        self._alarm = False

    def update(self, level: int, score: float) -> bool:
        """喂入一个窗口的（原始等级, 原始融合分），返回当前是否处于报警。

        内部先对 score 做 EMA 平滑、按平滑分重判等级（对外呈现的评分/
        等级即此平滑值）；raw level 参数按规格书接口保留，供未来策略使用。
        """
        if self._ema is None:
            self._ema = float(score)
        else:
            self._ema = self._alpha * float(score) + (1.0 - self._alpha) * self._ema
        self._level, _ = to_level(self._ema, self._cfg)

        if self._level >= LEVEL_SEVERE:
            self._severe_run += 1
            self._calm_run = 0
        else:
            self._calm_run += 1
            self._severe_run = 0

        if not self._alarm and self._severe_run >= self._n_alarm:
            self._alarm = True
        elif self._alarm and self._calm_run >= self._n_clear:
            self._alarm = False
        return self._alarm

    @property
    def smoothed_score(self) -> float:
        """EMA 平滑后的融合分（未喂入任何窗口时为 0）。"""
        return self._ema if self._ema is not None else 0.0

    @property
    def smoothed_level(self) -> int:
        """按平滑分判定的等级 0..3。"""
        return self._level

    @property
    def alarm(self) -> bool:
        """当前是否处于报警状态。"""
        return self._alarm


def evaluate(wf, baseline, cfg, fsm: AlarmFSM) -> FatigueResult:
    """便捷封装：子分 → 融合 → FSM → FatigueResult（GUI/记录直接用）。

    按 fusion.update_interval_sec 节拍调用一次（不要按帧调用，见 AlarmFSM 注释）。
    """
    sub = {
        "eye": eye_subscore(wf, baseline, cfg),
        "mouth": mouth_subscore(wf, baseline, cfg),
        "head": head_subscore(wf, baseline, cfg),
        "physio": physio_subscore(wf, baseline, cfg),
    }
    weights = (cfg or {}).get("fusion", {}).get("weights", {})
    raw_score = fuse(sub, weights)
    raw_level, _ = to_level(raw_score, cfg)
    alarm = fsm.update(raw_level, raw_score)
    level = fsm.smoothed_level
    score = fsm.smoothed_score

    # 微睡眠硬规则：持续闭眼超过阈值 → 立即判重度并报警（反馈#1/#5）。
    # 微睡眠是最危险状态，不等 EMA 平滑与"连续N窗"慢慢累积，直接越过 FSM。
    micro = float((cfg or {}).get("fusion", {}).get("microsleep_sec", 2.0))
    if micro > 0 and getattr(wf, "current_closed_dur", 0.0) >= micro:
        level = LEVEL_SEVERE
        alarm = True
        severe_th = float((cfg or {}).get("fusion", {}).get("level_thresholds", {}).get("severe", 0.70))
        score = max(score, severe_th)   # 分数也顶到重度线，界面显示一致

    return FatigueResult(
        score=score,
        level=level,
        level_name=LEVEL_NAMES[level],
        sub_scores=sub,
        alarm=alarm,
    )
