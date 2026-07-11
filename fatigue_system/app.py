# -*- coding: utf-8 -*-
"""疲劳检测与预警系统 —— 软件主入口。

运行方式（均在 conda 环境 rppg-toolbox 下、于 rPPG-Toolbox 根目录执行）：
    推荐：  python -m fatigue_system.app
    亦可：  python fatigue_system/app.py

可选参数：
    --config <path>     指定配置文件（默认为本包内 config.yaml）
    --selftest [秒]     无人值守自检：启动窗口并在若干秒后自动退出，
                        配合 QT_QPA_PLATFORM=offscreen 可在无显示环境下验证。

M0 阶段本入口仅拉起"视频源预览"窗口（视频显示区 + 操作控制区）。
"""

import argparse
import os
import sys

# Windows 控制台默认编码可能不是 UTF-8（如 GBK/cp1252），直接 print 中文会
# 抛 UnicodeEncodeError 让程序崩溃。这里强制把标准输出/错误切到 UTF-8
# （errors=replace 兜底），保证在任何环境打印中文都不会挂。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 降低 OpenCV V4L 探测警告噪声：必须在任何 cv2 导入之前设置
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

# 让本文件既能以 `python -m fatigue_system.app` 运行，也能以
# `python fatigue_system/app.py` 直接运行：确保 toolbox 根目录在 sys.path 上。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # rPPG-Toolbox 根目录
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yaml  # noqa: E402  (置于 sys.path 处理之后)

# 是否被 PyInstaller 打包成 exe 运行
_FROZEN = getattr(sys, "frozen", False)
# 可写目录：打成 exe 时用 exe 所在文件夹（临时解压目录只读、退出即删，
# 不能往里写 CSV/日志）；源码运行时用仓库根。CSV、alarm.wav 等都落到这里。
_APP_DIR = os.path.dirname(sys.executable) if _FROZEN else _ROOT

def _find_default_config() -> str:
    """定位默认配置文件，兼顾源码运行和打包成 exe 两种情况。

    打包后不能用 __file__ 推目录（会落到 _internal 根、找不到 config），
    改用 PyInstaller 的资源根 sys._MEIPASS。按优先级找第一个存在的：
      1) exe 旁边的 config.yaml —— 同学不改代码就能调参，最高优先；
      2) 打包进 _internal/fatigue_system/config.yaml —— spec 放的位置；
      3) 源码运行时的包内 config.yaml。
    """
    candidates = []
    if _FROZEN:
        candidates.append(os.path.join(_APP_DIR, "config.yaml"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "fatigue_system", "config.yaml"))
            candidates.append(os.path.join(meipass, "config.yaml"))
    candidates.append(os.path.join(_HERE, "config.yaml"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


DEFAULT_CONFIG_PATH = _find_default_config()


def load_config(path: str) -> dict:
    """读取 YAML 配置为字典，并把输出目录解析成可写的绝对路径。"""
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    _resolve_output_dir(config)
    return config


def _resolve_output_dir(config: dict) -> None:
    """把 logging.csv_dir 的相对路径锚定到可写目录 _APP_DIR。

    配置里默认写的是 "fatigue_system/outputs" 这种相对路径，直接用会随
    启动时的工作目录变化、打成 exe 后更会写到临时解压目录里丢失。这里
    统一改成绝对路径：源码运行仍等价于原来的 outputs/，exe 版则落到
    exe 同级的 outputs/，同学一眼能找到导出的 CSV。
    """
    log_cfg = config.setdefault("logging", {})
    csv_dir = log_cfg.get("csv_dir", "fatigue_system/outputs")
    if not os.path.isabs(csv_dir):
        # exe 版直接放 exe 旁的 outputs/；源码版保持仓库内相对结构
        csv_dir = os.path.join(_APP_DIR, "outputs" if _FROZEN else csv_dir)
    os.makedirs(csv_dir, exist_ok=True)
    log_cfg["csv_dir"] = csv_dir


def _cjk_font_candidates():
    """按优先级给出中文字体候选路径，兼顾原生 Windows、WSL、Linux。

    原生 Windows 下字体在 %WINDIR%\\Fonts；WSL 下经 /mnt/c 访问同一批字体；
    Linux 用 Noto/文泉驿。打成 exe 在同学的 Windows 上跑时走第一组。
    """
    names = ["msyh.ttc", "msyhbd.ttc", "Deng.ttf", "simhei.ttf", "simsun.ttc"]
    cands = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:                                   # 原生 Windows
        cands += [os.path.join(windir, "Fonts", n) for n in names]
    cands += ["/mnt/c/Windows/Fonts/" + n for n in names]   # WSL 访问 Windows 字体
    cands += [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    return cands


def _setup_cjk_font(app, config: dict) -> None:
    """为界面加载并设置含中文字形的字体，避免中文显示为方块。

    优先使用 config.yaml 中 ui.font_path 指定的字体；否则按 _CJK_FONT_CANDIDATES
    自动探测。全部失败时不报错，仅打印安装提示（界面仍可用，中文可能为方块）。
    """
    from PyQt5.QtGui import QFont, QFontDatabase

    ui_cfg = (config or {}).get("ui", {}) or {}
    candidates = []
    configured = ui_cfg.get("font_path")
    if configured:
        candidates.append(configured)
    candidates.extend(_cjk_font_candidates())

    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0]))
            print("[界面] 已加载中文字体：{}（{}）".format(families[0], path))
            return
    print(
        "[提示] 未找到中文字体，界面中文可能显示为方块。\n"
        "       可在 config.yaml 的 ui.font_path 指定字体，或执行："
        "sudo apt install fonts-noto-cjk",
        file=sys.stderr,
    )


