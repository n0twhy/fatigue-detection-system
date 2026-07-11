# -*- coding: utf-8 -*-
"""统一视频源接口（开发规格书 §6.10）。

对上层（GUI / 主检测循环）屏蔽"摄像头"与"视频文件"的差异，统一提供：
    * list_cameras()           —— 枚举可用摄像头，供 GUI 下拉选择
    * VideoSource.open_camera  —— 打开指定索引摄像头
    * VideoSource.open_file    —— 打开本地视频文件
    * VideoSource.read         —— 读取一帧，返回 (ok, frame_bgr, ts)
    * VideoSource.fps          —— 当前源帧率
    * VideoSource.release      —— 释放资源

时间戳约定（供 M1+ 的滑窗/rPPG 使用）：
    * 摄像头：单调时钟（首帧归零），反映真实经过时间。
    * 视频文件：视频内时间（POS_MSEC），保证 30s 窗口=30s 视频内容，
      与实际回放快慢无关。
"""

import os

# 降低 OpenCV 在探测不存在的摄像头设备时打印的 V4L 警告噪声；
# 必须在 import cv2 之前设置。
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

import glob
import sys
import threading
import time
from typing import List, Dict, Optional, Tuple

import cv2

# 摄像头帧率无法从驱动读到时的兜底值（硬件回退，非可调阈值）
_FALLBACK_FPS = 30.0


