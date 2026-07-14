# PROGRESS —— 疲劳检测系统进度与决策台账

> 本文件是**权威进度台账**（single source of truth）。任何接手都先读这里。
> 最近更新：2026-07-14。配套文档：
> `创新点调研与实现方案.md`（调研/论文）、`眼部检测改进日志.md`（眼部）、
> `组员反馈处理记录.md`（组员反馈）、`开发进度与问题交接.md`（总交接）。

---

## 0. 一句话现状

**v1.9 第二轮组员反馈修复完成**（2026-07-14）：低头场景三层修复（深低头失真门控/
pitch 纳入创新①可靠度/持续低头 8s 硬规则）、校准升级眨眼谷底锚点（组员"下三分之一"
建议，带兜底）、回退阈值 0.21→0.15（CEW 实测依据）、CSV 补 4 个创新指标列。
新单测 `verify_round2_fixes.py` 25 项 + 既有全回归 7 套全绿。详见
`组员反馈处理记录.md` 第二轮 ⑧⑨⑩。此前 v1.8（2026-07-13）：四创新点+M6 交付物
+Release 真机中文路径验证。剩余工作只有**写报告**（§5 第 6 条）。
回归/单测脚本持久化在 `dev_tools/verify_*.py`，不再依赖临时 scratchpad。

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
7. [x] **第二轮组员反馈修复 v1.9**（2026-07-14）：⑧低头场景（`eye_valid_pitch_deg`
       失真门控 + `pitch_reliability_deg/pitch_penalty_cap` 纳入创新① +
       `head_down_sec` 持续低头硬规则，参数面板加"持续低头直接报警"）；
       ⑨校准眨眼谷底锚点（`blink_anchor_*` 五参数，阈值取组员建议的下三分之一，
       无锚点退回 k×std）+ 回退阈 0.21→0.15（CEW 实测：误报瘫痪人群 3%→1%）；
       ⑩CSV 尾部追加 avg_blink_dur/microsleep_count/face_ratio/kss 四列。
       单测 `dev_tools/verify_round2_fixes.py` 25 项全绿、既有回归 7 套全绿。
       已推 main（b82d5fb，CI 绿）；**用户拍板暂不打 tag**——等 UI 高级化改版
       （用户提供样图+design.md）完成后一起发 v1.9。