def _use_pyqt5_qt_plugins() -> None:
    """把 Qt 平台插件路径强制指回 PyQt5 自带的一套。

    背景：opencv-python 会在 `import cv2` 时把 QT_QPA_PLATFORM_PLUGIN_PATH
    劫持到 cv2/qt/plugins（其中的 libqxcb.so 与 PyQt5 的 Qt 不兼容），
    导致 PyQt5 加载 xcb 平台插件失败而崩溃。此处在创建 QApplication 前
    覆盖该变量，指向 PyQt5/Qt5/plugins，从而使用兼容的插件。
    必须在 `import cv2` 之后、`QApplication(...)` 之前调用。
    """
    if _FROZEN:
        # 打包成 exe 时，PyInstaller 的运行时钩子已把 Qt 插件路径设到 _internal
        # 里的正确位置。这里再按源码目录结构去覆盖只会指向不存在的路径，
        # 反而让 Qt 平台插件加载失败、程序双击无反应。故打包环境直接跳过。
        return
    import PyQt5

    base = os.path.dirname(PyQt5.__file__)
    for sub in ("Qt5", "Qt"):
        plugins = os.path.join(base, sub, "plugins")
        if os.path.isdir(os.path.join(plugins, "platforms")):
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugins
            return


def _fix_mediapipe_unicode_path() -> None:
    """让 mediapipe 在含中文/非 ASCII 路径下也能找到模型文件（仅打包后 Windows）。

    mediapipe 的 C++ 层无法打开含非 ASCII 字符的路径。它用
    solution_base.__file__ 推算资源根，若 exe 放在中文用户名/文件夹下
    （如 C:\\Users\\风涌云起\\...），推出来的路径带中文，底层就打不开模型、
    报 FileNotFoundError。

    解法：把资源根目录（_MEIPASS）转成 Windows 短路径(8.3 命名，全 ASCII)，
    并据此改写 solution_base.__file__，使 mediapipe 后续按 ASCII 短路径去找。
    短路径生成默认在系统盘开启；万一取不到就保持原样、静默跳过。
    """
    if sys.platform != "win32":
        return
    meipass = getattr(sys, "_MEIPASS", "")
    if not meipass:
        return
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(meipass, buf, 32768):
            return
        short = buf.value
        os.chdir(short)
        import mediapipe.python.solution_base as _sb
        # solution_base 用 abspath(__file__)[:-3] 当资源根；给它一个短路径版本
        _sb.__file__ = os.path.join(short, "mediapipe", "python", "solution_base.py")
    except Exception as exc:
        print("[提示] mediapipe 中文路径兼容处理未生效：{}".format(exc), file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="疲劳检测与预警系统（M0：视频源预览）")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径（默认为包内 config.yaml）"
    )
    parser.add_argument(
        "--selftest",
        nargs="?",
        type=float,
        const=3.0,
        default=None,
        metavar="秒",
        help="无人值守自检：启动后自动退出（默认 3 秒）",
    )
    args = parser.parse_args(argv)

    if _FROZEN:
        _fix_mediapipe_unicode_path()

    if not os.path.isfile(args.config):
        print("[错误] 找不到配置文件：{}".format(args.config), file=sys.stderr)
        return 2
    config = load_config(args.config)

    # PyQt5 在解析完参数、确认配置无误后再导入，便于 --help 在缺依赖时也可用
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QTimer

    # MainWindow 会导入 cv2，cv2 会劫持 Qt 插件路径；导入后立即纠正
    from fatigue_system.ui.main_window import MainWindow

    _use_pyqt5_qt_plugins()  # 必须在 QApplication 之前

    app = QApplication(sys.argv[:1])
    _setup_cjk_font(app, config)  # 设置中文字体，避免界面方块乱码
    window = MainWindow(config)
    window.show()

    if args.selftest is not None:
        # 自检模式：若干秒后自动退出，返回码 0 表示窗口成功构建并进入事件循环
        seconds = max(0.5, float(args.selftest))
        print("[自检] 窗口已构建并显示，将在 {:.1f}s 后自动退出。".format(seconds))
        QTimer.singleShot(int(seconds * 1000), app.quit)

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
