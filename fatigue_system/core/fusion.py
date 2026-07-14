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
    - 低头越深(相对基线|pitch|越大) → 眼睑关键点越失真、眼部额外降权
      （组员实测：低头时 EAR 幅度压缩检不到闭眼，深低头锁到眉毛，v1.9 补上）；
    - 生理靠 HR 是否可用（None 已在 fuse 里被排除），此处给满可靠度。
    """
    sub = _sub_cfg(cfg)
    face_ratio = _clamp01(getattr(wf, "face_ratio", 1.0))
    yaw_full = float(sub.get("yaw_reliability_deg", 45.0))
    yaw_cap = float(sub.get("yaw_penalty_cap", 0.6))
    yaw_pen = min(yaw_cap, abs(getattr(wf, "mean_abs_yaw", 0.0)) / yaw_full) if yaw_full > 0 else 0.0
    pitch_full = float(sub.get("pitch_reliability_deg", 30.0))
    pitch_cap = float(sub.get("pitch_penalty_cap", 0.8))
    pitch_pen = min(pitch_cap, abs(getattr(wf, "mean_abs_pitch", 0.0)) / pitch_full) if pitch_full > 0 else 0.0
    return {
        "eye": face_ratio * (1.0 - yaw_pen) * (1.0 - pitch_pen),
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
        self.reconfigure(cfg)
        self.reset()

    def reconfigure(self, cfg) -> None:
        """运行时重载防误报参数（「参数设置」面板调参后调用）。

        只更新参数，不动 EMA/报警等运行状态——调参不应打断正在进行的监测。
        """
        fusion_cfg = (cfg or {}).get("fusion", {})
        self._cfg = cfg
        self._alpha = float(fusion_cfg.get("smoothing_alpha", 0.3))
        self._n_alarm = int(fusion_cfg.get("alarm_consecutive_windows", 3))
        self._n_clear = int(fusion_cfg.get("alarm_clear_windows", 2))
        # 创新③：置信度自适应决策窗口——多模态一致高疲劳时可少等几窗即报警
        self._adaptive_reduction = int(fusion_cfg.get("adaptive_alarm_reduction", 2))
        self._agree_conf = int(fusion_cfg.get("agreement_for_confident", 2))
        # 报警冷却（v1.10）：报警解除后的静默窗口数，防止"报警-解除-报警"反复刷屏
        interval = float(fusion_cfg.get("update_interval_sec", 1.0)) or 1.0
        cooldown_sec = float((cfg or {}).get("alarm", {}).get("cooldown_sec", 30.0))
        self._cooldown_windows = int(round(cooldown_sec / interval))

    def reset(self) -> None:
        """复位（切换视频源/重新开始检测时调用）。"""
        self._ema: Optional[float] = None
        self._level: int = LEVEL_AWAKE
        self._severe_run = 0
        self._calm_run = 0
        self._alarm = False
        self._cooldown = 0

    def force_alarm(self) -> None:
        """硬规则（微睡眠/持续低头）直接报警时同步 FSM 状态。

        否则 FSM 自身仍认为"未报警"，硬规则一解除就会出现状态抖动；
        也让冷却期从这次报警结束后开始计。
        """
        self._alarm = True
        self._cooldown = 0

    def update(self, level: int, score: float, agreement: int = 0) -> bool:
        """喂入一个窗口的（原始等级, 原始融合分, 高疲劳子分一致数），返回是否报警。

        内部先对 score 做 EMA 平滑、按平滑分重判等级（对外呈现的评分/等级即此
        平滑值）。**创新③**：当多个子分一致指向高疲劳（agreement≥阈值）时，说明
        证据充分、置信度高，缩短所需"连续重度窗口数"以更快报警；证据单薄时维持
        原窗口数谨慎判定——在灵敏与防误报间自适应（参考 Frontiers 2026）。
        **报警冷却（v1.10）**：解除后 alarm.cooldown_sec 内不再因评分再次报警，
        避免评分在重度线上下抖动时反复触发（老师实测被连报几十次）。
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

        if self._cooldown > 0:
            self._cooldown -= 1

        # 置信度自适应：证据一致时减少所需连续窗口数（至少 1）
        n_alarm = self._n_alarm
        if agreement >= self._agree_conf:
            n_alarm = max(1, self._n_alarm - self._adaptive_reduction)

        if not self._alarm and self._severe_run >= n_alarm and self._cooldown <= 0:
            self._alarm = True
        elif self._alarm and self._calm_run >= self._n_clear:
            self._alarm = False
            self._cooldown = self._cooldown_windows   # 进入冷却期
        return self._alarm

    @property
    def in_cooldown(self) -> bool:
        """当前是否处于报警冷却期（供界面/日志展示）。"""
        return self._cooldown > 0

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