class _CameraCaptureThread(threading.Thread):
    """摄像头后台采集线程（解决交接文档 §5.1 的 GUI 摄像头低帧率问题）。

    背景：usbip 转发进 WSL2 的摄像头，在"间歇式 read()"节奏下（GUI 的
    QTimer 每 ~50ms 调一次）单次 ``cap.read()`` 会阻塞 ~170ms，把 GUI 拖到
    ~5fps；而连续紧循环读取时同一相机可稳定输出 ~27fps。因此用本线程
    持续 drain 相机、只保留**最新一帧**，GUI 帧循环改经 ``read_latest()``
    取帧——帧率从此由处理能力决定，而非阻塞读取。

    线程安全：本线程只写、消费者只读；cv2 的 ``read()`` 每次返回新数组，
    故交付出去的帧不会被后续采集覆盖，消费者无需拷贝。

    输入: cap —— 已打开的 cv2.VideoCapture（本线程独占其 read()）。
    """

    def __init__(self, cap: "cv2.VideoCapture"):
        super().__init__(daemon=True, name="camera-capture")
        self._cap = cap
        self._cond = threading.Condition()
        self._frame = None            # 最新一帧（BGR numpy 数组）
        self._raw_ts: float = 0.0     # 该帧采集时刻（perf_counter 原始值，秒）
        self._seq: int = 0            # 帧序号；消费者据此判断"是否有新帧"
        self._failed = False          # 相机 read() 失败（断开/被占用）
        self._stopping = False

    def run(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
            ok, frame = self._cap.read()   # 阻塞读在此线程内消化，不影响 GUI
            now = time.perf_counter()
            with self._cond:
                if self._stopping:
                    return
                if not ok:
                    self._failed = True
                    self._cond.notify_all()
                    return
                self._frame = frame
                self._raw_ts = now
                self._seq += 1
                self._cond.notify_all()

    def read_latest(self, last_seq: int, timeout: float):
        """取比 last_seq 更新的最新帧；暂无新帧则最多等待 timeout 秒。

        参数:
            last_seq —— 消费者上次取到的帧序号（首次传 0）。
            timeout  —— 等新帧的最长秒数；超时视为相机异常。
        返回:
            (ok, frame_bgr, raw_ts, seq)
            ok=False 表示相机失败/线程停止/等待超时，此时 frame 为 None。
            raw_ts 为该帧采集时刻的 perf_counter 原始值（由调用方换算成
            首帧归零的时间戳，供滑窗/rPPG 使用，比消费时刻更准确）。
        """
        deadline = time.perf_counter() + timeout
        with self._cond:
            while self._seq <= last_seq and not self._failed and not self._stopping:
                remain = deadline - time.perf_counter()
                if remain <= 0:
                    break
                self._cond.wait(remain)
            if self._failed or self._stopping or self._seq <= last_seq:
                return False, None, 0.0, last_seq
            return True, self._frame, self._raw_ts, self._seq

    def stop(self) -> None:
        """请求线程退出。调用方须先 stop()+join() 再 cap.release()，
        避免采集线程还在 read() 时底层句柄被释放导致崩溃。"""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()


def _read_linux_camera_name(index: int) -> Optional[str]:
    """在 Linux 下尝试从 sysfs 读取摄像头设备名。

    参数:
        index —— 摄像头索引（约定与 /dev/video{index} 对应）。
    返回:
        设备名字符串；读不到时返回 None。
    """
    sys_path = "/sys/class/video4linux/video{}/name".format(index)
    try:
        with open(sys_path, "r", encoding="utf-8", errors="ignore") as f:
            name = f.read().strip()
            return name or None
    except OSError:
        return None


def _candidate_camera_indices(max_index: int) -> List[int]:
    """给出待探测的摄像头索引列表。

    Linux/WSL2：严格以真实存在的 /dev/video* 设备节点为准；无设备节点即
    判定为无摄像头，直接返回空列表（避免盲探 0..N 触发 V4L2 警告与卡顿）。
    其他平台（Windows/macOS）：无此类设备节点概念，按 0..max_index-1 逐个探测。
    """
    if sys.platform.startswith("linux"):
        indices = []
        for node in glob.glob("/dev/video*"):
            suffix = node.replace("/dev/video", "")
            if suffix.isdigit():
                indices.append(int(suffix))
        return sorted(set(indices))
    return list(range(max_index))


def list_cameras(max_index: int = 5) -> List[Dict]:
    """枚举当前机器上可用的摄像头。

    参数:
        max_index —— 通用回退方式下探测的最大索引数（探测 0..max_index-1）。
    返回:
        形如 [{'index': 0, 'name': 'HD WebCam'}, ...] 的列表，供 GUI 下拉框使用；
        无可用摄像头时返回空列表（例如 WSL2 默认无摄像头设备）。
    """
    cameras: List[Dict] = []
    for idx in _candidate_camera_indices(max_index):
        cap = None
        try:
            cap = cv2.VideoCapture(idx)
            if cap is not None and cap.isOpened():
                name = _read_linux_camera_name(idx) or "摄像头 {}".format(idx)
                cameras.append({"index": idx, "name": name})
        except Exception:
            # 探测单个设备失败不应影响整体枚举
            pass
        finally:
            if cap is not None:
                cap.release()
    return cameras


class VideoSource:
    """摄像头 / 视频文件的统一读取封装。

    构造参数:
        cfg —— 可选，config.yaml 中的 ``video`` 子配置字典；用于设置摄像头
               分辨率/目标帧率，以及提供帧率兜底值。
    """

    def __init__(self, cfg: Optional[Dict] = None):
        self._cfg = cfg or {}
        self._cap: Optional[cv2.VideoCapture] = None
        self._kind: Optional[str] = None      # 'camera' | 'file' | None
        self._source_desc: str = ""           # 当前源的人类可读描述
        self._file_fps: Optional[float] = None
        self._t0: Optional[float] = None       # 摄像头时间基准（首帧时刻）
        self._frame_idx: int = 0
        # 摄像头采集线程相关（仅 camera 源使用；见 _CameraCaptureThread 注释）
        self._capture_thread: Optional[_CameraCaptureThread] = None
        self._last_seq: int = 0                # 已消费的最新帧序号
        self._cam_fps: Optional[float] = None  # 打开时缓存的驱动上报帧率
        self._cam_size: Tuple[int, int] = (0, 0)  # 打开时缓存的画面尺寸
        # 视频文件是否循环播放；M0 预览设为 True 便于持续观察，
        # M5/M6 批量回放时应设为 False 以保证单遍处理。
        self.loop: bool = False

    # ---------------------------- 打开 / 释放 --------------------------------

    def open_camera(self, index: int) -> bool:
        """打开指定索引的摄像头。

        返回是否成功；失败时不改变已有状态之外仅确保资源已释放。
        """
        self.release()
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            return False
        # 采集编码：MJPG 压缩流带宽低，usbip 转发摄像头(WSL2)必须用它，
        # 否则默认 YUYV 未压缩流带宽过大导致读帧 select 超时。须在设分辨率前设定。
        fourcc = self._cfg.get("camera_fourcc", "MJPG")
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*str(fourcc)))
        # 尽力按配置设置采集参数（部分摄像头可能忽略）
        if "frame_width" in self._cfg:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self._cfg["frame_width"]))
        if "frame_height" in self._cfg:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self._cfg["frame_height"]))
        # 刻意**不**向相机请求帧率（不 set CAP_PROP_FPS）：实测在 usbip 摄像头上
        # 请求 20fps 会协商出实际仅 ~6fps 的降级模式（这正是 GUI 曾只有 ~5fps 的
        # 根因），而保持驱动默认 30fps 模式可得当前光照下的最高供帧率。
        # target_fps 只作为 GUI 帧循环节拍，与相机模式无关。
        self._cap = cap
        self._kind = "camera"
        self._source_desc = _read_linux_camera_name(index) or "摄像头 {}".format(index)
        self._t0 = None
        self._frame_idx = 0
        self._last_seq = 0
        # 采集线程启动后 cap 归其独占，其他线程不再调用 cap.get()
        # （cv2.VideoCapture 非线程安全），故在此一次性缓存尺寸/帧率。
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._cam_size = (w, h)
        reported = cap.get(cv2.CAP_PROP_FPS)
        self._cam_fps = reported if reported and reported > 0 else None
        if bool(self._cfg.get("camera_capture_thread", True)):
            self._capture_thread = _CameraCaptureThread(cap)
            self._capture_thread.start()
        return True

    def open_file(self, path: str) -> bool:
        """打开本地视频文件。

        返回是否成功；路径不存在或无法解码时返回 False。
        """
        if not path or not os.path.isfile(path):
            return False
        self.release()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        self._kind = "file"
        fps = cap.get(cv2.CAP_PROP_FPS)
        self._file_fps = fps if fps and fps > 0 else None
        self._source_desc = os.path.basename(path)
        self._t0 = None
        self._frame_idx = 0
        return True

    def release(self) -> None:
        """释放底层 VideoCapture 资源（先停采集线程，再释放句柄）。"""
        if self._capture_thread is not None:
            timeout = float(self._cfg.get("camera_read_timeout_sec", 2.0))
            self._capture_thread.stop()
            self._capture_thread.join(timeout=timeout)
            self._capture_thread = None
        if self._cap is not None:
            self._cap.release()
        self._cap = None
        self._kind = None
        self._source_desc = ""
        self._file_fps = None
        self._t0 = None
        self._frame_idx = 0
        self._last_seq = 0
        self._cam_fps = None
        self._cam_size = (0, 0)

    # ------------------------------- 读取 ------------------------------------

    def read(self) -> Tuple[bool, Optional["cv2.Mat"], float]:
        """读取一帧。

        返回:
            (ok, frame_bgr, ts)
            ok        —— 是否成功读到一帧。
            frame_bgr —— OpenCV BGR 图像（numpy 数组）；失败时为 None。
            ts        —— 时间戳（秒），见模块级"时间戳约定"。
        """
        if self._cap is None:
            return False, None, 0.0

        # 摄像头 + 采集线程：只取"最新帧"，阻塞读已在采集线程内消化。
        # 若相机产帧慢于本调用节奏，会等到下一新帧（不重复交付旧帧，
        # 避免同一帧被滑窗统计两次）；超时视为相机异常。
        if self._kind == "camera" and self._capture_thread is not None:
            timeout = float(self._cfg.get("camera_read_timeout_sec", 2.0))
            ok, frame, raw_ts, seq = self._capture_thread.read_latest(self._last_seq, timeout)
            if not ok:
                return False, None, self._current_ts()
            self._last_seq = seq
            if self._t0 is None:
                self._t0 = raw_ts       # 首帧归零；ts 用采集时刻，比消费时刻准确
            self._frame_idx += 1
            return True, frame, raw_ts - self._t0

        ok, frame = self._cap.read()

        # 视频文件读到末尾：按需循环
        if not ok and self._kind == "file":
            if self.loop:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
            if not ok:
                return False, None, self._current_ts()

        if not ok:
            return False, None, self._current_ts()

        ts = self._current_ts()
        self._frame_idx += 1
        return True, frame, ts

    def _current_ts(self) -> float:
        """根据源类型计算当前时间戳（秒）。"""
        if self._kind == "camera":
            now = time.perf_counter()
            if self._t0 is None:
                self._t0 = now
            return now - self._t0
        if self._kind == "file" and self._cap is not None:
            msec = self._cap.get(cv2.CAP_PROP_POS_MSEC)
            if msec and msec > 0:
                return msec / 1000.0
            # POS_MSEC 不可用时按帧序号 / 帧率推算
            return self._frame_idx / self.fps
        return 0.0

    # ------------------------------ 属性查询 ---------------------------------

    @property
    def fps(self) -> float:
        """当前源帧率（帧/秒）。

        文件用其自带帧率；摄像头优先用驱动上报值；均不可用时回退到
        配置的 target_fps 或硬件兜底值。
        """
        fallback = float(self._cfg.get("target_fps", _FALLBACK_FPS))
        if self._kind == "file":
            return self._file_fps if self._file_fps else fallback
        if self._kind == "camera":
            # 用打开时缓存的值：采集线程启动后不可再跨线程调用 cap.get()
            return self._cam_fps if self._cam_fps else fallback
        return fallback

    @property
    def kind(self) -> Optional[str]:
        """当前源类型：'camera' | 'file' | None。"""
        return self._kind

    @property
    def source_desc(self) -> str:
        """当前源的人类可读描述（摄像头名或文件名）。"""
        return self._source_desc

    @property
    def frame_size(self) -> Tuple[int, int]:
        """当前源画面尺寸 (宽, 高)；未打开时返回 (0, 0)。"""
        if self._cap is None:
            return 0, 0
        if self._kind == "camera":
            return self._cam_size    # 打开时缓存，避免跨线程 cap.get()
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return w, h

    def is_opened(self) -> bool:
        """当前是否已成功打开某个视频源。"""
        return self._cap is not None and self._cap.isOpened()
