# -*- coding: utf-8 -*-
"""CSV 数据记录（开发规格书 §6.11）。

两类输出（写入 config logging.csv_dir 目录）：
  * 检测记录  fatigue_log_<会话时间>.csv —— 规格书 §6.11 固定 20 列，
    按 logging.log_interval_sec 间隔落盘一行（间隔按帧时间戳 ts 判定：
    实时源即真实秒，文件回放即视频内秒，保证批量回放导出的时间轴正确）。
  * 会话汇总  session_summary_<会话时间>.csv —— 各等级时长/占比、报警
    次数、平均帧率、记录行数。

用法（GUI 帧循环内）：
    logger = DataLogger(config)
    logger.start()                    # 点「开始记录」
    wrote = logger.log(ff, wf, res)   # 每帧调用；返回是否写了新行(供UI表格同步)
    logger.stop(avg_fps=...)          # 点「停止并保存」/退出；写汇总并关闭
"""

import csv
import os
from datetime import datetime
from typing import Optional

from fatigue_system.core.types import LEVEL_NAMES

# 规格书 §6.11 固定列（顺序不可改，报告/分析脚本按此解析）
CSV_COLUMNS = [
    "timestamp", "ear", "mar", "pitch", "yaw", "roll",
    "perclos", "blink_rate", "eye_closed_dur", "yawn_count", "head_state",
    "hr", "hrv",
    "eye_score", "mouth_score", "head_score", "physio_score",
    "fatigue_score", "level", "alarm",
]


def _fmt(value, ndigits=3):
    """数值格式化：None → 空串（CSV 中缺失量留空，不写 0 以免混淆）。"""
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, ndigits)
    return value


class DataLogger:
    """检测数据 CSV 记录器（一次 start/stop 为一个会话）。

    构造参数:
        cfg —— 完整配置字典（用 logging 段：csv_dir / log_interval_sec）。
    """

    def __init__(self, cfg):
        log_cfg = (cfg or {}).get("logging", {})
        self._dir = str(log_cfg.get("csv_dir", "fatigue_system/outputs"))
        self._interval = float(log_cfg.get("log_interval_sec", 1.0))
        self._file = None
        self._writer = None
        self._path: Optional[str] = None
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._session_name: Optional[str] = None
        self._start_wall: Optional[datetime] = None
        self._last_row_ts: Optional[float] = None   # 上次写行的帧 ts
        self._prev_ts: Optional[float] = None       # 上帧 ts（累计等级时长用）
        self._prev_level: int = 0
        self._level_dur = [0.0] * len(LEVEL_NAMES)  # 各等级累计时长(秒)
        self._alarm_count = 0                        # 报警触发次数（上升沿）
        self._prev_alarm = False
        self._rows = 0

    # ------------------------------ 会话控制 ---------------------------------

    @property
    def active(self) -> bool:
        """当前是否在记录中。"""
        return self._writer is not None

    @property
    def csv_path(self) -> Optional[str]:
        """当前（或最近一次）检测记录 CSV 的路径。"""
        return self._path

    def start(self) -> str:
        """开始一个记录会话，返回检测记录 CSV 路径。已在记录中则先停止。"""
        if self.active:
            self.stop()
        self._reset_stats()
        os.makedirs(self._dir, exist_ok=True)
        self._start_wall = datetime.now()
        self._session_name = self._start_wall.strftime("%Y%m%d_%H%M%S")
        self._path = os.path.join(self._dir, "fatigue_log_{}.csv".format(self._session_name))
        # newline='' 是 csv 模块要求；utf-8-sig 便于 Excel 直接打开不乱码
        self._file = open(self._path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_COLUMNS)
        self._file.flush()
        return self._path

    def log(self, ff, wf, result) -> bool:
        """喂入一帧的（逐帧特征, 滑窗特征, 融合结果）。

        每帧调用；内部按 ts 间隔决定是否写行。统计量（等级时长/报警次数）
        每帧都累计，不受写行间隔影响。返回本次是否写了新行。
        """
        if not self.active:
            return False

        # 统计：用相邻帧 ts 差累计当前等级的持续时长；报警数上升沿
        if self._prev_ts is not None:
            dt = ff.ts - self._prev_ts
            if 0 < dt < 5.0:   # 跳变（换源/seek）不计入
                self._level_dur[self._prev_level] += dt
        self._prev_ts = ff.ts
        self._prev_level = int(result.level)
        if result.alarm and not self._prev_alarm:
            self._alarm_count += 1
        self._prev_alarm = bool(result.alarm)

        # 按间隔写行
        if self._last_row_ts is not None and ff.ts - self._last_row_ts < self._interval:
            return False
        self._last_row_ts = ff.ts
        sub = result.sub_scores or {}
        self._writer.writerow([
            _fmt(ff.ts), _fmt(ff.ear), _fmt(ff.mar),
            _fmt(ff.pitch, 1), _fmt(ff.yaw, 1), _fmt(ff.roll, 1),
            _fmt(wf.perclos), _fmt(wf.blink_rate, 1), _fmt(wf.eye_closed_dur, 2),
            wf.yawn_count, wf.head_state,
            _fmt(wf.hr, 1), _fmt(wf.hrv, 1),
            _fmt(sub.get("eye")), _fmt(sub.get("mouth")),
            _fmt(sub.get("head")), _fmt(sub.get("physio")),
            _fmt(result.score), int(result.level), int(bool(result.alarm)),
        ])
        self._file.flush()
        self._rows += 1
        return True

    def stop(self, avg_fps: Optional[float] = None) -> Optional[str]:
        """结束会话：关闭检测记录、写会话汇总，返回汇总 CSV 路径。

        未在记录中时安全返回 None（幂等，可挂到退出清理路径）。
        """
        if not self.active:
            return None
        self._file.close()
        self._file = None
        self._writer = None

        total = sum(self._level_dur)
        summary_path = os.path.join(
            self._dir, "session_summary_{}.csv".format(self._session_name))
        with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["项目", "值"])
            w.writerow(["会话开始时间", self._start_wall.strftime("%Y-%m-%d %H:%M:%S")])
            w.writerow(["检测总时长(秒)", round(total, 1)])
            for i, name in enumerate(LEVEL_NAMES):
                ratio = self._level_dur[i] / total if total > 0 else 0.0
                w.writerow(["{}时长(秒)".format(name), round(self._level_dur[i], 1)])
                w.writerow(["{}占比".format(name), "{:.1%}".format(ratio)])
            w.writerow(["报警次数", self._alarm_count])
            w.writerow(["平均帧率(fps)", round(avg_fps, 1) if avg_fps else ""])
            w.writerow(["记录行数", self._rows])
        return summary_path
