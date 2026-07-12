# fatigue_system —— 基于多特征融合的疲劳检测与预警系统

单摄像头、实时、纯软件的疲劳检测系统（综合课程设计）。同时观察**眼部**
（EAR/PERCLOS/眨眼动力学/微睡眠）、**嘴部**（MAR/哈欠）、**头部姿态**
（低头/偏头/点头）与 **rPPG 无接触心率**，质量感知加权融合出疲劳分，
四级判定（清醒/轻度/中度/重度）+ KSS 嗜睡量表，重度弹窗响铃报警，数据存 CSV。

## 快速开始

```bash
conda activate rppg-toolbox          # Python 3.8，依赖见 requirements-fatigue.txt
cd <rPPG-Toolbox 根目录>
python -m fatigue_system.app         # 启动 GUI
python -m fatigue_system.app --selftest 2   # 无人值守自检（exit 0 = 正常）
```

Windows 免环境版：见 GitHub 仓库 Releases 里的 `FatigueDetection-Windows.zip`
（打包方式见 `packaging/打包说明.md`）。

## 文档导航

| 读者 | 文档 |
|---|---|
| 使用/测试同学 | `运行说明.md`（怎么用）、`测试指南.md`（怎么测 6 场景与统计指标） |
| 想调参 | `参数设置说明.md`（config.yaml 每个参数的含义与依据） |
| 接手开发 | **`PROGRESS.md`（权威台账，先读）**、`开发进度与问题交接.md`（环境/坑）、`创新点调研与实现方案.md`、`眼部检测改进日志.md`、`组员反馈处理记录.md` |

## 目录结构（规格书 §4）

```
fatigue_system/
├── app.py            # 入口（--selftest / Qt 插件修复 / 中文字体 / exe 兼容）
├── config.yaml       # 唯一参数来源（全中文注释）
├── core/             # 特征提取/滑窗聚合/校准/融合/实时rPPG（纯算法，无 UI 依赖）
├── io/               # 视频源（摄像头/文件）与 CSV 记录
├── ui/               # PyQt5 六区界面（深色仪表盘）+ 参数设置对话框
├── experiments/      # M5 rPPG 对比实验 + CEW 眼部验证（依赖 toolbox/torch，非软件本体）
├── packaging/        # Windows exe 打包（PyInstaller + GitHub Actions）
├── dev_tools/        # 开发回归脚本 verify_*.py（非交付物）
└── outputs/          # CSV 记录 / 报警音 / 实验结果
```

软件本体（app/core/io/ui）**对 rPPG-Toolbox 与 torch 零依赖**，只用
PyQt5/opencv/mediapipe/numpy/scipy/PyYAML，可独立打包分发（独立发布仓库见
`../FatigueDetectionSystem/`，GitHub: n0twhy/fatigue-detection-system）。

## 铁律（改代码前必读）

不修改 toolbox 现有文件；不训练分类器（阈值+加权融合）；单摄像头无外设；
配置驱动无魔法数字；全中文注释；时长判定一律用时间戳不用帧数。
详见 `EXPWORK/疲劳检测系统_开发规格书.md` 与 `PROGRESS.md`。
