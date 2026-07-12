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
    # 创新②：眨眼动力学——微睡眠次数、平均眨眼时长（疲劳时眨眼变长/出现微睡眠）
    micro_s = _clamp01(getattr(wf, "microsleep_count", 0) / float(sub.get("microsleep_count_full", 2)))
    bd_normal = float(sub.get("blink_dur_normal_sec", 0.15))
    bd_full = float(sub.get("blink_dur_full_sec", 0.5))
    dur_s = 0.0
    if bd_full > bd_normal:
        dur_s = _clamp01((getattr(wf, "avg_blink_dur", 0.0) - bd_normal) / (bd_full - bd_normal))
    return max(perclos_s, closed_s, blink_s, micro_s, dur_s)


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

def fuse(sub_scores: Dict[str, Optional[float]], weights: Dict[str, float],
         reliabilities: Dict[str, float] = None) -> float:
    """加权融合 S = Σ (w_i·r_i)·s_i / Σ(w_i·r_i)（创新①：质量感知自适应融合）。

    在固定权重 w_i 基础上乘以各子分的**实时可靠度** r_i∈[0,1]（信号质量）再归一化：
    某模态此刻不可靠（如头转开导致 EAR 不准、人脸时有时无）就自动降权，克服固定
    权重在复杂环境下不鲁棒的问题（参考 Frontiers 2026 confidence-driven、TMU-Net
    uncertainty-weighted fusion）。reliabilities=None 时退化为原始固定权重融合。
    子分为 None（缺失）时按剩余权重归一化。

    参数:
        sub_scores    —— {'eye','mouth','head','physio'} → 0..1 或 None。
        weights       —— 同键权重（config fusion.weights，Σw=1）。
        reliabilities —— 同键可靠度 0..1；None 则全部按 1（等价原融合）。
    返回:
        融合疲劳分 S ∈ [0, 1]；全部缺失/权重为 0 时为 0。
    """
    total_w = 0.0
    acc = 0.0
    for key, w in (weights or {}).items():
        s = sub_scores.get(key)
        if s is None:
            continue
        rel = 1.0 if reliabilities is None else _clamp01(reliabilities.get(key, 1.0))
        eff = float(w) * rel
        acc += eff * _clamp01(s)
        total_w += eff
    return acc / total_w if total_w > 0 else 0.0


def reliabilities(wf, cfg) -> Dict[str, float]:
    """创新①：算各子分的实时可靠度 0..1，供质量感知融合动态加权。

    - 人脸检出占比低 → 面部三模态(眼/嘴/头)整体降权；
    - 头转越大(|yaw|越大) → 眼部 EAR 越受投影影响、额外降权（3D EAR 已缓解，
      故惩罚封顶，不至于清零）；
    - 生理靠 HR 是否可用（None 已在 fuse 里被排除），此处给满可靠度。
    """
    sub = _sub_cfg(cfg)
    face_ratio = _clamp01(getattr(wf, "face_ratio", 1.0))
    yaw_full = float(sub.get("yaw_reliability_deg", 45.0))
    yaw_cap = float(sub.get("yaw_penalty_cap", 0.6))
    yaw_pen = min(yaw_cap, abs(getattr(wf, "mean_abs_yaw", 0.0)) / yaw_full) if yaw_full > 0 else 0.0
    return {
        "eye": face_ratio * (1.0 - yaw_pen),
        "mouth": face_ratio,
        "head": face_ratio,
        "physio": 1.0,
    }


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
        # 创新③：置信度自适应决策窗口——多模态一致高疲劳时可少等几窗即报警
        self._adaptive_reduction = int(fusion_cfg.get("adaptive_alarm_reduction", 2))
        self._agree_conf = int(fusion_cfg.get("agreement_for_confident", 2))
        self.reset()

    def reset(self) -> None:
        """复位（切换视频源/重新开始检测时调用）。"""
        self._ema: Optional[float] = None
        self._level: int = LEVEL_AWAKE
        self._severe_run = 0
        self._calm_run = 0
        self._alarm = False

    def update(self, level: int, score: float, agreement: int = 0) -> bool:
        """喂入一个窗口的（原始等级, 原始融合分, 高疲劳子分一致数），返回是否报警。

        内部先对 score 做 EMA 平滑、按平滑分重判等级（对外呈现的评分/等级即此
        平滑值）。**创新③**：当多个子分一致指向高疲劳（agreement≥阈值）时，说明
        证据充分、置信度高，缩短所需"连续重度窗口数"以更快报警；证据单薄时维持
        原窗口数谨慎判定——在灵敏与防误报间自适应（参考 Frontiers 2026）。
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

        # 置信度自适应：证据一致时减少所需连续窗口数（至少 1）
        n_alarm = self._n_alarm
        if agreement >= self._agree_conf:
            n_alarm = max(1, self._n_alarm - self._adaptive_reduction)

        if not self._alarm and self._severe_run >= n_alarm:
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
    rels = reliabilities(wf, cfg)                        # 创新①：质量感知加权
    raw_score = fuse(sub, weights, rels)
    raw_level, _ = to_level(raw_score, cfg)
    # 创新③：证据一致度——有多少子分同时指向"中度以上"疲劳
    moderate_th = float((cfg or {}).get("fusion", {}).get("level_thresholds", {}).get("moderate", 0.5))
    agreement = sum(1 for s in sub.values() if s is not None and s >= moderate_th)
    alarm = fsm.update(raw_level, raw_score, agreement)
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
        kss=score_to_kss(score),        # 创新④：Karolinska 嗜睡量表 1-9
        reliabilities=rels,
    )


def score_to_kss(score: float) -> int:
    """创新④：融合分 S∈[0,1] → Karolinska 嗜睡量表 KSS 1-9。

    KSS 是睡眠研究公认的主观嗜睡量表（1=极清醒 … 9=极困、难以保持清醒），
    师兄论文也用它做标注。把连续融合分线性映射到 KSS，给四级判定一个国际
    通用、可解释的科学刻度（参考多篇用连续输出映射 KSS 的工作）。
    """
    return int(max(1, min(9, round(1 + _clamp01(score) * 8))))
