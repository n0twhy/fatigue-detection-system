# 基于多特征融合的疲劳检测与预警系统

用一个普通摄像头（或本地视频）实时判断人的疲劳程度，分成 **清醒 / 轻度 / 中度 /
重度** 四级，重度时弹窗+响铃报警，并把检测数据存成 CSV。全程本地运行，
**不上传数据、不用穿戴任何设备**。

> 综合课程设计作品。基础任务（摄像头/视频输入 → 面部特征提取 → 多特征融合
> 疲劳判断 → 分级预警 → 数据保存）完整实现。

## 功能特性

- **三类面部特征**：眼部（EAR、PERCLOS、眨眼率、闭眼时长）、嘴部（MAR、哈欠）、
  头部姿态（低头/偏头/点头）。
- **无接触心率**：从面部皮肤颜色的细微变化用 rPPG(POS) 估计心率，作为生理特征。
- **多特征加权融合** + 四级判定 + 防误报（滑窗统计 + 评分平滑 + 连续窗口触发）。
- **个性化基线校准**：开机采集本人 30 秒清醒态，按"偏离自己基线"判断，比固定阈值更准。
- **六区图形界面**：视频显示 / 特征参数 / 疲劳等级 / 预警提示 / 操作控制 / 数据记录。
- **数据导出**：逐条明细 CSV + 会话汇总 CSV，Excel 直接打开。

## 快速开始

### 方式一：直接用打包好的程序（推荐给测试同学，无需装环境）

下载 Release 里的 `FatigueDetection-Windows.zip` → 解压 → 双击 `FatigueDetection.exe`。
（Windows，目标电脑不用装 Python，也不用打包。）

> exe 由开发者打包一次后提供。打包方式见
> [fatigue_system/packaging/打包说明.md](fatigue_system/packaging/打包说明.md)：
> 既可在 GitHub 上一键自动打包（无需自己有 Windows），也可在本地 Windows 打。

### 方式二：源码运行（需要 Python 3.8~3.11）

```bash
pip install -r requirements.txt
python -m fatigue_system.app
```

具体操作（选摄像头/开视频/校准/记录/看结果）见 **[运行说明.md](运行说明.md)**。

## 目录结构

```
fatigue_system/
├── app.py              程序入口
├── config.yaml         全部可调参数（阈值/权重/窗口，都有中文注释）
├── core/               特征提取与融合判断
│   ├── face_mesh.py        MediaPipe 468 关键点
│   ├── eye_features.py     EAR / 眼部
│   ├── mouth_features.py   MAR / 嘴部
│   ├── head_pose.py        头部姿态
│   ├── rppg_realtime.py    实时心率（POS）
│   ├── feature_window.py   滑窗聚合（PERCLOS/眨眼率/哈欠…）
│   ├── calibration.py      个性化基线校准
│   └── fusion.py           多特征加权融合 + 四级 + 防误报
├── io/                 视频输入、CSV 记录
├── ui/                 六区图形界面
└── packaging/          打成 Windows exe 的脚本与说明
```

## 文档

- **[运行说明.md](运行说明.md)** —— 使用/测试指南（面向不看代码的同学）。
- **[fatigue_system/packaging/打包说明.md](fatigue_system/packaging/打包说明.md)** —— 怎么打成 Windows exe。

## 技术栈

Python · PyQt5（界面）· MediaPipe（人脸关键点）· OpenCV · NumPy · SciPy。
心率 POS 算法参考 Wang et al., *Algorithmic Principles of Remote PPG*, IEEE TBME 2017。

## 隐私说明

符合 GB/T 35273—2020《信息安全技术 个人信息安全规范》：全部在本地处理，
不联网、不上传、无接触采集，数据仅存于本机 CSV。