8. [ ] **UI 高级化改版**（2026-07-14 用户发起，**进行中**）：
   - **设计规范：`EXPWORK/DESIGN.md`（UI 唯一事实来源，苹果风浅色极简）+ 两张样图**
     （主界面：白底卡片布局/顶部工具栏/右侧等级卡/底部曲线+指标列表；设置面板：
     overlay 弹层）。§10 分 7 阶段实施，**每阶段完成后必须停下等用户截图确认**。
   - **阶段进度（断点续跑看这里）**：
     1. [x] 主题层（2026-07-14 完成，**等用户截图确认**）：`ui/theme.py` 全量重写
        （保留旧常量名映射到浅色新值+新增 §2-§4/§7.0 全部常量）；顺带把三处写死
        的深色接到常量：`plot_widget.py` 画布底→SURFACE、`panels.py` 报警横幅三态
        →浅色状态色、`video_widget.py` 容器→VIDEO_BG(#1A1A1C 深色特例)。
        预览图：`fatigue_system/outputs/ui_preview/phase1_{idle,state,settings}.png`
        （离屏截图脚本在会话 scratchpad shot_phase1.py，丢了可照 PROGRESS 重写）。
        已验证：截图渲染正常、无崩溃。旧布局元素（FM logo/英文副标题/底部按钮排/
        chips/表格/旧设置对话框）属阶段 2-5/7 范围，本阶段刻意未动。
     2. [x] 顶部工具栏（2026-07-14 完成，**等用户截图确认**）：`panels.py` ControlPanel
        重写为 §5.1 工具栏（应用名/合并源下拉[摄像头N/文件/打开文件…/刷新]/绿点fps/
        校准·记录·设置三个 QPainter 手绘线性图标按钮/唯一实心主按钮 开始监测(蓝)↔
        停止监测(红)，新增 start_requested 信号+set_running/set_fps/current_source）；
        `widgets.py` 新增 IconButton/StatusDot；`main_window.py` 删旧头栏(FM logo/
        英文副标题/StatusPill)与底部按钮排、接 _on_start、状态行改 §5.5 格式；
        theme.py 补 appTitle/statusDot/iconbtn QSS。"关键点"开关按 §5.2 待阶段5
        移入视频角（期间默认开启无开关）。预览：ui_preview/phase2_running.png
        （真实视频全流程 29fps 验证）+ phase2_state.png。selftest exit 0。
     3. [x] 右侧信息栏（2026-07-14 完成，**等用户截图确认**）：LevelPanel 重写为
        §5.3 等级卡（小标签/30px 等宽大数字融合分/LEVEL_BADGES 胶囊/KSS 12px 灰/
        眼部·嘴部·头部·生理 四行 ThinBar 4px 细进度条——超过 fusion.moderate 阈值
        才变橙，构造签名改为 LevelPanel(cfg, parent)）；AlarmPanel 改单行状态卡
        （圆点+同色文字 | 右侧"累计 N 次"，声音/弹窗/热更新逻辑原样保留）；
        widgets.py 新增 ThinBar；右列布局=等级卡撑满+报警单行。
        预览：ui_preview/phase3_running.png。selftest exit 0。
     4. [x] 指标监测区（2026-07-14 完成，**等用户截图确认**）：`plot_widget.py` 全量
        重写——左曲线卡（标题"某指标 · 近 60 秒"；曲线统一强调蓝 2px+8% 填充；
        仅融合分画三条阈值虚线并右端标"轻/中/重 x.xx"（读 config，调参即时反映）；
        x 轴只标 -60s/现在；历史缓存改 (ts,val) 真实 60s 时间窗）+ 右指标列表卡
        （MetricRow 行=名称+当前值，点击可切曲线、选中行浅蓝圆角；常用 5 项+
        "更多指标"手绘 chevron 折叠 11 项；替代旧 10 颗 chip+QTableWidget）。
        列表卡宽度 400-480 与右上信息栏对齐。基线文案按 §5.5 移入底部状态行
        （详细基线数据放悬停提示），MonitorPanel 签名改 (cfg, parent)、
        set_baseline_text 移除。预览：ui_preview/phase4_running.png。selftest exit 0。
        **4b 布局微调（用户反馈：等级卡留白大、窗口要方）**：等级卡四行分量改
        弹性均匀分布（行间 stretch，高卡不留死白）；默认窗口 1500×1000→1280×1120
        （样图方形比例，min 1000×860）；上下两段 3:2→11:9；视频区最小尺寸
        640×480→480×360。预览：ui_preview/phase4b_running.png。
        **4c 尺度放大（用户反馈：比样图小一号；DESIGN.md 与样图冲突→以样图为准，
        决定已写回 DESIGN.md §0.2）**：全局 13→14px、工具栏 52→64、应用名 16px、
        主按钮 15px、大数字 30→38px、badge/分量 13px、分量条 4→6px、卡片内边距
        16→20、列表行 38→44px、图标钮 32→36。预览：ui_preview/phase4c_running.png。
     5. [x] 视频区浮层（2026-07-14 完成，**等用户截图确认**）：`video_widget.py`
        重写——删左上绿字 ASCII 调试框（draw_hud 保留但不再调用，dev_tools 兼容）；
        新增 _OverlayPill（§2.4 半透明胶囊：右上"人脸 正常/丢失"绿/深两态、左下
        "EAR·MAR·姿态"数值）与 _LandmarkToggle（右下 30px 圆钮 3×3 点阵，切换
        关键点叠加，VideoWidget.landmarks_toggled 信号）；浮层贴角自适应、无画面
        自动隐藏。ControlPanel 的过渡 landmarks_toggled 信号删除。
        预览：ui_preview/phase5_running.png。selftest exit 0。
        **5b/5c 顶栏两轮放大（用户反馈"改得太保守"，样图右侧集群≈顶栏宽 40%）**：
        最终 工具栏 88 高、应用名 20px、图标钮 48px(半径12/线宽2.0)、主按钮
        17px(12×28)、下拉 16px、状态字 16px、间距 18；DESIGN.md §0.2 已同步。
        预览：ui_preview/phase5c_running.png。
        **5d 指标列表填满（用户反馈：卡片留白多就多放指标）**：常用区 5→8 项，
        提升 最长闭眼/微睡眠(创新②)/KSS(创新④) 出折叠区，正好铺满卡片。
        预览：ui_preview/phase5d_running.png。
     6. [x] 动画层（2026-07-14 定时接力完成，**等用户确认**）：新增 `ui/anim.py`
        （QVariantAnimation 统一封装，OutCubic/InCubic）。已实现：图标钮悬停底色
        150ms+按下图标缩 0.92（手绘，QSS 悬停规则移除）；指标行悬停 150ms/选中
        底色+文字色 180ms 三项同步；曲线切换交叉淡化 220ms（TimeSeriesChart 持旧
        曲线快照按 _fade 双绘，阈值线随淡入淡出）；ThinBar 数值 200ms 平滑推移+
        灰↔橙颜色插值；报警行出现=淡入+上移4px 220ms、解除=红→绿颜色过渡 250ms
        （人脸丢失同套处理）；等级 badge 变级颜色插值 250ms。**两处按规范取舍**：
        融合分大数字不加动画（§7.0纪律3/§7.3 明文：数据流更新不动画，台账旧指令
        与此冲突以 DESIGN.md 为准）；badge 重度脉冲放大未做（§7.3"允许"非必须，
        QLabel 缩放会引起邻元素跳动）。数据流刷新（视频帧/曲线追加/数值行）一律
        无动画（§7.8）。
     7. [x] 设置面板（2026-07-14 定时接力完成，**等用户确认**）：settings_dialog.py
        重构为主窗口内 overlay——压暗层 rgba(0,0,0,0.28)+380px 白面板 14px 圆角+
        手绘软阴影（注意：外层已挂透明度效果，Qt 不支持嵌套 QGraphicsEffect，
        DropShadow 会把子树渲染成空白，故阴影手绘；面板/分组 QSS 必须用
        objectName 限定——QLabel 继承 QFrame，裸 QFrame 选择器会给所有标签套框）。
        打开=淡入+0.95→1.00 220ms，关闭=淡出+缩回0.97 150ms InCubic；点压暗区/
        Esc=取消；跟随主窗口缩放。内容 6 项（用户定稿）：疲劳阈值组 轻/中/重
        （mild<moderate<severe 校验保留）+ 报警组 报警声音/报警弹窗/视频循环播放
        （widgets.py 新增 Switch：48×28 全圆轨道，§7.6 滑块+轨道色 180ms 同步）。
        取消(描边)+完成(蓝实心=应用+写回)。rewrite_yaml_values 行级保注释写回与
        applied→_apply_runtime_config 热更新链路原样保留；IconButton 新增 close
        图标。verify_settings.py 字段断言按 6 项更新（14 项全绿）。
        **验证汇总**：selftest exit 0；verify_round2_fixes 25 项全绿；预览
        ui_preview/phase6_running.png、phase7_settings.png（与样图二一致）。
        **未做任何 push/tag/发布目录同步——等用户确认后统一处理。**
   - **【给接力会话的执行指令（2026-07-14 用户授权：额度中断，4h 后自动续跑，
     做完 6+7 停下等用户确认；期间禁止 push/tag/同步发布目录）】**：
     1) 先读 EXPWORK/DESIGN.md（§7 动画规范 + §7.7/§8 设置面板）；UI 代码在
        fatigue_system/ui/（theme.py 常量含 ANIM_FAST/BASE/SLOW；样式全走 theme）。
     2) **阶段6 动画**（不改布局）：a. 按钮/列表行悬停底色过渡 150ms OutCubic
        （QVariantAnimation 颜色插值；QSS 无 transition）；按下 scale 0.98。
        b. 指标切换（plot_widget.MetricRow.clicked→MonitorPanel._select）：曲线
        交叉淡化 220ms（方案A：旧新两条曲线透明度互换，自绘 Chart 可在 paint 时
        按 progress 混合两组点）+ 选中行底色/文字色过渡 180ms + 大数字滚动插值
        220ms（LevelPanel._score 数值插值）。c. ThinBar 数值/灰橙颜色 200ms 插值
        （widgets.ThinBar 内加 QVariantAnimation）。d. AlarmPanel 状态行：报警出现
        淡入+上移4px 220ms，解除仅颜色过渡 250ms。e. LevelPanel badge 颜色插值
        250ms；进入重度时 badge 一次性 scale 1.0→1.06→1.0 300ms（禁循环闪烁）。
        禁止：>300ms、Linear、弹跳、给视频帧/曲线实时追加加动画（§7.8）。
     3) **阶段7 设置面板**：settings_dialog.py 重构为主窗口内 overlay（压暗层
        rgba(0,0,0,0.28) + 340px 白面板 14px 圆角 + 阴影）；打开 220ms
        透明度0→1+缩放0.95→1.00（geometry 动画），关闭 150ms 反向到 0.97；
        点压暗区/Esc=取消。**内容 6 项（用户定稿）**：疲劳阈值组(轻/中/重,
        mild<moderate<severe 校验保留) + 报警组(报警声音/报警弹窗 Switch) +
        其他(视频循环播放 Switch)；Switch 按 §6（36×22 全圆轨道，开启 #34C759，
        滑块位移 180ms）。底部 取消(描边)+完成(蓝实心,应用并写回)。
        **保留 rewrite_yaml_values 行级写回与 MainWindow._apply_runtime_config
        热更新链路**（settings_dialog 的 applied 信号）。尺寸参照 §0.2 校准值
        （样图优先，别做小）。
     4) 验证：QT_QPA_PLATFORM=offscreen python -m fatigue_system.app --selftest 6
        （项目根跑，conda env rppg-toolbox）exit 0；离屏截图脚本参考台账阶段1-5
        （用 data/Test/subject1/vid.avi 开源跑 6s grab 保存到
        fatigue_system/outputs/ui_preview/phase6_running.png / phase7_settings.png）；
        跑 dev_tools/verify_settings.py（改了 settings_dialog 后必须绿，若字段
        清单变了按新 6 项修断言并在处理记录注明）+ verify_round2_fixes.py。
     5) 完成后：更新本清单 6/7 为 [x]（写明改动文件/预览图路径/验证结果），
        **停止，等用户确认**。不 push、不 tag、不动 FatigueDetectionSystem/。
   - **参数面板最终定稿（用户 2026-07-14 拍板，覆盖 DESIGN.md §8 的"判定参数"组）**：
     共 6 项 = 疲劳阈值(轻/中/重) + 报警声音 + 报警弹窗 + 视频循环播放，与样图二
     一致。任务书核实：面板属"鼓励"拓展非必备；交付物《参数设置说明.md》文档保留。
   - **启动画面+延迟导入**（随本轮一起做）：exe 每次启动 1~2 分钟＝Defender 实时
     扫描无签名包（老师会亲自开 exe，不能让他关 Defender）。方案：PyInstaller
     pyi_splash（bootloader 级秒出图）+ 应用内加载页，重库导入推迟。onedir 已确认。
   - 全部完成并经用户确认后才发 v1.9（连同已保留的第二轮反馈三项修复）。

