# PROGRESS —— 疲劳检测系统进度与决策台账

> 本文件是**权威进度台账**（single source of truth）。任何接手都先读这里。
> 最近更新：2026-07-13。配套文档：
> `创新点调研与实现方案.md`（调研/论文）、`眼部检测改进日志.md`（眼部）、
> `组员反馈处理记录.md`（组员反馈）、`开发进度与问题交接.md`（总交接）。

---

## 0. 一句话现状

**v1.8 已发布、软件侧全部完成**（2026-07-13）：四个创新点落地（单测 11 条+全回归
7 项全绿）、M6 交付物补齐（参数设置说明/测试指南/README/GUI 参数设置面板）、
Release exe 真机中文路径验证通过。规格书 §9 交付物软件侧齐备。
剩余工作只有**写报告**（§5 第 6 条：创新意识/未来工作章节素材见 §1/§3/§4）。
回归/单测脚本持久化在 `dev_tools/verify_*.py`（innovations/settings/m3/m4/lowfps/
weak_ear/feedback_fixes），不再依赖临时 scratchpad。

---

## 1. 创新点台账（定义 / 改在哪 / 状态）

四个创新点全部遵守**铁律**：不训练分类器、单摄像头、配置驱动、中文注释。
即"仍是可解释的加权融合，只把固定权重升级为自适应"，不改任务书要求的基础模型形态。

### 创新① 质量感知自适应融合（最核心）
- **定义**：各子分（eye/mouth/head/physio）的融合权重 = 固定权重 × **实时可靠度**，
  再归一化。头转越大→眼部可靠度越低；人脸检出占比越低→整体降权；生理无 HR→降权。
  克服固定权重"某模态失效时仍被采信"的不鲁棒问题。
- **改在哪**：
  - `core/fusion.py :: reliabilities(wf, cfg)`（135 行）——产出
    `{"eye": face_ratio*(1-yaw_pen), "mouth": face_ratio, "head": face_ratio, "physio": 1.0}`，
    其中 `yaw_pen = min(yaw_penalty_cap, mean_abs_yaw/yaw_reliability_deg)`。
  - `core/fusion.py :: fuse(sub, weights, reliabilities=None)`（105 行）——
    `eff = w×rel; score = Σ(eff×s)/Σeff`。
  - `core/fusion.py :: evaluate()`（248 行）——调用 `reliabilities()` 并传入 `fuse()`；
    结果写入 `FatigueResult.reliabilities`。
  - 数据结构：`core/types.py` WindowFeatures 新增 `face_ratio`、`mean_abs_yaw`；
    FatigueResult 新增 `reliabilities`。
  - 特征来源：`core/feature_window.py :: _compute_signal_quality()`（280 行）
    产出 `face_ratio`、`mean_abs_yaw`。
  - 配置：`config.yaml` `fusion.subscore.yaw_reliability_deg=45`、`yaw_penalty_cap=0.6`。
  - UI：监测表新增"信号质量"行（`ui/plot_widget.py`）。
- **状态**：✅ 已完成（单测：yaw 大 → eye 可靠度 0.40 vs 正常 1.00）。

### 创新② 眨眼动力学特征
- **定义**：在 PERCLOS 之上补充**平均眨眼时长**与**微睡眠**（连续闭眼 >0.5s）计数——
  文献指出平均眨眼时长是微睡眠的最佳判别指标。纳入眼部子分与显示。
- **改在哪**：
  - `core/feature_window.py :: _compute_blink_dynamics()`（249 行）——
    返回 `(avg_blink_dur, microsleep_count)`，闭眼段 ≥ `microsleep_dur_sec` 记一次微睡眠。
  - `core/feature_window.py :: _compute_current_closed()`（229 行）——正在进行的连续
    闭眼时长（供 §创新③/硬规则微睡眠直接报警）。
  - `core/fusion.py :: eye_subscore()`（41 行）——改为
    `max(perclos_s, closed_s, blink_s, micro_s, dur_s)`，新增 micro_s、dur_s 两路。
  - 数据结构：WindowFeatures 新增 `avg_blink_dur`、`microsleep_count`、`current_closed_dur`。
  - 配置：`config.yaml` `eye.microsleep_dur_sec=0.5`、
    `fusion.subscore.microsleep_count_full=2`、`blink_dur_normal_sec=0.15`、`blink_dur_full_sec=0.5`；
    `fusion.microsleep_sec=2.0`（硬规则：正在进行闭眼≥2s → 直接重度+报警）。
  - UI：监测表新增"平均眨眼(s)""微睡眠(次)"两行。
- **状态**：✅ 已完成（单测：微睡眠/长眨眼 → 眼部子分 1.0）。

### 创新③ 置信度自适应决策窗口
- **定义**：报警防误报状态机根据**子分一致性**动态调节所需连续窗口数——多路子分同时
  报高（确定）→ 缩短窗口、报警更快；子分矛盾（模糊）→ 维持长窗口防误报。
