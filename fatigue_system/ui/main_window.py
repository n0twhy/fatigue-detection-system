# -*- coding: utf-8 -*-
"""主窗口（开发规格书 §6.12：六区完整 GUI）。

里程碑进度：
    M0 视频源显示/切换；
    M1 逐帧特征（FaceMesh→EAR/MAR/头姿）+ 关键点叠加 + HUD；
    M2 滑窗聚合 + 30s 个性化基线校准；
    M3 多特征加权融合 + 四级判定 + 防误报报警（声音/弹窗）+ CSV 记录，
       六区布局完整（基础任务完成点）；
    M4 实时 rPPG 辅线（滚动缓冲 POS → HR/HRV）：每帧喂 ROI，估计按
       rppg.update_interval_sec 节拍；HR 汇入窗口特征/基线校准/生理子分。

六区分布（§6.12）：
    ┌───────────────┬────────────┐
    │ ① 视频显示区   │ ③ 疲劳等级 │
    │ (VideoWidget) │ ④ 预警提示 │
    │               │ ⑥ 数据记录 │
    ├───────────────┴────────────┤
    │ ② 特征参数区（三行）        │
    │ ⑤ 操作控制区（按钮行）      │
    └────────────────────────────┘

帧循环由 QTimer 驱动（摄像头按 target_fps 节拍取最新帧；文件按源帧率）。
融合评分/报警状态机按 fusion.update_interval_sec 节拍（按 ts 判定，
不按帧数——见交接文档 §4.12），CSV 落盘与⑥表格按 logging.log_interval_sec。
"""

from collections import deque
from time import perf_counter
from typing import Dict, Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QVBoxLayout, QWidget,
)

from fatigue_system.ui import theme
from fatigue_system.io.video_source import VideoSource
from fatigue_system.io.data_logger import DataLogger
from fatigue_system.core.face_mesh import FaceMeshDetector
from fatigue_system.core.eye_features import compute_ear
from fatigue_system.core.mouth_features import compute_mar
from fatigue_system.core.head_pose import estimate_head_pose, classify_head_state
from fatigue_system.core.feature_window import FeatureAggregator
from fatigue_system.core.calibration import BaselineCalibrator
from fatigue_system.core.rppg_realtime import RealtimeRPPG
from fatigue_system.core import fusion
from fatigue_system.core.trend import TrendMonitor
from fatigue_system.core.types import FrameFeatures, Baseline
from fatigue_system.ui.video_widget import VideoWidget, draw_landmarks, draw_hud
from fatigue_system.ui.panels import (
    AlarmPanel, ControlPanel, LevelPanel,
)
from fatigue_system.ui.plot_widget import MonitorPanel

# 兼容旧引用（dev_tools/verify_gui_stages.py 从本模块导入绘制函数）
_draw_landmarks = draw_landmarks
_draw_hud = draw_hud