---

### 5.12【✅ 2026-07-15 v1.12】用户实机反馈的 6 项 UI/性能问题

1. **曲线 y 轴数字显示不全**（Win 上被裁成"90 73"）：刻度原来画在绘图区内、贴边。
   改为绘制在左侧留白区（PAD_L=54）内右对齐，刻度格式按量级自适应（≥100 取整、
   ≥10 一位小数、否则两位），x 轴标签同样给足底部空间（PAD_B=26）。
2. **"更多指标"改为换页**（原来是展开、把卡片撑长）：`_PagerRow` 在"更多指标 ⌄"与
   "返回常用指标 ⌃"间切换，两页各 8 项，卡片高度不变。
3. **曲线切换动画改为 §7.2 方案B（逐点插值变形）**：`TimeSeriesChart` 把序列重采样到
   _GRID_N=300 的均匀网格并归一化，切换时对**同一批 x 上的新旧 y 逐点插值**
   （y = 旧 + (新−旧)×ease(t)），曲线真实"变形"过去而非透明度叠加；y 轴刻度范围
   同步插值、阈值线随进度淡入淡出。列表选中态 180→220ms，与曲线变形、新增的
   **卡片当前值大数字滚动**同为 ANIM_BASE，三者同起同止。
4. **曲线僵硬呈阶梯**：根因是 PERCLOS/心率/融合分等**每秒才更新一次**，却每帧都写
   一个点 → 原样画出来就是台阶。绘制前对"慢指标"做 1.5s 滑动平均 + Catmull-Rom
   样条平滑；EAR/MAR 是逐帧量，**不平滑**（保留眨眼尖峰，已截图验证）。
   每个指标的 smooth_sec 在 `Metric` 里声明。
