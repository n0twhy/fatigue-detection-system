# -*- coding: utf-8 -*-
"""「参数设置」覆盖层（DESIGN.md §7.7/§8；内容为 2026-07-14 用户定稿 6 项）。

设计要点：
  * 不再用独立 QDialog，改为**主窗口内部覆盖层**：半透明压暗层铺满主窗口，
    白色圆角面板居中浮起；打开=淡入+0.95→1.00 微缩放浮现（220ms），
    关闭=淡出+缩回 0.97（150ms，退场更干脆）；点压暗区/Esc=取消（§7.7）。
  * 内容 6 项（用户定稿，覆盖 DESIGN.md §8 的"判定参数"组）：
    疲劳阈值(轻/中/重) + 报警(报警声音/报警弹窗/视频循环播放，Switch 开关)。
    其余参数仍在 config.yaml，含义见 参数设置说明.md。
  * 「完成」= 应用（热更新，不清统计不打断监测）并写回 config.yaml：
    **逐行只替换目标键的值**，保留全部中文注释与排版（PyYAML round-trip
    会丢注释，故用行级替换）；exe 内置配置只读时自动改存 exe 旁。
"""

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QDoubleSpinBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from fatigue_system.ui import theme
from fatigue_system.ui.anim import EASE_IN, animate
from fatigue_system.ui.widgets import IconButton, Switch

# 可调项清单（用户定稿 6 项）：(界面标签, 配置点路径, 类型, 最小, 最大, 步长)
# bool 型省略数值范围。范围给的是"安全可用区"，防止误设出病态值。
_FIELDS = [
    ("轻度",       ("fusion", "level_thresholds", "mild"),     "float", 0.05, 0.95, 0.05),
    ("中度",       ("fusion", "level_thresholds", "moderate"), "float", 0.05, 0.95, 0.05),
    ("重度",       ("fusion", "level_thresholds", "severe"),   "float", 0.05, 0.95, 0.05),
    ("报警声音",   ("alarm", "sound_enable"),                  "bool"),
    ("报警弹窗",   ("alarm", "popup_enable"),                  "bool"),
    ("视频循环播放", ("video", "loop_file"),                    "bool"),
]

# 面板分组（§8：参数装入圆角容器，组上方灰色小标签）
_GROUPS = [
    ("疲劳阈值", [0, 1, 2]),
    ("报警", [3, 4, 5]),
]

_PANEL_W = 380          # 面板宽（§7.7 约 340，按 §0.2 样图尺度校准放大）