- **改在哪**：
  - `core/fusion.py :: AlarmFSM.update(level, score, agreement=0)`（200 行）——
    `agreement >= agreement_for_confident` 时 `n_alarm = max(1, n_alarm - reduction)`。
  - `core/fusion.py :: evaluate()`——`agreement = Σ(1 for s in sub if s>=moderate_th)`，传入 update。
  - 配置：`config.yaml` `fusion.adaptive_alarm_reduction=2`、`fusion.agreement_for_confident=2`。
- **状态**：✅ 已完成（单测：一致高分 → 更快报警 [True,True]；弱信号 → [False,False]）。

### 创新④ KSS 嗜睡量表对齐（借鉴师兄）
- **定义**：融合分 S(0..1) → 国际公认 **Karolinska 嗜睡量表 KSS 1..9**
  （1 极清醒…9 极困），给四级判定科学背书；等级区与监测表并列显示。
- **改在哪**：
  - `core/fusion.py :: score_to_kss(score)`（290 行）——
    `int(clamp(round(1 + clamp01(score)*8), 1, 9))`。
  - `core/fusion.py :: evaluate()`——`FatigueResult.kss = score_to_kss(score)`。
  - 数据结构：FatigueResult 新增 `kss`（默认 1）。
  - UI：`ui/panels.py` 等级区显示"融合分 x.xxx · KSS n/9"；监测表新增"KSS 嗜睡度"行。
- **状态**：✅ 已完成（单测：S=0→KSS1，S=0.5→KSS5，S=1.0→KSS9）。

---

## 2. 任务书硬性要求（严格遵守，不可违背）

- **单摄像头**、普通 PC 摄像头，无额外硬件传感器。
- **实时**运行（GUI 逐帧刷新）。
- 基础判定：**阈值判断 + 加权融合**，划分**清醒/轻度/中度/重度四级**。
  → 创新①仅把"固定权重"升级为"自适应权重"，仍是可解释加权融合，**不改这一形态**。
- **防误报策略**：连续帧/滑窗/评分平滑/多条件之一以上。→ 创新③是其自适应升级。
- **六区界面**：视频区 / 实时指标区 / 疲劳等级区 / 检测记录(LOG)区 / 控制区 / 报警区。
  → KSS 并入疲劳等级区，不新增分区、不破坏布局。
- 三点延伸方向（体现创新意识）：①个性化基线（已做，30s 校准）；②多模态融合
  （眼+嘴+头+rPPG 生理，已做）；③传统 vs 深度对比 / 极简实时化（本系统定位）。
- 不强制公开数据集；用 CEW/YawDD 验证属**加分项**。
- 全中文注释、面向报告与答辩。

---

## 3. 已排除的方案及理由（避免重复踩坑）

| 排除项 | 理由 |
|---|---|
| **KAN（Kolmogorov-Arnold 网络）三级分类**（师兄创新点） | 需训练，违反"不训练分类器"铁律 → 列为**未来工作** |
| **面部动作单元 AU（AU43/45 等）** | 需额外训练模型/OpenFace，超出单摄轻量范围 → **未来工作** |
| **压力坐垫 / 智能手环多传感器**（师兄三模态） | 需额外硬件，非单摄像头 → **未来工作/展望** |
| **MRL Eye 数据集**做整脸眨眼验证 | 该集是**裁剪眼部**图，非整脸，不匹配 FaceMesh 全脸流程 → 改用 **CEW（整脸睁/闭眼）** |
| `cap.set(CAP_PROP_FPS, 20)` 提帧 | 在 usbipd 转发的摄像头上会把帧率**压到 ~5fps** → 移除，改用独立采集线程 |
| PyInstaller 本地交叉编译 Windows exe | Linux 不能交叉编译 → 用 **GitHub Actions windows-latest** 构建 |
| 用中文/非 ASCII 名字的 exe 或中文路径 | mediapipe 无法打开非 ASCII 路径 → **ASCII exe 名 + 模型复制到 C:\Users\Public** |
| 生理子分**硬依赖**已校准静息心率 | 未校准时永远显示"-" → 增加**自动静息 HR 中位数兜底**（`ui/main_window.py`） |

---

## 4. 论文/数据集引用（写报告直接用）

**创新①（质量感知/置信度自适应融合）**
- Frontiers in Neurorobotics 2026 —— confidence-driven adaptive fusion。
- arXiv 2606.26473 —— quality-aware multimodal fusion。
- TMU-Net（uncertainty-aware），PMC12431429。

**创新②（眨眼动力学 / 微睡眠）**
- Oxford *SLEEP Advances* 2023 —— PERCLOS 及眨眼时长综述；平均眨眼时长为微睡眠最佳判别指标。
- PMC10108649 —— 眨眼动力学与困倦。
- Soukupová & Čech 2016 —— EAR 眨眼检测原始方法。
- PeerJ 2022 —— 个性化 EAR 阈值（睁/闭中点）。