5. **exe 启动要等 1~2 分钟 / 点开始监测卡"未响应"**：
   - `ui/loading.py` 新增「载入中」窗口：**先出窗口再 import 重库**（cv2/mediapipe），
     显示当前步骤 + 不确定进度条 + "首次启动约需 1~2 分钟（Windows 安全中心正在
     扫描未签名程序）"。Defender 扫描无法消除，但不能让用户面对空白以为程序挂了。
   - `FaceMeshDetector.warmup()` + `MainWindow.warmup()`：加载阶段先跑一次空推理，
     把 mediapipe **首次推理建计算图的几秒卡顿**挪到"载入中"画面期间——否则它会
     发生在用户点「开始监测」的瞬间，界面直接未响应。
   - 打开摄像头/视频前先 `_busy()` 显示"正在打开…"并 processEvents。
6. **「打开视频文件」独立成按钮**（放在摄像头下拉旁，描边样式），不再藏在下拉项里。
- 验证：全回归 9 套全绿；selftest exit 0；截图 `ui_preview/chart_hr.png`（平滑无台阶）、
  `chart_ear.png`（眨眼细节保留）、`v112_page2.png`（换页）、`v112_loading.png`。

### 5.11【✅ 2026-07-15 v1.11】老师建议的三个新功能（**等用户确认**）
1. [x] **会话报告**（`io/session_report.py`）：停止记录时**自动生成单文件 HTML**
   报告并提示路径（历史面板里也可对任意历史会话补生成）。含：会话概览（时长/报警
   次数/平均 KSS/记录行数）、**疲劳等级时间线色带**、各等级时长占比、融合分曲线
   （带三条阈值虚线）、PERCLOS/心率曲线、报警时刻、基线与关键阈值摘要。
   曲线为**内联 SVG 手绘**——单文件、零 JS/CSS/网络依赖，双击即开、可直接插进课程
   报告。数据源＝已落盘的明细 CSV（不重跑检测）。配色取自 theme.py。
