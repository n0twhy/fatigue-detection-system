# -*- coding: utf-8 -*-
"""②③④⑤⑥ 各功能面板（开发规格书 §4 / §6.12），现代深色仪表盘风格。

    FeaturePanel  ② 特征参数区：指标磁贴网格（逐帧 + 滑窗）+ 基线条
    LevelPanel    ③ 疲劳等级区：主视觉等级 + 自绘评分条 + 子分磁贴
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
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult, LEVEL_NAMES
from fatigue_system.io.video_source import list_cameras
from fatigue_system.ui import theme
from fatigue_system.ui.widgets import ScoreMeter, StatTile

# 头部状态英文 → 中文（展示用）
HEAD_STATE_CN = {"normal": "正常", "lowered": "低头", "tilted": "偏头", "nodding": "点头"}

# 头部状态 → 颜色（正常绿，异常按轻重取橙/红）
_HEAD_STATE_COLOR = {
    "normal": theme.LEVEL_COLORS[0],
    "lowered": theme.LEVEL_COLORS[2],
    "tilted": theme.LEVEL_COLORS[2],
    "nodding": theme.LEVEL_COLORS[3],
}


# ------------------------------ ② 特征参数区 ----------------------------------

class FeaturePanel(QWidget):
    """指标磁贴网格：逐帧(EAR/MAR/头部) + 滑窗(PERCLOS/眨眼/闭眼/哈欠/点头/HR/HRV)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        title = QLabel("实时指标 · LIVE METRICS", self)
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)
        # (key, 标签, 是否等宽数字)
        specs = [
            ("ear", "EAR", True), ("mar", "MAR", True), ("head", "头部姿态", False),
            ("perclos", "PERCLOS", True), ("blink", "眨眼/分", True),
            ("closed", "最长闭眼", True), ("yawn", "哈欠", True),
            ("nod", "点头", True), ("hr", "心率 HR", True), ("hrv", "HRV", True),
        ]
        self._tiles: Dict[str, StatTile] = {}
        cols = 5
        for i, (key, label, mono) in enumerate(specs):
            tile = StatTile(label, mono=mono, value_px=19)
            self._tiles[key] = tile
            grid.addWidget(tile, i // cols, i % cols)
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        root.addLayout(grid)

        self._baseline = QLabel("基线：未校准 · 使用默认阈值", self)
        self._baseline.setObjectName("baseline")
        root.addWidget(self._baseline)

    def update_frame(self, ff: FrameFeatures, head_state: str) -> None:
        """更新逐帧磁贴（EAR/MAR/头部）；head_state 为英文状态。"""
        if not ff.face_found:
            for k in ("ear", "mar", "head"):
                self._tiles[k].set_value("—", theme.TEXT_MUTE)
            return
        self._tiles["ear"].set_value("{:.3f}".format(ff.ear), theme.ACCENT)
        self._tiles["mar"].set_value("{:.3f}".format(ff.mar), theme.ACCENT)
        self._tiles["head"].set_value(
            HEAD_STATE_CN.get(head_state, head_state),
            _HEAD_STATE_COLOR.get(head_state, theme.TEXT))

    def update_window(self, wf: WindowFeatures) -> None:
        """更新滑窗磁贴（HR/HRV 缺失时显示 —，M4 起有值）。"""
        self._tiles["perclos"].set_value("{:.0%}".format(wf.perclos))
        self._tiles["blink"].set_value("{:.1f}".format(wf.blink_rate))
        self._tiles["closed"].set_value("{:.1f}s".format(wf.eye_closed_dur))
        yawn = "{}{}".format(wf.yawn_count, "•" if wf.yawn_flag else "")
        self._tiles["yawn"].set_value(yawn)
        self._tiles["nod"].set_value(str(wf.nod_count))
        self._tiles["hr"].set_value(
            "{:.0f}".format(wf.hr) if wf.hr is not None else "—",
            theme.ACCENT_2 if wf.hr is not None else theme.TEXT_MUTE)
        self._tiles["hrv"].set_value(
            "{:.0f}".format(wf.hrv) if wf.hrv is not None else "—",
            theme.TEXT if wf.hrv is not None else theme.TEXT_MUTE)

    def set_window_idle(self, text: str = "") -> None:
        for k in ("perclos", "blink", "closed", "yawn", "nod", "hr", "hrv"):
            self._tiles[k].set_value("—", theme.TEXT_MUTE)

    def set_frame_idle(self, text: str = "") -> None:
        for k in ("ear", "mar", "head"):
            self._tiles[k].set_value("—", theme.TEXT_MUTE)

    def set_baseline_text(self, text: str) -> None:
        self._baseline.setText(text)


# ------------------------------ ③ 疲劳等级区 ----------------------------------

class LevelPanel(QGroupBox):
    """主视觉等级 + 自绘评分条 + 各子分磁贴。"""

    def __init__(self, parent=None):
        super().__init__("疲劳等级 · LEVEL", parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        self._level = QLabel("待机", self)
        self._level.setObjectName("heroLevel")
        self._level.setAlignment(Qt.AlignCenter)
        self._level.setStyleSheet("color:{};".format(theme.TEXT_MUTE))
        lay.addWidget(self._level)

        self._meter = ScoreMeter(self)
        lay.addWidget(self._meter)

        self._score = QLabel("融合分 —", self)
        self._score.setAlignment(Qt.AlignCenter)
        self._score.setStyleSheet(
            "color:{}; font-family:{}; font-size:12px;".format(theme.TEXT_DIM, theme.MONO))
        lay.addWidget(self._score)

        subs = QHBoxLayout()
        subs.setSpacing(8)
        self._sub_tiles: Dict[str, StatTile] = {}
        for key, label in [("eye", "眼"), ("mouth", "嘴"), ("head", "头"), ("physio", "生理")]:
            tile = StatTile(label, mono=True, value_px=16)
            self._sub_tiles[key] = tile
            subs.addWidget(tile)
        lay.addLayout(subs)

    def set_result(self, result: FatigueResult) -> None:
        color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        self._level.setText(result.level_name)
        self._level.setStyleSheet("color:{};".format(color))
        self._meter.set_score(result.score, int(result.level))
        # 融合分 + KSS 嗜睡量表刻度（创新④）
        self._score.setText("融合分 {:.3f}   ·   KSS {}/9".format(result.score, result.kss))
        sub = result.sub_scores or {}
        for key, tile in self._sub_tiles.items():
            v = sub.get(key)
            tile.set_value("—" if v is None else "{:.2f}".format(v),
                           theme.TEXT_MUTE if v is None else theme.TEXT)

    def set_idle(self, text: str = "待机") -> None:
        self._level.setText(text)
        self._level.setStyleSheet("color:{};".format(theme.TEXT_MUTE))
        self._meter.set_score(0.0, 0)
        self._score.setText("融合分 —")
        for tile in self._sub_tiles.values():
            tile.set_value("—", theme.TEXT_MUTE)


# ------------------------------ ④ 预警提示区 ----------------------------------

class _AlarmSound:
    """报警声音：优先 QSound 播放自动生成的提示 wav，失败退回系统蜂鸣。"""

    def __init__(self, wav_path: str):
        self._qsound = None
        try:
            from PyQt5.QtMultimedia import QSound
            self._ensure_wav(wav_path)
            self._qsound = QSound(wav_path)
        except Exception:
            self._qsound = None

    @staticmethod
    def _ensure_wav(path: str) -> None:
        if os.path.isfile(path):
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        rate = 44100
        import math
        frames = bytearray()
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

    _OK_STYLE = ("background-color:{}; color:{}; border:1px solid {}; border-radius:8px; "
                 "padding:10px; font-size:14px;").format("#0f2a1e", theme.LEVEL_COLORS[0], "#1c3d2b")
    _ALARM_STYLE = ("background-color:{}; color:#ffffff; border:1px solid {}; border-radius:8px; "
                    "padding:10px; font-size:14px; font-weight:bold;").format("#3d1418", theme.LEVEL_COLORS[3])

    def __init__(self, cfg: Dict, parent=None):
        super().__init__("实时预警 · ALERT", parent)
        alarm_cfg = (cfg or {}).get("alarm", {})
        self._sound_enable = bool(alarm_cfg.get("sound_enable", True))
        self._popup_enable = bool(alarm_cfg.get("popup_enable", True))
        self._repeat_sec = float(alarm_cfg.get("repeat_sec", 5.0))
        wav = alarm_cfg.get("wav_path") or os.path.join(
            (cfg or {}).get("logging", {}).get("csv_dir", "fatigue_system/outputs"), "alarm.wav")
        self._sound = _AlarmSound(wav) if self._sound_enable else None

        lay = QVBoxLayout(self)
        self._banner = QLabel("● 正常监测中", self)
        self._banner.setAlignment(Qt.AlignCenter)
        self._banner.setStyleSheet(self._OK_STYLE)
        lay.addWidget(self._banner)
        self._info = QLabel("累计报警  0", self)
        self._info.setStyleSheet(
            "color:{}; font-family:{}; font-size:12px;".format(theme.TEXT_DIM, theme.MONO))
        lay.addWidget(self._info)

        self._active = False
        self._face_lost = False
        self._alarm_count = 0
        self._last_sound_t: Optional[float] = None
        self._popup: Optional[QMessageBox] = None

    _LOST_STYLE = ("background-color:{}; color:#ffffff; border:1px solid {}; border-radius:8px; "
                   "padding:10px; font-size:14px; font-weight:bold;").format("#3a2d10", theme.LEVEL_COLORS[2])

    def set_face_lost(self, active: bool) -> None:
        """人脸持续丢失（趴睡/离开画面）时的橙色提示 + 提示音（组员反馈#8）。

        不硬判"重度疲劳"（离开画面也可能只是走开，硬判会误报），而是明确提示
        "请回到画面中"并响铃，让检测对象回归；面部回归后自动恢复正常横幅。
        """
        if active and not self._face_lost:
            self._banner.setText("⚠  未检测到人脸 · 请回到摄像头画面中")
            self._banner.setStyleSheet(self._LOST_STYLE)
            self._play_sound()
        elif active:
            if (self._last_sound_t is not None
                    and monotonic() - self._last_sound_t >= self._repeat_sec):
                self._play_sound()
        elif self._face_lost:      # 面部回归 → 恢复正常
            self._banner.setText("● 正常监测中")
            self._banner.setStyleSheet(self._OK_STYLE)
            self._active = False
        self._face_lost = active

    def update_alarm(self, active: bool, level_name: str) -> None:
        if self._face_lost:        # 人脸丢失提示优先，暂不覆盖
            return
        if active and not self._active:
            self._alarm_count += 1
            self._banner.setText("⚠  重度疲劳报警 · 请立即休息")
            self._banner.setStyleSheet(self._ALARM_STYLE)
            self._info.setText("累计报警  {}".format(self._alarm_count))
            self._play_sound()
            self._show_popup(level_name)
        elif active:
            if (self._last_sound_t is not None
                    and monotonic() - self._last_sound_t >= self._repeat_sec):
                self._play_sound()
        elif self._active:
            self._banner.setText("● 报警已解除 · 继续监测")
            self._banner.setStyleSheet(self._OK_STYLE)
            if self._popup is not None:
                self._popup.hide()
        self._active = active

    def set_idle(self) -> None:
        self._active = False
        self._face_lost = False
        self._banner.setText("● 正常监测中")
        self._banner.setStyleSheet(self._OK_STYLE)
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
            self._popup.setWindowModality(Qt.NonModal)
        self._popup.setText("检测到持续{}状态！\n\n请立即停止当前作业并休息。".format(level_name))
        self._popup.show()


# ------------------------------ ⑤ 操作控制区 ----------------------------------

class ControlPanel(QWidget):
    """控制按钮行：对外只发信号，不直接操作检测状态。"""

    open_camera_requested = pyqtSignal(int)
    open_file_requested = pyqtSignal(str)
    calibrate_requested = pyqtSignal()
    record_toggled = pyqtSignal(bool)
    landmarks_toggled = pyqtSignal(bool)
    stop_requested = pyqtSignal()

    def __init__(self, vcfg: Dict, parent=None):
        super().__init__(parent)
        self._vcfg = vcfg or {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(QLabel("摄像头", self))
        self._camera_combo = QComboBox(self)
        self._camera_combo.setMinimumWidth(150)
        lay.addWidget(self._camera_combo)
        self._btn_refresh = QPushButton("刷新", self)
        self._btn_refresh.clicked.connect(self.refresh_cameras)
        lay.addWidget(self._btn_refresh)
        self._btn_camera = QPushButton("打开摄像头", self)
        self._btn_camera.clicked.connect(self._emit_camera)
        lay.addWidget(self._btn_camera)
        self._btn_file = QPushButton("打开视频文件", self)
        self._btn_file.clicked.connect(self._pick_file)
        lay.addWidget(self._btn_file)
        self._btn_calib = QPushButton("开始校准", self)
        self._btn_calib.setProperty("accent", True)
        self._btn_calib.clicked.connect(self.calibrate_requested.emit)
        lay.addWidget(self._btn_calib)
        self._btn_record = QPushButton("开始记录", self)
        self._btn_record.setProperty("accent", True)
        self._btn_record.setCheckable(True)
        self._btn_record.toggled.connect(self._on_record_toggled)
        lay.addWidget(self._btn_record)
        self._chk_landmarks = QCheckBox("关键点", self)
        self._chk_landmarks.setChecked(True)
        self._chk_landmarks.stateChanged.connect(
            lambda s: self.landmarks_toggled.emit(bool(s)))
        lay.addWidget(self._chk_landmarks)
        lay.addStretch(1)
        self._btn_stop = QPushButton("停止", self)
        self._btn_stop.clicked.connect(self.stop_requested.emit)
        lay.addWidget(self._btn_stop)

    def refresh_cameras(self) -> None:
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
        self._emit_camera()

    def set_calibrate_enabled(self, enabled: bool) -> None:
        self._btn_calib.setEnabled(enabled)

    def set_recording(self, recording: bool) -> None:
        self._btn_record.blockSignals(True)
        self._btn_record.setChecked(recording)
        self._btn_record.setText("停止并保存" if recording else "开始记录")
        self._btn_record.blockSignals(False)

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
    _MAX_ROWS = 500

    def __init__(self, parent=None):
        super().__init__("检测记录 · LOG", parent)
        lay = QVBoxLayout(self)
        self._hint = QLabel("未在记录 · 点「开始记录」落盘 CSV 并在此显示", self)
        self._hint.setStyleSheet("color:{}; font-size:12px;".format(theme.TEXT_MUTE))
        lay.addWidget(self._hint)
        self._table = QTableWidget(0, len(self._COLUMNS), self)
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
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
        level_color = theme.LEVEL_COLORS[int(result.level) % len(theme.LEVEL_COLORS)]
        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            if col == 8:      # 等级列上色
                from PyQt5.QtGui import QColor
                item.setForeground(QColor(level_color))
            self._table.setItem(row, col, item)
        self._table.scrollToBottom()
