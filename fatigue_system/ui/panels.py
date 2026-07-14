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
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from fatigue_system.core.types import FrameFeatures, WindowFeatures, FatigueResult, LEVEL_NAMES
from fatigue_system.io.video_source import list_cameras
from fatigue_system.ui import theme
from fatigue_system.ui.anim import animate
from fatigue_system.ui.widgets import IconButton, StatTile, StatusDot, ThinBar

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

class LevelPanel(QFrame):
    """疲劳等级卡片（DESIGN.md §5.3）：小标签 + 大数字融合分 + 等级 badge +
    KSS + 四行分量细进度条（超过"中度"阈值才变橙）。"""

    def __init__(self, cfg: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        # 分量变橙的阈值：取"中度"分级线（配置驱动，调参面板改后热更新可见）
        self._cfg = cfg or {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        lay.setSpacing(12)

        title = QLabel("疲劳等级", self)
        title.setObjectName("sectionTitle")
        lay.addWidget(title)

        head = QHBoxLayout()
        head.setSpacing(10)
        self._score = QLabel("—", self)
        self._score.setObjectName("bigScore")
        head.addWidget(self._score)
        self._badge = QLabel("待机", self)
        self._set_badge_idle()
        head.addWidget(self._badge)
        self._kss = QLabel("", self)
        self._kss.setObjectName("kssLabel")
        head.addWidget(self._kss)
        head.addStretch(1)
        lay.addLayout(head)

        # 四行分量随卡片高度均匀分布（行间弹性间距，高卡不留死白、矮卡自动紧凑）
        rows_box = QVBoxLayout()
        rows_box.setSpacing(0)
        self._sub_bars: Dict[str, ThinBar] = {}
        self._sub_vals: Dict[str, QLabel] = {}
        for key, label in [("eye", "眼部"), ("mouth", "嘴部"),
                           ("head", "头部"), ("physio", "生理")]:
            rows_box.addStretch(1)
            row = QHBoxLayout()
            row.setSpacing(10)
            lab = QLabel(label, self)
            lab.setObjectName("subLabel")
            lab.setFixedWidth(40)
            row.addWidget(lab)
            bar = ThinBar(self)
            self._sub_bars[key] = bar
            row.addWidget(bar, stretch=1)
            val = QLabel("—", self)
            val.setObjectName("subValue")
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setFixedWidth(48)
            self._sub_vals[key] = val
            row.addWidget(val)
            rows_box.addLayout(row)
        rows_box.addStretch(2)
        lay.addLayout(rows_box, stretch=1)

    def _over_thresh(self) -> float:
        return float(self._cfg.get("fusion", {})
                     .get("level_thresholds", {}).get("moderate", 0.5))

    def _set_badge(self, text: str, bg: str, fg: str) -> None:
        self._badge_bg = QColor(bg)
        self._badge_fg = QColor(fg)
        self._badge.setText(text)
        self._apply_badge_style()

    def _apply_badge_style(self) -> None:
        self._badge.setStyleSheet(
            "background-color:{bg}; color:{fg}; border-radius: 13px; "
            "padding: 4px 12px; font-size: 13px;".format(
                bg=self._badge_bg.name(), fg=self._badge_fg.name()))

    def _set_badge_idle(self) -> None:
        self._last_level = None
        self._set_badge("待机", theme.TRACK, theme.TEXT_DIM)

    def _transition_badge(self, text: str, bg: str, fg: str) -> None:
        """§7.3：等级变化时 badge 底色/文字色 250ms 颜色插值（不循环闪烁）。"""
        self._badge.setText(text)
        animate(self, "_anim_bbg", QColor(self._badge_bg), QColor(bg), 250,
                self._on_badge_bg)
        animate(self, "_anim_bfg", QColor(self._badge_fg), QColor(fg), 250,
                self._on_badge_fg)

    def _on_badge_bg(self, c) -> None:
        self._badge_bg = QColor(c)
        self._apply_badge_style()

    def _on_badge_fg(self, c) -> None:
        self._badge_fg = QColor(c)
        self._apply_badge_style()

    def set_result(self, result: FatigueResult) -> None:
        # 大数字随数据流更新，不加动画（§7.0 纪律3 / §7.3）
        self._score.setText("{:.3f}".format(result.score))
        bg, fg = theme.LEVEL_BADGES[int(result.level) % len(theme.LEVEL_BADGES)]
        level = int(result.level)
        if getattr(self, "_last_level", None) is None:
            self._set_badge(result.level_name, bg, fg)      # 首次直接就位
        elif level != self._last_level:
            self._transition_badge(result.level_name, bg, fg)
        else:
            self._badge.setText(result.level_name)
        self._last_level = level
        self._kss.setText("KSS {}/9".format(result.kss))   # 创新④
        sub = result.sub_scores or {}
        over = self._over_thresh()
        for key, bar in self._sub_bars.items():
            v = sub.get(key)
            if v is None:
                bar.set_value(0.0, False)
                self._sub_vals[key].setText("—")
            else:
                bar.set_value(v, v > over)
                self._sub_vals[key].setText("{:.2f}".format(v))

    def set_idle(self, text: str = "待机") -> None:
        self._score.setText("—")
        self._set_badge_idle()
        self._kss.setText("")
        for key, bar in self._sub_bars.items():
            bar.set_value(0.0, False)
            self._sub_vals[key].setText("—")


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


class AlarmPanel(QFrame):
    """预警状态行（DESIGN.md §5.3）：单行卡片=状态圆点+文字（左）与累计次数（右），
    不再用整幅色块。重度报警弹窗（非模态）与声音逻辑保留。"""

    def __init__(self, cfg: Dict, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        alarm_cfg = (cfg or {}).get("alarm", {})
        self._sound_enable = bool(alarm_cfg.get("sound_enable", True))
        self._popup_enable = bool(alarm_cfg.get("popup_enable", True))
        self._repeat_sec = float(alarm_cfg.get("repeat_sec", 5.0))
        wav = alarm_cfg.get("wav_path") or os.path.join(
            (cfg or {}).get("logging", {}).get("csv_dir", "fatigue_system/outputs"), "alarm.wav")
        self._sound = _AlarmSound(wav) if self._sound_enable else None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.PAD_CARD, 14, theme.PAD_CARD, 14)
        lay.setSpacing(8)
        self._text = QLabel(self)
        self._text.setObjectName("alarmText")
        lay.addWidget(self._text)
        lay.addStretch(1)
        self._count_label = QLabel("累计 0 次", self)
        self._count_label.setObjectName("alarmCount")
        lay.addWidget(self._count_label)

        self._active = False
        self._face_lost = False
        self._alarm_count = 0
        self._last_sound_t: Optional[float] = None
        self._popup: Optional[QMessageBox] = None
        self._line_text = ""
        self._line_dot = QColor(theme.GREEN)
        self._line_fg = QColor(theme.GREEN_TEXT)
        # 淡入用的透明度效果（§7.5：报警出现时整行淡入+上移 4px）
        self._fx = QGraphicsOpacityEffect(self._text)
        self._fx.setOpacity(1.0)
        self._text.setGraphicsEffect(self._fx)
        self._set_line("正常监测中", theme.GREEN, theme.GREEN_TEXT)

    def _apply_line(self) -> None:
        self._text.setText(
            '<span style="color:{dot}; font-size:10px;">&#9679;</span>&nbsp; '
            '<span style="color:{fg};">{t}</span>'.format(
                dot=self._line_dot.name(), fg=self._line_fg.name(), t=self._line_text))

    def _set_line(self, text: str, dot: str, fg: str) -> None:
        """单行状态：彩色圆点 + 同色系文字（§5.3，状态只用一行字表达）。"""
        self._line_text = text
        self._line_dot = QColor(dot)
        self._line_fg = QColor(fg)
        self._apply_line()

    def _fade_in_line(self, text: str, dot: str, fg: str) -> None:
        """§7.5 报警/提示出现：内容淡入 + 向上位移 4px 归位，220ms OutCubic。"""
        self._set_line(text, dot, fg)
        animate(self, "_anim_op", 0.0, 1.0, theme.ANIM_BASE, self._fx.setOpacity)
        lay = self.layout()
        pad = theme.PAD_CARD

        def _shift(v):
            lay.setContentsMargins(pad, 14 + int(round(v)), pad, 14 - int(round(v)))
        animate(self, "_anim_sh", 4.0, 0.0, theme.ANIM_BASE, _shift)

    def _color_line(self, text: str, dot: str, fg: str) -> None:
        """§7.5 报警解除：只做颜色过渡（红→绿 250ms），不做位移。"""
        self._line_text = text

        def _dot(c):
            self._line_dot = QColor(c)
            self._apply_line()

        def _fg(c):
            self._line_fg = QColor(c)
            self._apply_line()
        animate(self, "_anim_ld", QColor(self._line_dot), QColor(dot), 250, _dot)
        animate(self, "_anim_lf", QColor(self._line_fg), QColor(fg), 250, _fg)

    def set_face_lost(self, active: bool) -> None:
        """人脸持续丢失（趴睡/离开画面）时的橙色提示 + 提示音（组员反馈#8）。

        不硬判"重度疲劳"（离开画面也可能只是走开，硬判会误报），而是明确提示
        "请回到画面中"并响铃，让检测对象回归；面部回归后自动恢复正常状态行。
        """
        if active and not self._face_lost:
            self._fade_in_line("未检测到人脸，请回到摄像头画面中",
                               theme.ORANGE, theme.ORANGE)
            self._play_sound()
        elif active:
            if (self._last_sound_t is not None
                    and monotonic() - self._last_sound_t >= self._repeat_sec):
                self._play_sound()
        elif self._face_lost:      # 面部回归 → 颜色过渡恢复正常
            self._color_line("正常监测中", theme.GREEN, theme.GREEN_TEXT)
            self._active = False
        self._face_lost = active

    def update_alarm(self, active: bool, level_name: str) -> None:
        if self._face_lost:        # 人脸丢失提示优先，暂不覆盖
            return
        if active and not self._active:
            self._alarm_count += 1
            self._fade_in_line("报警中：持续{}，请立即休息".format(level_name),
                               theme.RED, theme.RED)
            self._count_label.setText("累计 {} 次".format(self._alarm_count))
            self._play_sound()
            self._show_popup(level_name)
        elif active:
            if (self._last_sound_t is not None
                    and monotonic() - self._last_sound_t >= self._repeat_sec):
                self._play_sound()
        elif self._active:
            # §7.5 解除：仅颜色过渡（红→绿），不做位移
            self._color_line("报警已解除，继续监测", theme.GREEN, theme.GREEN_TEXT)
            if self._popup is not None:
                self._popup.hide()
        self._active = active

    def show_trend_hint(self, slope_per_min: float) -> None:
        """趋势预警提示（v1.11 功能③）：温和提示，**不响铃、不弹窗、不计入报警次数**。

        与报警的区别：报警是"已经重度了"的事后响应；趋势提示是"分数在持续上升"
        的提前提醒。若此刻正在报警或人脸丢失，则不覆盖那两个更重要的状态。
        """
        if self._active or self._face_lost:
            return
        self._fade_in_line(
            "疲劳正在累积（评分持续上升 {:.2f}/分），建议休息".format(slope_per_min),
            theme.ORANGE, theme.ORANGE)

    def set_idle(self) -> None:
        self._active = False
        self._face_lost = False
        self._set_line("正常监测中", theme.GREEN, theme.GREEN_TEXT)
        if self._popup is not None:
            self._popup.hide()

    def apply_config(self, cfg: Dict) -> None:
        """运行时应用新的预警配置（「参数设置」面板调参后调用）。"""
        alarm_cfg = (cfg or {}).get("alarm", {})
        self._sound_enable = bool(alarm_cfg.get("sound_enable", True))
        self._popup_enable = bool(alarm_cfg.get("popup_enable", True))
        self._repeat_sec = float(alarm_cfg.get("repeat_sec", 5.0))
        if self._sound_enable and self._sound is None:
            wav = alarm_cfg.get("wav_path") or os.path.join(
                (cfg or {}).get("logging", {}).get("csv_dir", "fatigue_system/outputs"),
                "alarm.wav")
            self._sound = _AlarmSound(wav)
        elif not self._sound_enable:
            self._sound = None
        if not self._popup_enable and self._popup is not None:
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


# ------------------------------ ⑤ 操作控制区（顶部工具栏）----------------------

class ControlPanel(QFrame):
    """顶部工具栏（DESIGN.md §5.1）：对外只发信号，不直接操作检测状态。

    单行白卡片：应用名 | 视频源下拉（摄像头/打开文件/刷新合并） | 弹性空白 |
    运行状态圆点+fps | 校准/记录/设置 三个图标按钮 | 唯一实心"开始/停止监测"。
    原底部九按钮排删除；"关键点"开关已按 §5.2 移入视频区右下角。
    """

    open_camera_requested = pyqtSignal(int)
    open_file_requested = pyqtSignal(str)
    calibrate_requested = pyqtSignal()
    record_toggled = pyqtSignal(bool)
    history_requested = pyqtSignal()       # v1.11：历史会话回看
    settings_requested = pyqtSignal()
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, vcfg: Dict, parent=None):
        super().__init__(parent)
        self.setObjectName("header")
        self.setFixedHeight(88)
        self._vcfg = vcfg or {}
        self._running = False
        self._file_item_path: Optional[str] = None   # 最近打开的视频文件路径

        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 14, 18, 14)
        lay.setSpacing(18)

        title = QLabel("疲劳检测", self)
        title.setObjectName("appTitle")
        lay.addWidget(title)

        self._camera_combo = QComboBox(self)
        self._camera_combo.setMinimumWidth(190)
        self._camera_combo.activated.connect(self._on_source_activated)
        lay.addWidget(self._camera_combo)

        lay.addStretch(1)

        self._status = StatusDot(self)
        lay.addWidget(self._status)

        # 图标 + 文字标签（老师建议：纯图标第一眼看不出功能）
        self._btn_calib = IconButton("target", "开始校准（30s 个性化基线）", self,
                                     label="校准")
        self._btn_calib.clicked.connect(self.calibrate_requested.emit)
        lay.addWidget(self._btn_calib)
        self._btn_record = IconButton("record", "开始/停止记录（CSV）", self,
                                      checkable=True, label="记录")
        self._btn_record.toggled.connect(self._on_record_toggled)
        lay.addWidget(self._btn_record)
        self._btn_history = IconButton("history", "历史会话回看 / 导出报告", self,
                                       label="历史")
        self._btn_history.clicked.connect(self.history_requested.emit)
        lay.addWidget(self._btn_history)
        self._btn_settings = IconButton("gear", "参数设置", self, label="设置")
        self._btn_settings.clicked.connect(self.settings_requested.emit)
        lay.addWidget(self._btn_settings)

        # 唯一实心按钮：随运行状态切换 文字/颜色/行为（§1.5/§5.1）
        self._btn_main = QPushButton("开始监测", self)
        self._btn_main.setProperty("primary", True)
        self._btn_main.setCursor(Qt.PointingHandCursor)
        self._btn_main.clicked.connect(self._on_main_clicked)
        lay.addWidget(self._btn_main)

    # ------------------------------ 视频源下拉 --------------------------------

    def refresh_cameras(self) -> None:
        """重建下拉：摄像头列表 + 打开视频文件… + 刷新设备列表。"""
        current = self._camera_combo.currentData()
        self._camera_combo.blockSignals(True)
        self._camera_combo.clear()
        cameras = list_cameras()
        for cam in cameras:
            self._camera_combo.addItem(
                "摄像头 {}".format(cam["index"]), ("cam", cam["index"]))
        if not cameras:
            self._camera_combo.addItem("（未检测到摄像头）", ("none", None))
        if self._file_item_path:
            self._camera_combo.addItem(
                "文件：{}".format(os.path.basename(self._file_item_path)),
                ("file", self._file_item_path))
        self._camera_combo.addItem("打开视频文件…", ("pick_file", None))
        self._camera_combo.addItem("刷新设备列表", ("refresh", None))
        # 尽量恢复原选中项
        if current is not None:
            pos = self._camera_combo.findData(current)
            if pos >= 0:
                self._camera_combo.setCurrentIndex(pos)
        self._camera_combo.blockSignals(False)

    def _on_source_activated(self, pos: int) -> None:
        kind, value = self._camera_combo.itemData(pos) or ("none", None)
        if kind == "cam":
            self.open_camera_requested.emit(int(value))
        elif kind == "file":
            self.open_file_requested.emit(value)
        elif kind == "pick_file":
            self._pick_file()
        elif kind == "refresh":
            self.refresh_cameras()

    def _pick_file(self) -> None:
        start_dir = os.path.dirname(self._file_item_path) if self._file_item_path \
            else self._vcfg.get("last_dir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", start_dir,
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv);;所有文件 (*.*)")
        if path:
            self._file_item_path = path
            self.refresh_cameras()
            pos = self._camera_combo.findData(("file", path))
            if pos >= 0:
                self._camera_combo.setCurrentIndex(pos)
            self.open_file_requested.emit(path)
        else:
            self._restore_selection_to_source()

    def _restore_selection_to_source(self) -> None:
        """选了"打开文件…/刷新"这类动作项后，把显示恢复到最近的真实源。"""
        for i in range(self._camera_combo.count()):
            kind, _v = self._camera_combo.itemData(i) or ("none", None)
            if kind in ("cam", "file"):
                self._camera_combo.setCurrentIndex(i)
                return

    def camera_available(self) -> bool:
        kind, _v = self._camera_combo.itemData(0) or ("none", None)
        return kind == "cam"

    def select_camera(self, index: int) -> None:
        pos = self._camera_combo.findData(("cam", int(index)))
        if pos >= 0:
            self._camera_combo.setCurrentIndex(pos)

    def request_open_camera(self) -> None:
        kind, value = self._camera_combo.currentData() or ("none", None)
        if kind == "cam":
            self.open_camera_requested.emit(int(value))

    def current_source(self):
        """当前下拉选中的真实源：("cam", idx) / ("file", path) / None。"""
        kind, value = self._camera_combo.currentData() or ("none", None)
        return (kind, value) if kind in ("cam", "file") else None

    # ------------------------------ 状态同步 ----------------------------------

    def set_running(self, running: bool) -> None:
        """主按钮随运行状态切换：运行=红"停止监测"，停止=蓝"开始监测"。"""
        self._running = bool(running)
        self._btn_main.setText("停止监测" if self._running else "开始监测")
        self._btn_main.setProperty("danger", self._running)
        # 刷新 QSS 属性选择器（danger 变化后需重新抛光样式）
        self._btn_main.style().unpolish(self._btn_main)
        self._btn_main.style().polish(self._btn_main)

    def set_fps(self, text: str, dot_color: str) -> None:
        self._status.set_status(text, dot_color)

    def set_calibrate_enabled(self, enabled: bool) -> None:
        self._btn_calib.setEnabled(enabled)

    def set_recording(self, recording: bool) -> None:
        self._btn_record.blockSignals(True)
        self._btn_record.setChecked(recording)
        self._btn_record.blockSignals(False)

    def _on_record_toggled(self, checked: bool) -> None:
        self.record_toggled.emit(checked)

    def _on_main_clicked(self) -> None:
        if self._running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()


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