2. [x] **历史会话回看**（`ui/history_dialog.py`）：顶栏新增「历史」按钮（时钟图标）→
   overlay 面板：左侧列出 outputs/ 下历次会话（按时间倒序），选中即显示概览 +
   等级时间线色带 + 融合分曲线（**读 CSV 重绘，不重跑视频、不占摄像头、不打断当前
   监测**），一键「导出报告」并用系统默认程序打开（Windows/WSL/Linux 三种打开方式）。
3. [x] **疲劳趋势预警**（`core/trend.py` + `config.yaml` trend 段）：对融合分做滑窗
   **最小二乘线性回归**，斜率 ≥ slope_per_min(0.05/分) 且分数已离开清醒噪声区
   (min_score 0.20) 且连续 hold_windows(3) 满足 → 在预警区给一行温和提示
   "疲劳正在累积（评分持续上升 x.xx/分），建议休息"。**它不是报警**：不走 AlarmFSM、
   不响铃、不弹窗、不计入报警次数，且有 5 分钟冷却——刻意设计成不会变成新的误报源。
   体现任务书要求的"预**警**"（事前）而非"报警"（事后）。
- 验证：`dev_tools/verify_v111_features.py` **18 项全绿**（含"清醒平稳不提示"、
  "低分噪声区上升不提示"、"报告是单文件无外部依赖"、"历史面板统计正确"等）；
  全回归 **10 套全绿** + selftest exit 0。
- 预览：`ui_preview/v111_toolbar.png`（校准/记录/历史/设置 四个带标签按钮）、
  `v111_history.png`（历史面板）；示例报告已生成到 Windows 桌面 `会话报告示例.html`。