**创新③（置信度自适应时间窗）**
- Frontiers in Neurorobotics 2026 —— 确定则决策快、模糊则窗口长。

**创新④（KSS 嗜睡量表）**
- Karolinska Sleepiness Scale（Åkerstedt & Gillberg 1990），1..9。
- Nature *Scientific Reports* 2025，PMC11985911 —— 模型输出映射 KSS，alert(1-6)/sleepy(7-9)。

**师兄论文（对照 / 未来工作依据）**
- 邹秉航《面向工业环境的无干扰式多模态人员疲劳检测系统》，四川大学硕士 2026。
  面部视频+压力坐垫+手环三模态；AU/运动频率/rPPG；极端随机树选特征；多分支网络+logit
  决策融合（二分类 93%）；KSS+Oddball 标注；KAN 三级分类。**我们部分继承其决策融合+分级
  +KSS 思路并轻量化，重型部分列未来工作。**

**数据集**
- CEW（Closed Eyes in the Wild，整脸睁/闭眼）：HF `MichalMlodawski/closed-open-eyes`，
  512×512 整脸 parquet，放 `data/CEW/`；文件 001–7xx=闭眼，8xx–1000=睁眼。眼部验证 96.6%。
- YawDD（哈欠）：HF `linoyts/wan_yawning` mp4，放 `data/yawn/`。

---

## 5. 下一步 TODO（按顺序）

1. [x] **回归全绿**（2026-07-13）：M3 融合 25 项、M4 rPPG 12 项（真实视频 MAE 4.5bpm）、
       低帧率、弱 EAR、组员反馈 10 项、CEW 96.6%、创新点单测 11 项——全部通过，
       可靠度/一致性/KSS 未破坏既有用例。脚本在 `dev_tools/verify_*.py`。
2. [x] **selftest + 离屏截图**（2026-07-13）：selftest 干净退出(exit 0)；截图确认
       等级区"融合分 x.xxx · KSS n/9"、监测表新增 4 行（平均眨眼/微睡眠/信号质量/KSS）
       显示正常且着色正确、图表标题等文字无裁切。
3. [x] **补文档**（2026-07-13）：`创新点调研与实现方案.md`"实现记录"节已补全；
       `开发进度与问题交接.md` 新增 §5.7；记忆文件已更新。
4. [x] **M6 交付物补齐**（2026-07-13，全仓库对照规格书扫描后补）：
   - `参数设置说明.md`（任务书交付物，修复 运行说明.md 的断引用）；
   - `测试指南.md`（批量回放按用户决定交给测试组员手工做，文档教他们
     6 场景怎么测、CSV 指标怎么统计）；
   - `README.md`（规格书 §4 目录树要求）；
   - **GUI「参数设置」面板**（任务书"鼓励增加参数设置"拓展，至此
     曲线/历史/参数面板三件拓展齐了）：`ui/settings_dialog.py` 新建，
     11 个常用参数，「应用」热更新（`AlarmFSM.reconfigure` /
     `FeatureAggregator.reconfigure`，不清滑窗不丢报警状态）、
     「应用并保存」行级写回 config.yaml **保留全部中文注释**；
     单测 `dev_tools/verify_settings.py` 14 项全绿，受影响回归重跑全绿。
5. [x] **同步发布目录 + 提交 & 打 tag**（2026-07-13）：改动已同步并分两次提交
       （dac857c 创新点 / 1caeb3a M6 补齐），CI 两次全绿，`v1.8` tag 已发
       Release（FatigueDetection-Windows.zip 212.7MB）。**真机验证**：exe 在
       `C:\Users\风涌云起\Downloads\疲劳检测v1.8测试\`（用户名+目录双中文路径）
       经 WSL interop 实跑 `--selftest 6`，mediapipe 模型加载成功、exit 0——
       中文路径修复未回退。
6. [ ] 报告"创新意识"章节：按 §1/§4 组织；"未来工作"按 §3 组织（AU / KAN / 多传感器）。

---

## 6. 状态一览表

| 项 | 状态 |
|---|---|
| 创新① 质量感知融合 | ✅ 代码+单测完成，UI 已接 |
| 创新② 眨眼动力学 | ✅ 代码+单测完成，UI 已接 |
| 创新③ 自适应决策窗 | ✅ 代码+单测完成 |
| 创新④ KSS 对齐 | ✅ 代码+单测完成，UI 已接 |
| 全回归验证 | ✅ 7 项全绿（2026-07-13） |
| UI 截图确认 | ✅ 通过（2026-07-13） |
| 文档补记（实现记录/交接） | ✅ 已补（2026-07-13） |
| M6 交付物补齐（参数说明/测试指南/README/参数面板） | ✅ 已补（2026-07-13） |
| 同步 FatigueDetectionSystem/ | ✅ 已同步（2026-07-13） |
| 提交/打 tag/Release | ✅ v1.8 已发布，exe 真机中文路径验证通过（2026-07-13） |
