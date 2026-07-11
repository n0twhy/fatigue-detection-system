# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把疲劳检测软件打成 Windows 独立程序。

在 Windows 上、装好依赖后运行：
    pyinstaller fatigue_system/packaging/app.spec
产物在 dist/疲劳检测系统/ 下（含 疲劳检测系统.exe，双击即用）。

打包范围：只打疲劳检测软件本身（app/core/io/ui）。刻意排除 torch、
matplotlib 和整个 rPPG-Toolbox（那些只有 M5 离线实验用），把体积压到最小。
"""

import os
from PyInstaller.utils.hooks import collect_all

# 本 spec 在 fatigue_system/packaging/ 下，仓库根是它的上上级
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
FS_DIR = os.path.join(REPO_ROOT, "fatigue_system")

# mediapipe 靠一堆 .tflite / .binarypb 模型文件工作，PyInstaller 默认不收集，
# collect_all 会把它的数据、动态库和隐藏导入一并抓齐。
mp_datas, mp_binaries, mp_hidden = collect_all("mediapipe")

a = Analysis(
    [os.path.join(FS_DIR, "app.py")],
    pathex=[REPO_ROOT],                 # 让 fatigue_system 能作为包被导入
    binaries=mp_binaries,
    datas=mp_datas + [
        (os.path.join(FS_DIR, "config.yaml"), "fatigue_system"),
    ],
    hiddenimports=mp_hidden + [
        "PyQt5.QtMultimedia",           # 报警声音 QSound 用
        "scipy.signal",
    ],
    excludes=[
        # 这些只有 M5 离线实验依赖，软件本体用不到，排除以缩小体积
        "torch", "torchvision", "matplotlib", "pandas",
        "unsupervised_methods", "neural_methods", "dataset", "evaluation",
        "tensorflow", "IPython", "notebook",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="疲劳检测系统",
    console=False,                      # 图形程序，不弹黑色命令行窗口
    icon=None,                          # 有图标可在此填 .ico 路径
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name="疲劳检测系统",               # 产物文件夹名 dist/疲劳检测系统/
)