### 5.10【✅ 2026-07-15 v1.10】老师反馈：办公场景被连报几十次（**误报根因修复**）

**现象**：老师打开软件后最小化、自己在电脑前干活，被报警三四十次。
**复现**（`dev_tools/verify_false_alarm_fix.py` 场景 A1，10 分钟办公+每 30s 低头看
键盘 8s）：**修复前报警 20 次**（首次 t=8s），重度窗口占比 22%。

**根因（两条硬规则 + 一个设计缺口）**：
1. **低头看键盘时 EAR 被"下视"压低**（实测 0.13~0.14 vs 未校准阈值 0.15）→ 被当成
   闭眼 → **微睡眠硬规则 2 秒就直接判重度报警**；
2. **持续低头 8s 硬规则**：看键盘/看资料 8 秒是日常动作，必然误报；
3. **没有"多特征同时满足"的要求**（正是老师指出的）：单一通道就能顶到重度。
   更隐蔽的是——低头会**同时**压低 EAR 并触发低头，看似"眼+头两个特征互相印证"，
   实为**同一动作的产物**，是假的相互印证。

**修复（六条，全部配置驱动）**：
1. `eye.eye_valid_pitch_deg` 30→**15**：低头超阈时眼部 EAR 判为不可信，不进任何眼部
   统计（下视压低 EAR 是注视方向的产物，不是困倦）。
2. `head.auto_neutral_pitch`（新）：未校准时自动估计俯仰零点＝缓冲内 **25 分位数**
   （solvePnP 正视时 pitch≈+14° 而非 0，因人而异；中位数在"低头占比近半"时会被抬高
   → 实测启动 12s 误报一次）；并**单调不上升**（直立是 pitch 下界，长时间埋头不得把
   零点带偏——否则真趴睡的人趴一会儿后系统反以为坐得很正，实测 PERCLOS 0→0.79）。
3. `eye.deep_closed_ratio` / `deep_closed_margin_ratio`（新）+ `WindowFeatures.
   current_deep_closed_dur`：微睡眠硬规则改用**深度闭眼阈**（真闭眼 EAR≈0.078 ≪
   下视压低的 0.13）。**不依赖任何姿态推断**，即使"开机时就低着头"导致零点被带偏
   也不会误报（这是第二道独立保险）。
4. `fusion.microsleep_min_eye_reliability`（新，0.6）：微睡眠硬规则要求眼部实时可靠度
   达标（低头/侧脸/丢脸时不认）。
5. `fusion.head_down_sec` 8→**20**：低头看东西是日常，8 秒太短；真埋头打瞌睡会持续更久。
6. **多特征证据门**（老师建议的核心）`fusion.evidence_subscore_thresh`(0.5) +
   `severe_min_channels`(2)：进入"重度区"至少需 2 个**独立**模态各自给出证据，否则
   融合分封顶在重度线之下（最高只到"中度"预警，不报警）。
   \+ `alarm.cooldown_sec`(30)：报警解除后冷却，防止分数在阈值上下抖动来回触发。

**验证（双向，缺一不可）** `dev_tools/verify_false_alarm_fix.py` 13 项全绿：
- 不该报的不报：办公+低头看键盘 10min **0 次**（原 20 次）、连续低头 15s 写字 0 次、
  说话 5min 0 次；
- 该报的照报：真微睡眠（正常坐姿持续闭眼）**6s 报警**、真综合疲劳（眼+嘴+头）报警、
  真趴睡（持续埋头 >20s）**50s 报警**、冷却期生效。
- 全回归 8 套全绿。**两个旧断言按新语义重写**（`verify_m3_fusion` ⑤"低头+EAR降→中度"
  正是老师投诉的误报机制，改为"低头前 20s 不报警 + 眼部判不可信 + 持续 20s 才由硬
  规则判重度"；`verify_round2_fixes` ⑧ 硬规则阈 8s→20s 相应放宽）。

**UI**：顶栏三个图标按钮加文字标签（校准/记录/设置，老师建议——纯图标看不出功能）。

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
| 第二轮组员反馈修复（低头/校准锚点/CSV） | ✅ v1.9 代码+文档+25 项单测（2026-07-14） |
