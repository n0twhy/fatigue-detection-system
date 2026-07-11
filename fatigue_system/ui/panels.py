# -*- coding: utf-8 -*-
"""②③④⑤⑥ 各功能面板（开发规格书 §4 / §6.12）。

    FeaturePanel  ② 特征参数区：逐帧 / 滑窗 / 基线 三行
    LevelPanel    ③ 疲劳等级区：四级 + 颜色（绿/黄/橙/红）+ 融合分 + 子分
    AlarmPanel    ④ 预警提示区：状态横幅 + 重度报警弹窗 + 声音
    ControlPanel  ⑤ 操作控制区：摄像头下拉/视频文件/校准/记录/停止
    LogTablePanel ⑥ 数据记录区：滚动表格（与 CSV 落盘同节拍追加）

主窗口只做接线：数据对象进面板，展示格式由各面板自理。
"""

import os
import struct
import wave
from time import monotonic
from typing import Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult, LEVEL_NAMES
from fatigue_system.io.video_source import list_cameras

# 头部状态英文 → 中文（展示用）
HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}

# 四级配色（③ 等级区背景 / ⑥ 表格等级列前景）：绿/黄/橙/红
_LEVEL_COLORS = ("#2e7d32", "#f9a825", "#ef6c00", "#c62828")


# ------------------------------ ② 特征参数区 ----------------------------------