def evidence_channels(sub_scores: Dict[str, Optional[float]], cfg) -> int:
    """有多少个模态**独立**给出了疲劳证据（子分 ≥ evidence_subscore_thresh）。

    用于"多特征证据门"（见 evaluate）：老师建议——疲劳分要多个特征同时满足
    才允许大幅上升，单一特征异常不足以判重度。
    """
    fusion_cfg = (cfg or {}).get("fusion", {})
    th = float(fusion_cfg.get("evidence_subscore_thresh", 0.5))
    return sum(1 for s in sub_scores.values() if s is not None and s >= th)


def evaluate(wf, baseline, cfg, fsm: AlarmFSM) -> FatigueResult:
    """便捷封装：子分 → 融合 → 证据门 → FSM → FatigueResult（GUI/记录直接用）。

    按 fusion.update_interval_sec 节拍调用一次（不要按帧调用，见 AlarmFSM 注释）。
    """
    sub = {
        "eye": eye_subscore(wf, baseline, cfg),
        "mouth": mouth_subscore(wf, baseline, cfg),
        "head": head_subscore(wf, baseline, cfg),
        "physio": physio_subscore(wf, baseline, cfg),
    }
    fusion_cfg = (cfg or {}).get("fusion", {})
    th_cfg = fusion_cfg.get("level_thresholds", {})
    severe_th = float(th_cfg.get("severe", 0.70))
    weights = fusion_cfg.get("weights", {})
    rels = reliabilities(wf, cfg)                        # 创新①：质量感知加权
    raw_score = fuse(sub, weights, rels)

    # ---------------- 多特征证据门（老师建议，v1.10）----------------
    # 只有 ≥ severe_min_channels 个模态各自给出证据时，融合分才允许进入"重度区"；
    # 否则封顶在重度线之下（最高只到"中度"预警）。这防的是**单一特征的伪证据**：
    # 低头看键盘会同时压低 EAR（下视遮眼）并触发低头，看似两个特征，实为同一动作
    # 的产物——故眼部在低头时已被判为不可信（feature_window._eye_valid），此处证据
    # 门再兜一层：真正的重度疲劳应当有多个**互相独立**的信号同时出现。
    n_evidence = evidence_channels(sub, cfg)
    min_ch = int(fusion_cfg.get("severe_min_channels", 2))
    gated = False
    if min_ch > 1 and n_evidence < min_ch and raw_score >= severe_th:
        raw_score = severe_th - 0.01     # 封顶：最高只到"中度"
        gated = True

    raw_level, _ = to_level(raw_score, cfg)
    # 创新③：证据一致度——有多少子分同时指向"中度以上"疲劳
    moderate_th = float(th_cfg.get("moderate", 0.5))
    agreement = sum(1 for s in sub.values() if s is not None and s >= moderate_th)
    alarm = fsm.update(raw_level, raw_score, agreement)
    level = fsm.smoothed_level
    score = fsm.smoothed_score
    if gated:                            # 平滑分同样不得越过重度线
        score = min(score, severe_th - 0.01)
        level, _ = to_level(score, cfg)

    # ---------------- 硬规则（越过 EMA 与"连续N窗"，但都带护栏）----------------
    # ① 微睡眠：持续闭眼超阈值 → 立即重度报警（反馈#1/#5）。
    #    双重护栏（v1.10，老师实测 10 分钟误报 20 次的根因）：
    #      (a) 眼部实时可靠度达标——低头/侧脸/丢脸时 EAR 失真，不认；
    #      (b) 必须是**深度闭眼**（current_deep_closed_dur，见 feature_window.
    #          _deep_closed_thresh）——"低头看键盘"下视会把 EAR 压到普通闭眼阈
    #          附近(0.13~0.14)但远不到真闭眼水平(≈0.08)。(b) 不依赖姿态推断，
    #          即使"开机时就低着头"导致俯仰零点被带偏也不会误报。
    micro = float(fusion_cfg.get("microsleep_sec", 2.0))
    min_eye_rel = float(fusion_cfg.get("microsleep_min_eye_reliability", 0.6))
    eye_trustworthy = rels.get("eye", 1.0) >= min_eye_rel
    micro_hit = (micro > 0 and eye_trustworthy
                 and getattr(wf, "current_deep_closed_dur", 0.0) >= micro)
    # ② 持续低头：头埋下去打瞌睡时眼部已不可测，持续低头是最后可用的疲劳证据。
    #    护栏（v1.10）：阈值从 8s 提到 20s——低头看键盘/看资料/写字都是日常动作，
    #    8 秒太短会误报；真打瞌睡的埋头会持续更久且不抬起。
    head_down = float(fusion_cfg.get("head_down_sec", 20.0))
    head_hit = (head_down > 0
                and getattr(wf, "current_lowered_dur", 0.0) >= head_down)
    if micro_hit or head_hit:
        level = LEVEL_SEVERE
        alarm = True
        score = max(score, severe_th)   # 分数也顶到重度线，界面显示一致
        fsm.force_alarm()               # 同步 FSM 状态，避免下一窗立刻"解除"抖动

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