class MainWindow(QMainWindow):
    """疲劳检测主窗口（M3：六区 + 融合 + 预警 + 记录）。"""

    def __init__(self, config: Dict):
        super().__init__()
        self._config = config or {}
        self._vcfg = self._config.get("video", {})
        self._fusion_interval = float(
            self._config.get("fusion", {}).get("update_interval_sec", 1.0))

        self._source = VideoSource(self._vcfg)
        self._detector = FaceMeshDetector(self._config)
        self._logger = DataLogger(self._config)
        self._fsm = fusion.AlarmFSM(self._config)
        self._trend = TrendMonitor(self._config)   # v1.11：疲劳上升趋势预警
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._tick_times = deque(maxlen=30)
        self._show_landmarks = True
        self._last_features: Optional[FrameFeatures] = None
        self._last_result = None                 # 最近一次融合结果（按节拍更新）
        self._last_fusion_ts: Optional[float] = None
        # 人脸持续丢失（趴睡/离开画面）检测
        self._face_lost_since: Optional[float] = None
        self._face_lost_sec = float(
            self._config.get("alarm", {}).get("face_lost_sec", 3.0))
        # 自动静息心率：未做（含HR的）校准时，用运行中的心率中位数作静息参照，
        # 让生理子分无需完整校准也能激活（否则一直显示 "-"）。
        self._hr_rest_samples: deque = deque(maxlen=180)
        rp_cfg = self._config.get("rppg", {})
        self._hr_rest_min_samples = int(rp_cfg.get("auto_rest_min_samples", 15))

        # M2：滑窗聚合 + 基线校准状态
        self._aggregator: Optional[FeatureAggregator] = None
        self._calibrator: Optional[BaselineCalibrator] = None
        self._calibrating = False
        self._baseline = None
        self._baseline_note = "基线未校准"   # §5.5：基线状态并入底部状态行

        # M4：实时 rPPG（辅线，rppg.enable=false 时保持 None → 退化为基础模型）
        self._rppg: Optional[RealtimeRPPG] = None
        self._last_hr: Optional[float] = None
        self._last_hrv: Optional[float] = None

        # 退出清理：closeEvent 只覆盖"用户关窗"，而 app.quit()（如 --selftest）
        # 不触发 closeEvent；若进程退出时摄像头采集线程仍阻塞在 cap.read()，
        # 会触发 pthread 级崩溃。故清理做成幂等 _cleanup() 并同时挂到 aboutToQuit。
        self._cleaned = False
        QApplication.instance().aboutToQuit.connect(self._cleanup)

        self._build_ui()
        self._control.refresh_cameras()
        self._auto_start()

    # ------------------------------- 界面搭建 --------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("疲劳检测")
        # 默认接近方形（样图比例）；视频区可缩放故允许缩到较小窗口
        self.resize(1280, 1120)
        self.setMinimumSize(1000, 860)

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(theme.GAP)

        # 顶部工具栏（§5.1）：应用名/视频源下拉/状态/图标按钮/唯一实心主按钮
        self._control = ControlPanel(self._vcfg, self)
        self._control.open_camera_requested.connect(self._on_open_camera)
        self._control.open_file_requested.connect(self._on_open_file)
        self._control.calibrate_requested.connect(self._on_start_calibration)
        self._control.record_toggled.connect(self._on_record_toggled)
        self._control.history_requested.connect(self._open_history)
        self._control.settings_requested.connect(self._open_settings)
        self._control.start_requested.connect(self._on_start)
        self._control.stop_requested.connect(self._on_stop)
        root.addWidget(self._control)

        # 中部：左=视频，右=等级 / 预警
        upper = QHBoxLayout()
        upper.setSpacing(theme.GAP)
        self._video = VideoWidget(self)
        # 关键点开关在视频区右下角（§5.2）
        self._video.landmarks_toggled.connect(self._on_toggle_landmarks)
        upper.addWidget(self._video, stretch=3)
        right = QVBoxLayout()
        right.setSpacing(theme.GAP)
        # 等级卡片占满剩余高度，报警状态单行（§5.3）
        self._level_panel = LevelPanel(self._config, self)
        right.addWidget(self._level_panel, stretch=1)
        self._alarm_panel = AlarmPanel(self._config, self)
        right.addWidget(self._alarm_panel)
        right_box = QWidget(self)
        right_box.setLayout(right)
        right_box.setMinimumWidth(400)
        right_box.setMaximumWidth(480)
        upper.addWidget(right_box, stretch=1)
        root.addLayout(upper, stretch=11)

        # 指标监测区（§5.4：左曲线卡 + 右指标列表卡）；上下 55:45（样图比例）
        self._monitor = MonitorPanel(self._config, self)
        root.addWidget(self._monitor, stretch=9)

        # 底部细状态行
        self._status_label = QLabel("未打开视频源", self)
        self._status_label.setStyleSheet(
            "color:{}; font-size:12px; padding:2px;".format(theme.TEXT_MUTE))
        root.addWidget(self._status_label)

        self.setCentralWidget(central)

    def warmup(self) -> None:
        """预热重型组件（启动加载阶段调用，见 app.main 的「载入中」窗口）。

        mediapipe 首次推理会现场建计算图（实测卡好几秒）。不预热的话，这份开销会
        落在用户点「开始监测」的那一刻，界面直接"未响应"。
        """
        if self._detector is not None:
            self._detector.warmup()

    def _busy(self, message: str) -> None:
        """打开视频源前的即时反馈：先把提示画出来，再去做可能阻塞的事。

        打开摄像头/解码首帧可能要一两秒（USB 摄像头协商更久），期间事件循环被占住，
        窗口会显示"未响应"。先 show_message + processEvents，用户至少知道在干活。
        """
        self._video.show_message(message)
        self._control.set_fps("正在打开…", theme.TEXT_MUTE)
        QApplication.processEvents()

    def _auto_start(self) -> None:
        default_source = str(self._vcfg.get("default_source", "camera")).lower()
        if default_source == "camera" and self._control.camera_available():
            self._control.select_camera(self._vcfg.get("camera_index", 0))
            self._control.request_open_camera()

    # ------------------------------- 事件响应 --------------------------------

    def _on_start(self) -> None:
        """主按钮"开始监测"：打开下拉当前选中的源（摄像头或最近的视频文件）。"""
        src = self._control.current_source()
        if src is None:
            self._warn("没有可用的视频源。请连接摄像头，或在下拉中选择「打开视频文件…」。")
            return
        kind, value = src
        if kind == "cam":
            self._on_open_camera(int(value))
        else:
            self._on_open_file(value)

    def _on_open_camera(self, index: int) -> None:
        self._busy("正在打开摄像头…")
        if self._source.open_camera(int(index)):
            self._start_loop()
        else:
            self._video.show_message("无视频源\n\n请选择摄像头，或打开视频文件")
            self._control.set_fps("已停止", theme.TEXT_MUTE)
            self._warn("打开摄像头 [{}] 失败。设备可能被占用或不可用。".format(index))

    def _on_open_file(self, path: str) -> None:
        # 默认播完自动停止（不循环），便于单遍回放测试（组员反馈）；config 可开循环
        self._busy("正在打开视频…")
        self._source.loop = bool(self._vcfg.get("loop_file", False))
        if self._source.open_file(path):
            self._start_loop()
        else:
            self._video.show_message("无视频源\n\n请选择摄像头，或打开视频文件")
            self._control.set_fps("已停止", theme.TEXT_MUTE)
            self._warn("无法打开视频文件：\n{}\n\n请确认文件编码受 OpenCV 支持。".format(path))

    def _open_settings(self) -> None:
        """「参数设置」覆盖层（M6 拓展，§7.7）：应用后对运行中的组件热更新。"""
        from fatigue_system.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._config, self)
        dlg.applied.connect(self._apply_runtime_config)
        dlg.open_over()

    def _open_history(self) -> None:
        """「历史」覆盖层（v1.11 功能②）：回看历次会话、导出 HTML 报告。

        只读 outputs/ 里的 CSV，不占摄像头、不打断当前监测。
        """
        from fatigue_system.ui.history_dialog import HistoryDialog
        dlg = HistoryDialog(self._config, self)
        dlg.open_over()

    def _apply_runtime_config(self) -> None:
        """把（被参数设置面板改过的）共享配置热应用到运行中的组件。

        不清滑窗统计、不丢报警状态、不重启视频源——调参不打断监测。
        涉及在构造时缓存过配置值的所有对象。
        """
        self._fusion_interval = float(
            self._config.get("fusion", {}).get("update_interval_sec", 1.0))
        self._face_lost_sec = float(
            self._config.get("alarm", {}).get("face_lost_sec", 3.0))
        self._fsm.reconfigure(self._config)
        self._trend.reconfigure(self._config)
        if self._aggregator is not None:
            self._aggregator.reconfigure(self._config)
        self._alarm_panel.apply_config(self._config)
        self._vcfg = self._config.get("video", {})
        # 视频文件循环开关对当前已打开的文件源立即生效（摄像头源不读该属性）
        self._source.loop = bool(self._vcfg.get("loop_file", False))

    def _on_stop(self) -> None:
        self._timer.stop()
        self._source.release()
        self._stop_recording_if_active()
        self._tick_times.clear()
        self._last_features = None
        self._last_result = None
        self._last_fusion_ts = None
        self._face_lost_since = None
        self._hr_rest_samples.clear()
        self._aggregator = None
        self._calibrating = False
        self._rppg = None
        self._last_hr = None
        self._last_hrv = None
        self._fsm.reset()
        self._trend.reset()
        self._control.set_calibrate_enabled(True)
        self._control.set_running(False)
        self._control.set_fps("已停止", theme.TEXT_MUTE)
        self._video.show_message("已停止\n\n请选择摄像头或打开视频文件")
        self._status_label.setText("已停止")
        self._monitor.reset()
        self._level_panel.set_idle()
        self._alarm_panel.set_idle()

    def _on_toggle_landmarks(self, checked: bool) -> None:
        self._show_landmarks = bool(checked)

    def _on_start_calibration(self) -> None:
        if not self._source.is_opened():
            self._warn("请先打开摄像头或视频文件，再开始基线校准。")
            return
        self._calibrator = BaselineCalibrator(self._config)
        self._calibrating = True
        self._control.set_calibrate_enabled(False)
        dur = self._config.get("calibration", {}).get("duration_sec", 30)
        self._baseline_note = "校准中 0%（约 {}s，请保持清醒睁眼、正对镜头）".format(dur)

    def _on_record_toggled(self, recording: bool) -> None:
        if recording:
            if not self._source.is_opened():
                self._warn("请先打开摄像头或视频文件，再开始记录。")
                self._control.set_recording(False)
                return
            self._logger.start()
        else:
            self._stop_recording_if_active()

    def _stop_recording_if_active(self) -> None:
        """停止记录：落盘 CSV + 会话汇总，并自动生成 HTML 会话报告（v1.11 功能①）。"""
        if not self._logger.active:
            return
        csv_path = self._logger.csv_path
        summary = self._logger.stop(avg_fps=self._measured_fps())
        self._control.set_recording(False)
        report = ""
        try:
            from fatigue_system.io.session_report import build_report
            path = build_report(csv_path, cfg=self._config,
                                baseline_note=self._baseline_note)
            report = "\n\n会话报告（可直接双击打开）：\n{}".format(path)
        except Exception as exc:     # 报告只是增值项，失败不影响 CSV 已落盘
            report = "\n\n（会话报告生成失败：{}）".format(exc)
        self._warn("检测记录已保存：\n{}\n\n会话汇总：\n{}{}".format(
            csv_path, summary, report))

    # ------------------------------- 帧循环 ----------------------------------

    def _start_loop(self) -> None:
        """启动帧循环：文件按源帧率、摄像头按 target_fps；重建聚合器与状态机。"""
        self._tick_times.clear()
        target_fps = float(self._vcfg.get("target_fps", 20))
        if self._source.kind == "file":
            src_fps = self._source.fps
            use_fps = src_fps if src_fps and src_fps > 0 else target_fps
        else:
            use_fps = target_fps
        # 切换视频源：取消进行中的校准；新建聚合器（沿用已有基线）；状态机复位
        self._calibrating = False
        self._control.set_calibrate_enabled(True)
        self._aggregator = FeatureAggregator(self._config, self._source.fps)
        if self._baseline is not None and getattr(self._baseline, "valid", False):
            self._aggregator.set_baseline(self._baseline)
        self._fsm.reset()
        self._trend.reset()          # v1.11：换源即清趋势缓冲（时间戳基准变了）
        self._last_result = None
        self._last_fusion_ts = None
        self._face_lost_since = None
        self._hr_rest_samples.clear()
        # M4：换源即新建 rPPG 估计器（时间戳基准变了，旧缓冲必须作废）
        if bool(self._config.get("rppg", {}).get("enable", True)):
            self._rppg = RealtimeRPPG(self._source.fps, self._config)
        else:
            self._rppg = None
        self._last_hr = None
        self._last_hrv = None
        interval = max(1, int(round(1000.0 / use_fps)))
        self._control.set_running(True)
        self._timer.start(interval)

    def _on_tick(self) -> None:
        ok, frame, ts = self._source.read()
        if not ok or frame is None:
            self._timer.stop()
            self._stop_recording_if_active()
            self._control.set_running(False)
            self._control.set_fps("已结束", theme.TEXT_MUTE)
            self._status_label.setText("状态：视频源已结束或读取失败")
            self._video.show_message("▶ 视频播放结束")
            return
        self._tick_times.append(perf_counter())
        annotated, ff = self._process_and_annotate(frame, ts)
        self._last_features = ff

        # M4：实时 rPPG 辅线——每帧喂 ROI，估计按 rppg.update_interval_sec
        # 节拍在 estimate() 内部缓存，帧循环高频调用无压力
        if self._rppg is not None:
            if ff.roi_rgb is not None:
                self._rppg.update(ff.roi_rgb, ts)
            self._last_hr, self._last_hrv = self._rppg.estimate()

        wf = None
        if self._aggregator is not None:
            # M2：滑窗聚合 + 校准
            self._aggregator.push(ff)
            if self._calibrating and self._calibrator is not None:
                self._calibrator.push(ff, hr=self._last_hr)   # M4：静息 HR 入基线
                if self._calibrator.is_done():
                    self._finish_calibration()
                else:
                    self._baseline_note = "校准中 {:.0f}%（请保持清醒睁眼、正对镜头）".format(
                        self._calibrator.progress() * 100)
            wf = self._aggregator.result()
            wf.hr = self._last_hr       # M4：HR/HRV 汇入窗口特征（缺失即 None）
            wf.hrv = self._last_hrv

            # 人脸持续丢失检测（组员反馈#8：趴睡面部离开画面采集不到）
            if ff.face_found:
                self._face_lost_since = None
            elif self._face_lost_since is None:
                self._face_lost_since = ts
            face_lost = (self._face_lost_since is not None
                         and ts - self._face_lost_since >= self._face_lost_sec)

            # M3：融合评分 + 报警（按 update_interval_sec 节拍，按 ts 判定）
            if (self._last_fusion_ts is None
                    or ts - self._last_fusion_ts >= self._fusion_interval):
                self._last_fusion_ts = ts
                if self._last_hr is not None:
                    self._hr_rest_samples.append(self._last_hr)
                self._last_result = fusion.evaluate(
                    wf, self._physio_baseline(), self._config, self._fsm)
                self._level_panel.set_result(self._last_result)
                # 人脸丢失优先提示（并报警），否则走正常融合报警
                if face_lost:
                    self._alarm_panel.set_face_lost(True)
                else:
                    self._alarm_panel.set_face_lost(False)
                    self._alarm_panel.update_alarm(
                        self._last_result.alarm, self._last_result.level_name)
                    # v1.11 功能③：疲劳上升趋势 → 温和提示（不响铃、不计入报警）
                    if self._trend.update(ts, self._last_result.score):
                        self._alarm_panel.show_trend_hint(self._trend.slope_per_min)

        # M3：CSV 记录（每帧喂入统计，写行节拍由 logger 内部控制）
        if wf is not None and self._last_result is not None and self._logger.active:
            self._logger.log(ff, wf, self._last_result)

        self._video.show_frame(annotated)
        self._update_status(ts)
        head_state = classify_head_state(
            ff.pitch, ff.yaw, ff.roll, self._baseline, self._config) if ff.face_found else ""
        # 视频区浮层：人脸状态胶囊 + EAR/MAR/姿态 数值胶囊（§5.2）
        self._video.set_overlay(ff, head_state)
        # 指标监测：每帧刷新曲线与指标列表
        self._monitor.append(ff, wf, self._last_result, head_state)

    def _finish_calibration(self) -> None:
        self._baseline = self._calibrator.finalize()
        self._calibrating = False
        self._control.set_calibrate_enabled(True)
        b = self._baseline
        if b.valid:
            self._aggregator.set_baseline(b)
            self._baseline_note = "基线已校准 · 闭眼阈 {:.3f}".format(
                self._aggregator.ear_closed_thresh)
            # 详细基线数据放状态行悬停提示（做减法，§1.1）
            hr_text = " | 静息HR {:.0f}".format(b.hr_rest) if b.hr_rest else ""
            self._status_label.setToolTip(
                "睁眼EAR {:.3f}±{:.3f} | 闭口MAR {:.3f} | 头中性(俯{:+.1f} 偏{:+.1f} 翻{:+.1f}){hr}".format(
                    b.ear_open_mean, b.ear_open_std, b.mar_closed_mean,
                    b.pitch, b.yaw, b.roll, hr=hr_text))
        else:
            self._baseline_note = "基线样本不足 · 仍用默认阈值（可重试校准）"

    # --------------------------- 逐帧检测 + 叠加 -----------------------------

    def _process_and_annotate(self, frame_bgr, ts: float):
        landmarks_px, roi_rgb = self._detector.process(frame_bgr)
        vis = frame_bgr.copy()
        if landmarks_px is not None:
            ear, left_ear, right_ear = compute_ear(landmarks_px)
            mar = compute_mar(landmarks_px)
            pitch, yaw, roll = estimate_head_pose(landmarks_px, frame_bgr.shape)
            ff = FrameFeatures(ts=ts, face_found=True, ear=ear, left_ear=left_ear,
                               right_ear=right_ear, mar=mar, pitch=pitch, yaw=yaw,
                               roll=roll, roi_rgb=roi_rgb)
            if self._show_landmarks:
                draw_landmarks(vis, landmarks_px)
        else:
            ff = FrameFeatures(ts=ts, face_found=False, roi_rgb=None)
        # 旧 ASCII 调试框已废弃（§5.2）：数值改由视频区角落的 Qt 胶囊浮层显示
        return vis, ff

    # ------------------------------- 状态显示 --------------------------------

    def _measured_fps(self) -> float:
        if len(self._tick_times) < 2:
            return 0.0
        span = self._tick_times[-1] - self._tick_times[0]
        return (len(self._tick_times) - 1) / span if span > 0 else 0.0

    def _physio_baseline(self):
        """供融合用的静息心率参照：优先校准值，否则用运行心率中位数自动兜底。

        生理子分需要"本人静息心率"作参照。以前只能靠校准得到——没校准(含HR)
        就一直没有、physio 显示 "-"。这里在缺失时用监测过程中累积的心率中位数
        自动估一个静息参照，让 physio 无需完整校准也能激活。
        """
        b = self._baseline
        if b is not None and getattr(b, "valid", False) and getattr(b, "hr_rest", None):
            return b        # 已有校准静息心率，最准，直接用
        if len(self._hr_rest_samples) >= self._hr_rest_min_samples:
            s = sorted(self._hr_rest_samples)
            median = s[len(s) // 2]
            return Baseline(valid=True, hr_rest=float(median))
        return b            # 心率样本还不够，physio 暂为 None（显示 "-"）

    def _update_status(self, ts: float) -> None:
        w, h = self._source.frame_size
        mfps = self._measured_fps()
        # 工具栏运行状态：绿点 + fps（记录中由记录图标的选中态表达）
        self._control.set_fps("{:.0f} fps".format(mfps), theme.GREEN)
        # 底部状态行（§5.5 格式：源 · 分辨率 · fps · 已运行 · 基线状态）
        minutes, seconds = divmod(int(ts), 60)
        self._status_label.setText(
            "{desc} · {w}×{h} · {mfps:.0f} fps · 已运行 {m} 分 {s} 秒 · {base}{rec}".format(
                desc=self._source.source_desc, w=w, h=h, mfps=mfps,
                m=minutes, s=seconds, base=self._baseline_note,
                rec=" · 记录中" if self._logger.active else ""))

    # ------------------------------- 辅助方法 --------------------------------

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "提示", message)

    def _cleanup(self) -> None:
        """停止帧循环并释放视频源/检测器/记录器。

        幂等：closeEvent 与 aboutToQuit 可能先后各调一次，只执行一遍。
        """
        if self._cleaned:
            return
        self._cleaned = True
        self._timer.stop()
        self._stop_recording_if_active()
        self._source.release()
        if self._detector is not None:
            self._detector.close()

    def closeEvent(self, event) -> None:
        self._cleanup()
        super().closeEvent(event)