def _cfg_get(cfg: Dict, path: Tuple[str, ...], default=None):
    """按点路径从嵌套字典取值。"""
    node = cfg or {}
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _cfg_set(cfg: Dict, path: Tuple[str, ...], value) -> None:
    """按点路径写入嵌套字典（沿途缺失的层自动补建）。"""
    node = cfg
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _format_yaml_scalar(value) -> str:
    """把 Python 标量格式化为 YAML 字面量（bool 小写；浮点去多余零）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = "{:g}".format(value)
        # 2.0 这类整数值补回小数点，保持"这是个浮点参数"的可读性
        return text if ("." in text or "e" in text) else text + ".0"
    return str(value)


def rewrite_yaml_values(text: str, updates: Dict[Tuple[str, ...], object]) -> Tuple[str, List[Tuple[str, ...]]]:
    """在 YAML 原文中按"配置点路径"逐行替换标量值，保留注释与排版。

    仅处理"key: 标量"形态的行（本项目 config.yaml 的全部可调项都是这种），
    用缩进栈跟踪当前所在层级以精确匹配路径（同名键在不同层级不会误伤）。

    返回 (新文本, 实际替换到的路径列表)。
    """
    lines = text.splitlines(True)
    stack: List[Tuple[int, str]] = []          # [(缩进, 键名)]
    replaced: List[Tuple[str, ...]] = []
    key_re = re.compile(r"^(\s*)([A-Za-z_]\w*)\s*:(.*)$")

    for i, raw in enumerate(lines):
        no_comment = raw.split("#", 1)[0]
        if not no_comment.strip():
            continue                            # 空行/纯注释行
        m = key_re.match(no_comment.rstrip("\n"))
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        path = tuple(k for _, k in stack)
        if path not in updates:
            continue

        new_val = _format_yaml_scalar(updates[path])
        prefix = "{}{}: ".format(" " * indent, key)
        hash_idx = raw.find("#")
        if hash_idx >= 0:
            # 行尾带注释：新值后补空格对齐到原注释列（至少留 2 空格）
            pad = max(hash_idx - len(prefix) - len(new_val), 2)
            lines[i] = prefix + new_val + " " * pad + raw[hash_idx:]
        else:
            lines[i] = prefix + new_val + ("\n" if raw.endswith("\n") else "")
        replaced.append(path)
    return "".join(lines), replaced


def default_config_path() -> str:
    """定位保存目标 config.yaml：源码运行=包内配置；exe=优先 exe 旁的配置。"""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "config.yaml")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # fatigue_system/
    return os.path.join(here, "config.yaml")


class _SettingRow(QWidget):
    """面板内一行：左参数名 + 右控件，行高约 44px（§8 行高按样图尺度放大）。"""

    def __init__(self, label: str, control: QWidget, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        name = QLabel(label, self)
        name.setStyleSheet("font-size:14px; background:transparent;")
        lay.addWidget(name)
        lay.addStretch(1)
        lay.addWidget(control)


class SettingsDialog(QWidget):
    """参数设置覆盖层：apply_values（热更新共享配置）/ save_to_file（保注释写回）。

    对外协议与旧版一致（dev_tools/verify_settings.py 依赖）：
    `_editors[路径] = (类型, 控件)`、`apply_values()`、`save_to_file()`、
    `applied` 信号。展示形态从 QDialog 换成主窗口内 overlay（§7.7）。
    """

    applied = pyqtSignal()     # 参数已写入共享配置字典，主窗口应做热更新

    def __init__(self, cfg: Dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        # 配置文件路径：app.py 加载时记录；缺失（如测试直接构造）则按默认规则定位
        self._cfg_path = cfg.get("_config_path") or default_config_path()
        self._dim_p = 0.0            # 压暗层进度 0..0.28（动画插值）
        self._closing = False

        self.setFocusPolicy(Qt.StrongFocus)
        self._build_panel()
        if parent is not None:
            parent.installEventFilter(self)   # 跟随主窗口缩放
            self.hide()

    # ------------------------------- 界面搭建 --------------------------------

    def _build_panel(self) -> None:
        # 外层 wrap 挂透明度效果，内层面板挂阴影（同一控件只能挂一个效果）
        self._wrap = QWidget(self)
        self._opacity = QGraphicsOpacityEffect(self._wrap)
        self._opacity.setOpacity(1.0)
        self._wrap.setGraphicsEffect(self._opacity)

        wrap_lay = QVBoxLayout(self._wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(self._wrap)
        # 注意：阴影不能用 QGraphicsDropShadowEffect——外层 wrap 已挂透明度效果，
        # Qt 不支持嵌套 QGraphicsEffect（会把整个子树渲染成空白）。
        # §4 的弹出面板阴影改为在覆盖层 paintEvent 手绘（见 _draw_panel_shadow）。
        # 样式必须用 objectName 限定：QLabel 继承 QFrame，裸 "QFrame" 选择器
        # 会给面板里所有标签都套上白底边框。
        panel.setObjectName("settingsPanel")
        panel.setStyleSheet(
            "QFrame#settingsPanel {{ background-color: {bg}; border: 1px solid {bd}; "
            "border-radius: {r}px; }}".format(
                bg=theme.SURFACE, bd=theme.BORDER, r=theme.RADIUS_PANEL))
        wrap_lay.addWidget(panel)
        self._panel = panel

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        lay.setSpacing(12)

        # 标题行：标题 + 右侧 × 关闭
        head = QHBoxLayout()
        title = QLabel("参数设置", panel)
        title.setStyleSheet("font-size:17px; font-weight:500; background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        btn_close = IconButton("close", "关闭（不保存）", panel)
        btn_close.setFixedSize(32, 32)
        btn_close.clicked.connect(self.close_overlay)
        head.addWidget(btn_close)
        lay.addLayout(head)

        # 分组容器（§8：白底圆角 + 行间分隔线，组上方灰色小标签）
        self._editors: Dict[Tuple[str, ...], Tuple[str, QWidget]] = {}
        for group_name, indices in _GROUPS:
            glabel = QLabel(group_name, panel)
            glabel.setObjectName("sectionTitle")
            lay.addWidget(glabel)
            box = QFrame(panel)
            box.setObjectName("settingsGroup")
            box.setStyleSheet(
                "QFrame#settingsGroup {{ background-color: {bg}; border: 1px solid {bd}; "
                "border-radius: 10px; }}".format(bg=theme.SURFACE, bd=theme.BORDER))
            box_lay = QVBoxLayout(box)
            box_lay.setContentsMargins(0, 2, 0, 2)
            box_lay.setSpacing(0)
            for j, idx in enumerate(indices):
                spec = _FIELDS[idx]
                label, path, kind = spec[0], spec[1], spec[2]
                current = _cfg_get(self._cfg, path)
                if kind == "bool":
                    w = Switch(box)
                    w.setChecked(bool(current))
                else:
                    w = QDoubleSpinBox(box)
                    w.setDecimals(2)
                    w.setRange(float(spec[3]), float(spec[4]))
                    w.setSingleStep(float(spec[5]))
                    w.setValue(float(current) if current is not None else float(spec[3]))
                    w.setFixedWidth(96)
                    w.setAlignment(Qt.AlignRight)
                self._editors[path] = (kind, w)
                if j > 0:
                    sep = QFrame(box)
                    sep.setFixedHeight(1)
                    sep.setStyleSheet(
                        "background-color:{}; border:none;".format(theme.SEPARATOR))
                    box_lay.addWidget(sep)
                box_lay.addWidget(_SettingRow(label, w, box))
            lay.addWidget(box)

        hint = QLabel("全部参数及依据见 参数设置说明.md", panel)
        hint.setStyleSheet(
            "font-size:12px; color:{}; background:transparent;".format(theme.TEXT_MUTE))
        lay.addWidget(hint)

        # 底部按钮：取消(描边) + 完成(唯一实心蓝，应用并写回，§8)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("取消", panel)
        btn_cancel.clicked.connect(self.close_overlay)
        btns.addWidget(btn_cancel)
        btn_done = QPushButton("完成", panel)
        btn_done.setProperty("primary", True)
        btn_done.clicked.connect(self._on_done)
        btns.addWidget(btn_done)
        lay.addLayout(btns)

    # ------------------------------ overlay 显示 ------------------------------

    def open_over(self) -> None:
        """在父窗口上弹出：压暗层淡入 + 面板 0.95→1.00 浮现（§7.7，220ms）。"""
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        self._closing = False
        self.setGeometry(parent.rect())
        final = self._final_panel_rect()
        self._wrap.setGeometry(self._scaled_rect(final, 0.95))
        self.show()
        self.raise_()
        self.setFocus()
        self._opacity.setOpacity(0.0)
        animate(self, "_anim_op", 0.0, 1.0, theme.ANIM_BASE, self._opacity.setOpacity)
        animate(self, "_anim_geo", self._scaled_rect(final, 0.95), final,
                theme.ANIM_BASE, self._wrap.setGeometry)
        animate(self, "_anim_dim", 0.0, 0.28, theme.ANIM_BASE, self._on_dim)

    def close_overlay(self) -> None:
        """关闭（取消/完成/点压暗区/Esc）：淡出 + 缩回 0.97（150ms，InCubic）。"""
        if self._closing:
            return
        if self.parentWidget() is None:
            self.hide()
            return
        self._closing = True
        final = self._wrap.geometry()
        animate(self, "_anim_op", float(self._opacity.opacity()), 0.0,
                theme.ANIM_FAST, self._opacity.setOpacity, easing=EASE_IN)
        animate(self, "_anim_geo", final, self._scaled_rect(final, 0.97),
                theme.ANIM_FAST, self._wrap.setGeometry, easing=EASE_IN)
        animate(self, "_anim_dim", self._dim_p, 0.0, theme.ANIM_FAST,
                self._on_dim, easing=EASE_IN, on_finish=self._after_close)

    def _after_close(self) -> None:
        self.hide()
        self._closing = False
        self.deleteLater()      # 每次打开都新建实例（取当前配置值），关完即销毁

    def _final_panel_rect(self) -> QRect:
        """面板最终矩形：水平垂直居中。"""
        hint = self._wrap.sizeHint()
        h = min(hint.height(), self.height() - 48)
        x = (self.width() - _PANEL_W) // 2
        y = (self.height() - h) // 2
        return QRect(x, y, _PANEL_W, h)

    @staticmethod
    def _scaled_rect(rect: QRect, factor: float) -> QRect:
        """以矩形中心缩放（§7.7：起点 0.95 的"微缩放浮现"）。"""
        w = int(rect.width() * factor)
        h = int(rect.height() * factor)
        return QRect(rect.center().x() - w // 2, rect.center().y() - h // 2, w, h)

    def _on_dim(self, v) -> None:
        self._dim_p = float(v)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._dim_p > 0.001:
            p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * self._dim_p)))
        self._draw_panel_shadow(p)
        p.end()

    def _draw_panel_shadow(self, p: QPainter) -> None:
        """手绘面板软阴影（≈ 0 8px 24px rgba(0,0,0,0.12)，§4 唯一允许的阴影）。

        用几层逐渐外扩、透明度递减的圆角矩形近似高斯模糊；随面板淡入淡出
        （强度乘压暗进度，出场入场观感一致）。
        """
        if not self._wrap.isVisible() or self._dim_p <= 0.001:
            return
        base = self._wrap.geometry().translated(0, 8)
        strength = self._dim_p / 0.28          # 随开合动画 0..1
        p.setPen(Qt.NoPen)
        for grow, alpha in ((24, 6), (16, 10), (8, 14)):
            c = QColor(0, 0, 0, int(alpha * strength))
            p.setBrush(c)
            r = base.adjusted(-grow, -grow, grow, grow)
            p.drawRoundedRect(r, theme.RADIUS_PANEL + grow / 2.0,
                              theme.RADIUS_PANEL + grow / 2.0)

    def mousePressEvent(self, event) -> None:
        # 点击压暗区域 = 取消并关闭（§7.7 交互约定）
        if not self._wrap.geometry().contains(event.pos()):
            self.close_overlay()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_overlay()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        # 主窗口缩放时覆盖层跟随铺满、面板保持居中
        if obj is self.parentWidget() and event.type() == event.Resize and self.isVisible():
            self.setGeometry(obj.rect())
            self._wrap.setGeometry(self._final_panel_rect())
        return super().eventFilter(obj, event)

    # ------------------------------- 应用/保存 --------------------------------

    def _collect(self) -> Dict[Tuple[str, ...], object]:
        """读取界面当前值 → {配置点路径: 新值}。"""
        values = {}
        for path, (kind, w) in self._editors.items():
            if kind == "bool":
                values[path] = bool(w.isChecked())
            else:
                values[path] = float(w.value())
        return values

    def _validate(self, values: Dict[Tuple[str, ...], object]) -> bool:
        """一致性校验：分级阈值必须 mild < moderate < severe。"""
        th = ("fusion", "level_thresholds")
        mild = values[th + ("mild",)]
        moderate = values[th + ("moderate",)]
        severe = values[th + ("severe",)]
        if not (mild < moderate < severe):
            QMessageBox.warning(
                self, "参数无效",
                "分级阈值必须满足 轻度 < 中度 < 重度（当前 {:.2f} / {:.2f} / {:.2f}）。".format(
                    mild, moderate, severe))
            return False
        return True

    def apply_values(self) -> bool:
        """把界面值写入共享配置字典并通知主窗口热更新；校验失败返回 False。"""
        values = self._collect()
        if not self._validate(values):
            return False
        for path, value in values.items():
            _cfg_set(self._cfg, path, value)
        self.applied.emit()
        return True

    def save_to_file(self) -> str:
        """把当前可调项的值写回 config.yaml（保留注释），返回实际保存路径。

        exe 打包场景下内置配置在只读的临时目录，写失败时自动落到 exe 旁；
        若目标文件不存在（首次导出），以当前加载的配置文件为底稿复制后再改。
        """
        src = self._cfg_path
        if not os.path.isfile(src):
            src = default_config_path()
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        new_text, replaced = rewrite_yaml_values(text, self._collect())
        target = self._cfg_path
        # exe 场景：加载的可能是打包在临时解压目录(_MEIPASS)里的内置配置——该目录
        # 退出即删，写进去等于白存；一律改存 exe 旁（启动加载时该位置优先级最高）。
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.abspath(target).startswith(os.path.abspath(meipass)):
            target = default_config_path()
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(new_text)
        except OSError:
            target = os.path.join(
                os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd(),
                "config.yaml")
            with open(target, "w", encoding="utf-8") as f:
                f.write(new_text)
        missing = len(self._collect()) - len(replaced)
        if missing:
            print("[参数设置] 有 {} 项未在配置文件中找到对应行（已在运行时生效）。".format(missing))
        return target

    # ------------------------------- 按钮响应 --------------------------------

    def _on_done(self) -> None:
        """完成 = 应用（热更新）并写回配置文件，然后关闭（§8）。"""
        if not self.apply_values():
            return
        try:
            self.save_to_file()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败",
                                "写入配置文件失败：{}\n参数已在本次运行生效。".format(exc))
        self.close_overlay()