class FeaturePanel(QWidget):
    """三行文字：逐帧指标 / 滑窗统计（含 HR/HRV 占位）/ 基线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._frame_label = QLabel("逐帧：等待视频源…", self)
        self._frame_label.setStyleSheet("color:#1a5; padding:2px; font-size:13px; background:#f2f7f2;")
        self._window_label = QLabel("窗口：—", self)
        self._window_label.setStyleSheet("color:#25a; padding:2px; font-size:13px; background:#eef2fb;")
        self._calib_label = QLabel("基线：未校准（将使用默认阈值）", self)
        self._calib_label.setStyleSheet("color:#a60; padding:2px; font-size:13px; background:#fbf6ee;")
        for w in (self._frame_label, self._window_label, self._calib_label):
            lay.addWidget(w)

    def update_frame(self, ff: FrameFeatures, head_state: str) -> None:
        """更新逐帧行；head_state 为英文状态（本面板转中文）。"""
        if not ff.face_found:
            self._frame_label.setText("逐帧：未检测到人脸")
            return
        self._frame_label.setText(
            "逐帧：EAR {ear:.3f}（左{le:.2f}/右{re:.2f}） | MAR {mar:.3f} | "
            "俯仰 {p:+.1f}° 偏航 {y:+.1f}° 翻滚 {r:+.1f}° | 头部 {st}".format(
                ear=ff.ear, le=ff.left_ear, re=ff.right_ear, mar=ff.mar,
                p=ff.pitch, y=ff.yaw, r=ff.roll,
                st=HEAD_STATE_CN.get(head_state, head_state)))

    def update_window(self, wf: WindowFeatures) -> None:
        """更新滑窗行（HR/HRV 缺失时显示 —，M4 起有值）。"""
        hr = "{:.0f}".format(wf.hr) if wf.hr is not None else "—"
        hrv = "{:.0f}".format(wf.hrv) if wf.hrv is not None else "—"
        self._window_label.setText(
            "窗口：PERCLOS {pc:.0%} | 眨眼 {bc}次·{br:.1f}次/分 | 最长闭眼 {cd:.1f}s | "
            "哈欠 {yc}次{yf} | 头部 {hs} | 点头 {nd}次 | HR {hr} | HRV {hrv}".format(
                pc=wf.perclos, bc=wf.blink_count, br=wf.blink_rate, cd=wf.eye_closed_dur,
                yc=wf.yawn_count, yf="(进行中)" if wf.yawn_flag else "",
                hs=HEAD_STATE_CN.get(wf.head_state, wf.head_state), nd=wf.nod_count,
                hr=hr, hrv=hrv))

    def set_window_idle(self, text: str = "窗口：—") -> None:
        self._window_label.setText(text)

    def set_frame_idle(self, text: str) -> None:
        self._frame_label.setText(text)

    def set_baseline_text(self, text: str) -> None:
        """基线行由主窗口按校准状态提供文案（进行中/完成/失败）。"""
        self._calib_label.setText(text)


# ------------------------------ ③ 疲劳等级区 ----------------------------------

class LevelPanel(QGroupBox):
    """大字等级 + 背景配色 + 融合分 + 各子分明细。"""

    def __init__(self, parent=None):
        super().__init__("③ 疲劳等级", parent)
        lay = QVBoxLayout(self)
        self._level_label = QLabel("未开始", self)
        self._level_label.setAlignment(Qt.AlignCenter)
        self._level_label.setMinimumHeight(64)
        self._level_label.setStyleSheet(
            "background:#555; color:white; font-size:28px; font-weight:bold; border-radius:6px;")
        lay.addWidget(self._level_label)
        self._score_label = QLabel("融合分 S：—", self)
        self._score_label.setStyleSheet("font-size:13px;")
        lay.addWidget(self._score_label)
        self._sub_label = QLabel("子分：—", self)
        self._sub_label.setStyleSheet("font-size:12px; color:#555;")
        self._sub_label.setWordWrap(True)
        lay.addWidget(self._sub_label)

    def set_result(self, result: FatigueResult) -> None:
        color = _LEVEL_COLORS[int(result.level) % len(_LEVEL_COLORS)]
        self._level_label.setText(result.level_name)
        self._level_label.setStyleSheet(
            "background:{}; color:white; font-size:28px; font-weight:bold; border-radius:6px;".format(color))
        self._score_label.setText("融合分 S：{:.3f}（EMA 平滑）".format(result.score))
        sub = result.sub_scores or {}

        def _s(key):
            v = sub.get(key)
            return "—" if v is None else "{:.2f}".format(v)
        self._sub_label.setText("子分：眼 {} | 嘴 {} | 头 {} | 生理 {}".format(
            _s("eye"), _s("mouth"), _s("head"), _s("physio")))

    def set_idle(self, text: str = "未开始") -> None:
        self._level_label.setText(text)
        self._level_label.setStyleSheet(
            "background:#555; color:white; font-size:28px; font-weight:bold; border-radius:6px;")
        self._score_label.setText("融合分 S：—")
        self._sub_label.setText("子分：—")


# ------------------------------ ④ 预警提示区 ----------------------------------

class _AlarmSound:
    """报警声音：优先 QSound 播放自动生成的提示 wav，失败退回系统蜂鸣。

    wav 不存在时用 wave 模块合成（880Hz 正弦 0.6s，双声调），
    避免往仓库里塞二进制资源。
    """

    def __init__(self, wav_path: str):
        self._qsound = None
        try:
            from PyQt5.QtMultimedia import QSound
            self._ensure_wav(wav_path)
            self._qsound = QSound(wav_path)
        except Exception:
            self._qsound = None   # 无 QtMultimedia / 无音频后端 → 退回蜂鸣

    @staticmethod
    def _ensure_wav(path: str) -> None:
        if os.path.isfile(path):
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        rate = 44100
        import math
        frames = bytearray()
        # 两段音调（880Hz→660Hz）各 0.3s，首尾 10ms 淡入淡出防爆音
        for freq in (880.0, 660.0):
            n = int(rate * 0.3)
            for i in range(n):
                amp = 0.6
                fade = min(1.0, i / (rate * 0.01), (n - 1 - i) / (rate * 0.01))
                val = int(32767 * amp * fade * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<h", val)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))

    def play(self) -> None:
        if self._qsound is not None:
            try:
                self._qsound.play()
                return
            except Exception:
                pass
        from PyQt5.QtWidgets import QApplication
        QApplication.beep()


class AlarmPanel(QGroupBox):
    """预警横幅 + 重度报警弹窗（非模态）+ 声音（上升沿播放，持续期重复）。"""

    def __init__(self, cfg: Dict, parent=None):
        super().__init__("④ 预警提示", parent)
        alarm_cfg = (cfg or {}).get("alarm", {})
        self._sound_enable = bool(alarm_cfg.get("sound_enable", True))
        self._popup_enable = bool(alarm_cfg.get("popup_enable", True))
        self._repeat_sec = float(alarm_cfg.get("repeat_sec", 5.0))
        wav = alarm_cfg.get("wav_path") or os.path.join(
            (cfg or {}).get("logging", {}).get("csv_dir", "fatigue_system/outputs"), "alarm.wav")
        self._sound = _AlarmSound(wav) if self._sound_enable else None

        lay = QVBoxLayout(self)
        self._banner = QLabel("正常监测中", self)
        self._banner.setAlignment(Qt.AlignCenter)
        self._banner.setMinimumHeight(40)
        self._banner.setStyleSheet(
            "background:#e8f5e9; color:#2e7d32; font-size:15px; border-radius:4px;")
        lay.addWidget(self._banner)
        self._info = QLabel("报警次数：0", self)
        self._info.setStyleSheet("font-size:12px; color:#555;")
        lay.addWidget(self._info)

        self._active = False
        self._alarm_count = 0
        self._last_sound_t: Optional[float] = None
        self._popup: Optional[QMessageBox] = None

    def update_alarm(self, active: bool, level_name: str) -> None:
        """每个融合节拍调用：处理横幅/弹窗/声音的沿触发与重复提醒。"""
        if active and not self._active:          # 上升沿：触发报警
            self._alarm_count += 1
            self._banner.setText("⚠ 重度疲劳报警！请立即休息")
            self._banner.setStyleSheet(
                "background:#c62828; color:white; font-size:15px; font-weight:bold; border-radius:4px;")
            self._info.setText("报警次数：{}".format(self._alarm_count))
            self._play_sound()
            self._show_popup(level_name)
        elif active:                              # 持续报警：按间隔重复提示音
            if (self._last_sound_t is not None
                    and monotonic() - self._last_sound_t >= self._repeat_sec):
                self._play_sound()
        elif self._active:                        # 下降沿：解除
            self._banner.setText("报警已解除，继续监测")
            self._banner.setStyleSheet(
                "background:#e8f5e9; color:#2e7d32; font-size:15px; border-radius:4px;")
            if self._popup is not None:
                self._popup.hide()
        self._active = active

    def set_idle(self) -> None:
        self._active = False
        self._banner.setText("正常监测中")
        self._banner.setStyleSheet(
            "background:#e8f5e9; color:#2e7d32; font-size:15px; border-radius:4px;")
        if self._popup is not None:
            self._popup.hide()

    @property
    def alarm_count(self) -> int:
        return self._alarm_count

    def _play_sound(self) -> None:
        if self._sound is not None:
            self._sound.play()
        self._last_sound_t = monotonic()

    def _show_popup(self, level_name: str) -> None:
        if not self._popup_enable:
            return
        if self._popup is None:
            self._popup = QMessageBox(self)
            self._popup.setIcon(QMessageBox.Warning)
            self._popup.setWindowTitle("疲劳报警")
            self._popup.setStandardButtons(QMessageBox.Ok)
            self._popup.setWindowModality(Qt.NonModal)   # 非模态：不阻塞帧循环
        self._popup.setText("检测到持续{}状态！\n\n请立即停止当前作业并休息。".format(level_name))
        self._popup.show()


# ------------------------------ ⑤ 操作控制区 ----------------------------------

class ControlPanel(QWidget):
    """控制按钮行：对外只发信号，不直接操作检测状态。"""

    open_camera_requested = pyqtSignal(int)
    open_file_requested = pyqtSignal(str)
    calibrate_requested = pyqtSignal()
    record_toggled = pyqtSignal(bool)       # True=开始记录 False=停止并保存
    landmarks_toggled = pyqtSignal(bool)
    stop_requested = pyqtSignal()

    def __init__(self, vcfg: Dict, parent=None):
        super().__init__(parent)
        self._vcfg = vcfg or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lay.addWidget(QLabel("摄像头：", self))
        self._camera_combo = QComboBox(self)
        self._camera_combo.setMinimumWidth(150)
        lay.addWidget(self._camera_combo)
        self._btn_refresh = QPushButton("刷新", self)
        self._btn_refresh.clicked.connect(self.refresh_cameras)
        lay.addWidget(self._btn_refresh)
        self._btn_camera = QPushButton("打开摄像头", self)
        self._btn_camera.clicked.connect(self._emit_camera)
        lay.addWidget(self._btn_camera)
        self._btn_file = QPushButton("打开视频文件…", self)
        self._btn_file.clicked.connect(self._pick_file)
        lay.addWidget(self._btn_file)
        self._btn_calib = QPushButton("开始校准", self)
        self._btn_calib.clicked.connect(self.calibrate_requested.emit)
        lay.addWidget(self._btn_calib)
        self._btn_record = QPushButton("开始记录", self)
        self._btn_record.setCheckable(True)
        self._btn_record.toggled.connect(self._on_record_toggled)
        lay.addWidget(self._btn_record)
        self._chk_landmarks = QCheckBox("显示关键点", self)
        self._chk_landmarks.setChecked(True)
        self._chk_landmarks.stateChanged.connect(
            lambda s: self.landmarks_toggled.emit(bool(s)))
        lay.addWidget(self._chk_landmarks)
        lay.addStretch(1)
        self._btn_stop = QPushButton("停止", self)
        self._btn_stop.clicked.connect(self.stop_requested.emit)
        lay.addWidget(self._btn_stop)

    # ------------------------------ 对外方法 ---------------------------------

    def refresh_cameras(self) -> None:
        """枚举摄像头填充下拉框；无摄像头时禁用相关控件。"""
        self._camera_combo.clear()
        cameras = list_cameras()
        if cameras:
            for cam in cameras:
                self._camera_combo.addItem(
                    "[{}] {}".format(cam["index"], cam["name"]), cam["index"])
            self._camera_combo.setEnabled(True)
            self._btn_camera.setEnabled(True)
        else:
            self._camera_combo.addItem("（未检测到摄像头）", None)
            self._camera_combo.setEnabled(False)
            self._btn_camera.setEnabled(False)

    def camera_available(self) -> bool:
        return self._btn_camera.isEnabled()

    def select_camera(self, index: int) -> None:
        pos = self._camera_combo.findData(index)
        if pos >= 0:
            self._camera_combo.setCurrentIndex(pos)

    def request_open_camera(self) -> None:
        """程序化触发"打开摄像头"（自动启动用）。"""
        self._emit_camera()

    def set_calibrate_enabled(self, enabled: bool) -> None:
        self._btn_calib.setEnabled(enabled)

    def set_recording(self, recording: bool) -> None:
        """同步记录按钮状态（不触发信号）。"""
        self._btn_record.blockSignals(True)
        self._btn_record.setChecked(recording)
        self._btn_record.setText("停止并保存" if recording else "开始记录")
        self._btn_record.blockSignals(False)

    # ------------------------------ 内部槽 -----------------------------------

    def _emit_camera(self) -> None:
        index = self._camera_combo.currentData()
        if index is not None:
            self.open_camera_requested.emit(int(index))

    def _pick_file(self) -> None:
        start_dir = self._vcfg.get("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", start_dir,
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv);;所有文件 (*.*)")
        if path:
            self.open_file_requested.emit(path)

    def _on_record_toggled(self, checked: bool) -> None:
        self._btn_record.setText("停止并保存" if checked else "开始记录")
        self.record_toggled.emit(checked)


# ------------------------------ ⑥ 数据记录区 ----------------------------------

class LogTablePanel(QGroupBox):
    """滚动表格：与 CSV 落盘同节拍追加一行，超出上限后从头部丢弃。"""

    _COLUMNS = ["时间(s)", "EAR", "MAR", "PERCLOS", "眨眼/分", "哈欠", "头部", "评分", "等级", "报警"]
    _MAX_ROWS = 500   # 界面显示上限（完整数据在 CSV 里，此处仅防内存无限涨）

    def __init__(self, parent=None):
        super().__init__("⑥ 数据记录", parent)
        lay = QVBoxLayout(self)
        self._hint = QLabel("未在记录（点「开始记录」落盘 CSV 并在此显示）", self)
        self._hint.setStyleSheet("font-size:12px; color:#777;")
        lay.addWidget(self._hint)
        self._table = QTableWidget(0, len(self._COLUMNS), self)
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self._table)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def clear_rows(self) -> None:
        self._table.setRowCount(0)

    def append_row(self, ff: FrameFeatures, wf: WindowFeatures, result: FatigueResult) -> None:
        if self._table.rowCount() >= self._MAX_ROWS:
            self._table.removeRow(0)
        row = self._table.rowCount()
        self._table.insertRow(row)
        values = [
            "{:.1f}".format(ff.ts), "{:.3f}".format(ff.ear), "{:.3f}".format(ff.mar),
            "{:.0%}".format(wf.perclos), "{:.1f}".format(wf.blink_rate),
            str(wf.yawn_count), HEAD_STATE_CN.get(wf.head_state, wf.head_state),
            "{:.3f}".format(result.score), LEVEL_NAMES[int(result.level)],
            "是" if result.alarm else "",
        ]
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()
