# -*- coding: utf-8 -*-
"""「参数设置」对话框（M6 拓展功能，任务书"鼓励增加参数设置"项）。

设计要点：
  * 只暴露测试中**最常调**的参数（完整参数仍在 config.yaml，含义见 参数设置说明.md），
    避免把整份配置搬进界面导致误操作；
  * 「应用」**立即生效**：直接改共享配置字典，主窗口随后对聚合器/报警状态机等做
    热更新（不清滑窗统计、不丢报警状态，见 MainWindow._apply_runtime_config）；
  * 「应用并保存」把新值写回 config.yaml：**逐行只替换目标键的值**，完整保留原有
    中文注释与排版（PyYAML round-trip 会丢注释，故用行级替换）；打包 exe 内置配置
    只读时自动改存到 exe 旁的 config.yaml（启动加载时该位置优先级本就最高）。
"""

import os
import re
import sys
from typing import Dict, List, Tuple

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
    QMessageBox, QSpinBox, QVBoxLayout,
)

# 可调项清单：(界面标签, 配置点路径, 类型, 最小, 最大, 步长)
# bool 型省略数值范围。范围给的是"安全可用区"，防止误设出病态值。
_FIELDS = [
    ("闭眼判定 k（越大越保守）",        ("eye", "ear_closed_k_std"),               "float", 0.5, 3.0, 0.1),
    ("哈欠 MAR 阈值",                  ("mouth", "mar_yawn_thresh"),              "float", 0.05, 0.80, 0.01),
    ("轻度疲劳阈值 mild",              ("fusion", "level_thresholds", "mild"),     "float", 0.05, 0.95, 0.05),
    ("中度疲劳阈值 moderate",          ("fusion", "level_thresholds", "moderate"), "float", 0.05, 0.95, 0.05),
    ("重度疲劳阈值 severe",            ("fusion", "level_thresholds", "severe"),   "float", 0.05, 0.95, 0.05),
    ("报警所需连续窗口（个）",          ("fusion", "alarm_consecutive_windows"),    "int",   1,   10,  1),
    ("微睡眠直接报警（秒，0=关）",      ("fusion", "microsleep_sec"),              "float", 0.0, 10.0, 0.5),
    ("人脸丢失提示（秒）",              ("alarm", "face_lost_sec"),                "float", 1.0, 99.0, 1.0),
    ("报警声音",                       ("alarm", "sound_enable"),                 "bool"),
    ("报警弹窗",                       ("alarm", "popup_enable"),                 "bool"),
    ("视频文件循环播放",                ("video", "loop_file"),                    "bool"),
]


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


class SettingsDialog(QDialog):
    """常用参数设置对话框：应用（立即生效）/ 应用并保存（写回 config.yaml）。"""

    applied = pyqtSignal()     # 参数已写入共享配置字典，主窗口应做热更新

    def __init__(self, cfg: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("参数设置")
        self._cfg = cfg
        # 配置文件路径：app.py 加载时记录；缺失（如测试直接构造）则按默认规则定位
        self._cfg_path = cfg.get("_config_path") or default_config_path()

        root = QVBoxLayout(self)
        hint = QLabel(
            "只列出最常调的参数；全部参数及依据见 参数设置说明.md。\n"
            "「应用」立即生效（不清统计、不打断监测）；「应用并保存」同时写回配置文件。",
            self)
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)
        self._editors = {}
        for spec in _FIELDS:
            label, path, kind = spec[0], spec[1], spec[2]
            current = _cfg_get(self._cfg, path)
            if kind == "bool":
                w = QCheckBox(self)
                w.setChecked(bool(current))
            elif kind == "int":
                w = QSpinBox(self)
                w.setRange(int(spec[3]), int(spec[4]))
                w.setSingleStep(int(spec[5]))
                w.setValue(int(current) if current is not None else int(spec[3]))
            else:
                w = QDoubleSpinBox(self)
                w.setDecimals(2)
                w.setRange(float(spec[3]), float(spec[4]))
                w.setSingleStep(float(spec[5]))
                w.setValue(float(current) if current is not None else float(spec[3]))
            self._editors[path] = (kind, w)
            form.addRow(label, w)
        root.addLayout(form)

        buttons = QDialogButtonBox(self)
        self._btn_apply = buttons.addButton("应用", QDialogButtonBox.ApplyRole)
        self._btn_save = buttons.addButton("应用并保存", QDialogButtonBox.AcceptRole)
        buttons.addButton("关闭", QDialogButtonBox.RejectRole)
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_save.clicked.connect(self._on_apply_and_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------- 内部逻辑 --------------------------------

    def _collect(self) -> Dict[Tuple[str, ...], object]:
        """读取界面当前值 → {配置点路径: 新值}。"""
        values = {}
        for path, (kind, w) in self._editors.items():
            if kind == "bool":
                values[path] = bool(w.isChecked())
            elif kind == "int":
                values[path] = int(w.value())
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

    def _on_apply(self) -> None:
        if self.apply_values():
            QMessageBox.information(self, "参数设置", "已应用（本次运行生效）。")

    def _on_apply_and_save(self) -> None:
        if not self.apply_values():
            return
        try:
            target = self.save_to_file()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败",
                                "写入配置文件失败：{}\n参数已在本次运行生效。".format(exc))
            return
        QMessageBox.information(self, "参数设置",
                                "已应用并保存到：\n{}".format(target))
        self.accept()
