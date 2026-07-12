# -*- coding: utf-8 -*-
"""核心数据结构定义（开发规格书 §6.1）。

本模块只定义"接缝"数据结构，串联三条处理链：
    逐帧特征 FrameFeatures → 滑窗聚合 WindowFeatures → 融合判定 FatigueResult

说明：
  * 环境为 Python 3.8，规格书中 ``tuple | None`` 一类 PEP 604 联合类型语法
    在 3.8 不可用，此处统一改用 ``typing.Optional`` 等价表达。
  * 全部字段带单位说明；除时间戳 ``ts`` 外均给出中性默认值，方便"未检测到
    人脸"等场景直接构造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# -----------------------------------------------------------------------------
# 共享命名约定（非可调参数，属于类型语义的一部分，故放在 types 而非 config）
# -----------------------------------------------------------------------------

# 疲劳四级：索引即等级编号 0..3，与 FatigueResult.level 对应
LEVEL_NAMES: Tuple[str, str, str, str] = ("清醒", "轻度疲劳", "中度疲劳", "重度疲劳")

# 头部姿态状态取值，与 WindowFeatures.head_state 对应
HEAD_STATE_NORMAL = "normal"      # 中性/正视
HEAD_STATE_LOWERED = "lowered"    # 低头
HEAD_STATE_TILTED = "tilted"      # 偏头
HEAD_STATE_NODDING = "nodding"    # 点头（俯仰周期性摆动）


@dataclass
class FrameFeatures:
    """单帧原始特征（由逐帧特征链在每一帧产出，M1 起使用）。

    字段单位：
        ts        —— 时间戳，单位秒。摄像头用单调时钟；视频文件用视频内时间。
        face_found—— 本帧是否检测到人脸；为 False 时其余特征无效（取默认值）。
        ear       —— 双眼平均 EAR（眼纵横比），无量纲。
        left_ear  —— 左眼 EAR。
        right_ear —— 右眼 EAR。
        mar       —— 嘴纵横比 MAR，无量纲。
        pitch     —— 头部俯仰角，单位度（低头为正方向，具体符号由 head_pose 约定）。
        yaw       —— 头部偏航角，单位度。
        roll      —— 头部翻滚角，单位度。
        roi_rgb   —— 面颊+鼻部肤色 ROI 的 (R, G, B) 均值，取值 0..255；供实时 rPPG
                     使用；未检测到人脸或未取 ROI 时为 None。
    """

    ts: float
    face_found: bool = False
    ear: float = 0.0
    left_ear: float = 0.0
    right_ear: float = 0.0
    mar: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    roi_rgb: Optional[Tuple[float, float, float]] = None


@dataclass
class WindowFeatures:
    """滑窗聚合特征（由 feature_window 在每个统计窗口产出，M2 起使用）。

    字段单位：
        perclos       —— 窗口内闭眼帧占比，取值 0..1。
        blink_rate    —— 眨眼率，单位 次/分。
        blink_count   —— 眨眼统计窗口内的眨眼次数（原始计数，便于人工核对）。
        eye_closed_dur—— 窗口内最长连续闭眼时长，单位秒。
        current_closed_dur—— 截至当前帧、正在进行的连续闭眼时长，单位秒
                     （用于微睡眠直接报警；区别于历史最长值）。
        yawn_count    —— 窗口内哈欠次数。
        yawn_flag     —— 当前是否处于哈欠状态。
        mar_mean      —— 窗口内 MAR 均值。
        head_state    —— 头部状态，取 HEAD_STATE_* 之一。
        nod_count     —— 窗口内点头次数。
        hr            —— 心率，单位 bpm；来自实时 rPPG，缺失时为 None。
        hrv           —— 心率变异性（如 RMSSD），单位 ms；缺失时为 None。
    """

    perclos: float = 0.0
    blink_rate: float = 0.0
    blink_count: int = 0
    eye_closed_dur: float = 0.0
    current_closed_dur: float = 0.0
    avg_blink_dur: float = 0.0      # 创新②：窗口内平均眨眼时长（秒），微睡眠判别指标
    microsleep_count: int = 0       # 创新②：窗口内微睡眠次数（连续闭眼 >0.5s）
    yawn_count: int = 0
    yawn_flag: bool = False
    mar_mean: float = 0.0
    head_state: str = HEAD_STATE_NORMAL
    nod_count: int = 0
    face_ratio: float = 1.0         # 创新①：窗口内检出人脸的帧占比（信号质量代理）
    mean_abs_yaw: float = 0.0       # 创新①：窗口内平均|偏航角|（头转越大眼部越不可靠）
    hr: Optional[float] = None
    hrv: Optional[float] = None


@dataclass
class FatigueResult:
    """融合判定结果（由 fusion 在每个窗口产出，M3 起使用）。

    字段单位：
        score      —— 加权融合疲劳分 S，取值 0..1，越大越疲劳。
        level      —— 疲劳等级 0..3（0 清醒 / 1 轻度 / 2 中度 / 3 重度）。
        level_name —— 等级中文名，取自 LEVEL_NAMES[level]。
        sub_scores —— 各子分明细 {'eye','mouth','head','physio'}，取值 0..1。
        alarm      —— 是否触发预警（经防误报状态机 AlarmFSM 判定）。
        kss        —— 创新④：Karolinska 嗜睡量表刻度 1..9（1 极清醒…9 极困）。
        reliabilities—— 创新①：各子分实时可靠度 0..1（质量感知融合用的权重系数）。
    """

    score: float = 0.0
    level: int = 0
    level_name: str = LEVEL_NAMES[0]
    sub_scores: Dict[str, float] = field(default_factory=dict)
    alarm: bool = False
    kss: int = 1
    reliabilities: Dict[str, float] = field(default_factory=dict)


@dataclass
class Baseline:
    """个性化清醒态基线（由 calibration 在启动 30s 校准后产出，M2 起使用）。

    体现规格书 §3 延伸③：之后按"偏离本人基线"判疲劳，而非固定绝对阈值。

    字段单位：
        ear_open_mean   —— 清醒睁眼 EAR 均值（无量纲）。
        ear_open_std    —— 清醒睁眼 EAR 标准差。
        mar_closed_mean —— 闭口 MAR 均值。
        pitch/yaw/roll  —— 头部中性角均值（度），作为头姿判定的零点。
        hr_rest         —— 静息心率均值(bpm)；M4 有 rPPG 才有值，否则 None。
        n_samples       —— 参与统计的有效帧数。
        valid           —— 是否成功完成校准（样本足够）。
    """

    ear_open_mean: float = 0.30
    ear_open_std: float = 0.03
    mar_closed_mean: float = 0.05
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    hr_rest: Optional[float] = None
    n_samples: int = 0
    valid: bool = False
