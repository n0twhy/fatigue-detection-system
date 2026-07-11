# -*- coding: utf-8 -*-
"""基于多特征融合的疲劳检测与预警系统（课程设计交付物）。

本包内的全部代码由本课程设计新增，**不修改 rPPG-Toolbox 现有任何文件**。
按开发规格书 §10 里程碑增量开发，当前处于：M0（脚手架）。

子包说明：
    core/         —— 核心算法与数据结构（特征提取、滑窗、校准、融合）
    io/           —— 输入输出（视频源、CSV 记录）
    ui/           —— PyQt5 图形界面
    experiments/  —— Part A：UBFC 上 rPPG 深度 vs 无监督对比实验

依赖约定：所有命令在 conda 环境 rppg-toolbox 下运行；额外依赖见
requirements-fatigue.txt。
"""

# 版本号：与里程碑对应，便于报告与 git 记录追溯
__version__ = "0.0.1-M0"
