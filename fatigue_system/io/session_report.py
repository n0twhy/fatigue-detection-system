# -*- coding: utf-8 -*-
"""会话报告（v1.11 功能①）：把一次检测会话导出成**单文件 HTML** 报告。

内容：会话概览（时长/平均帧率/报警次数/平均 KSS）、疲劳等级时间线色带、
各等级时长占比、报警时刻列表、融合分与关键指标曲线、基线与关键参数摘要。

实现要点：
  * **数据源就是已落盘的明细 CSV**（io/data_logger.py 的 24 列），不重跑检测、
    不依赖摄像头——所以历史会话也能随时补生成报告（见 ui/history_dialog.py）。
  * 曲线用**内联 SVG** 手绘：单文件、不依赖任何 JS 库/网络（老师双击即可打开，
    也便于插进课程报告）。
  * 样式取自 ui/theme.py 的同一套色板，与软件界面观感一致（无魔法色值）。
"""

import csv
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from fatigue_system.core.types import LEVEL_NAMES
from fatigue_system.ui import theme

# 等级对应的展示色（与界面 badge 同一套语义色）
_LEVEL_COLORS = (theme.GREEN, "#E9B949", theme.ORANGE, theme.RED)


def _f(row: Dict[str, str], key: str) -> Optional[float]:
    """从 CSV 行取浮点；空串/缺列 → None。"""
    v = (row.get(key) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def read_session(csv_path: str) -> List[Dict[str, str]]:
    """读取明细 CSV（utf-8-sig，Excel 兼容）。"""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _svg_line_chart(points: List[Tuple[float, float]], width: int, height: int,
                    yrange: Tuple[float, float], color: str,
                    thresholds: Optional[List[Tuple[float, str]]] = None) -> str:
    """把 (x, y) 序列画成内联 SVG 折线（含可选的水平阈值虚线）。"""
    if len(points) < 2:
        return '<div class="empty">数据点不足</div>'
    pad_l, pad_r, pad_t, pad_b = 44, 48, 10, 22
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    xs = [p[0] for p in points]
    x0, x1 = min(xs), max(xs)
    span_x = (x1 - x0) or 1.0
    y0, y1 = yrange
    span_y = (y1 - y0) or 1.0

    def sx(x):
        return pad_l + pw * (x - x0) / span_x

    def sy(y):
        return pad_t + ph * (1.0 - (max(y0, min(y1, y)) - y0) / span_y)

    poly = " ".join("{:.1f},{:.1f}".format(sx(x), sy(y)) for x, y in points)
    area = "{:.1f},{:.1f} ".format(sx(x0), pad_t + ph) + poly + " {:.1f},{:.1f}".format(
        sx(x1), pad_t + ph)
    parts = ['<svg viewBox="0 0 {} {}" class="chart">'.format(width, height)]
    if thresholds:
        for ty, label in thresholds:
            if not (y0 <= ty <= y1):
                continue
            yy = sy(ty)
            parts.append(
                '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" '
                'stroke="{}" stroke-dasharray="4 4" stroke-width="1"/>'.format(
                    pad_l, yy, pad_l + pw, yy, theme.BORDER))
            parts.append(
                '<text x="{:.1f}" y="{:.1f}" class="axis">{}</text>'.format(
                    pad_l + pw + 6, yy + 4, label))
    parts.append('<polygon points="{}" fill="{}" fill-opacity="0.08"/>'.format(area, color))
    parts.append(
        '<polyline points="{}" fill="none" stroke="{}" stroke-width="2" '
        'stroke-linejoin="round"/>'.format(poly, color))
    # y 轴两端刻度 + x 轴起止时间
    parts.append('<text x="4" y="{:.1f}" class="axis">{:.2f}</text>'.format(pad_t + 10, y1))
    parts.append('<text x="4" y="{:.1f}" class="axis">{:.2f}</text>'.format(pad_t + ph, y0))
    parts.append('<text x="{:.1f}" y="{}" class="axis">0s</text>'.format(pad_l, height - 6))
    parts.append(
        '<text x="{:.1f}" y="{}" class="axis" text-anchor="end">{:.0f}s</text>'.format(
            pad_l + pw, height - 6, x1))
    parts.append("</svg>")
    return "".join(parts)


def _level_timeline(rows: List[Dict[str, str]], width: int = 900, height: int = 26) -> str:
    """疲劳等级时间线：一条按时间着色的色带（一眼看清整场会话的疲劳走势）。"""
    ts = [_f(r, "timestamp") for r in rows]
    ts = [t for t in ts if t is not None]
    if len(ts) < 2:
        return '<div class="empty">数据点不足</div>'
    t0, t1 = ts[0], ts[-1]
    span = (t1 - t0) or 1.0
    parts = ['<svg viewBox="0 0 {} {}" class="timeline">'.format(width, height)]
    for i, row in enumerate(rows):
        t = _f(row, "timestamp")
        lv = _f(row, "level")
        if t is None or lv is None:
            continue
        nxt = _f(rows[i + 1], "timestamp") if i + 1 < len(rows) else t1
        x = width * (t - t0) / span
        w = max(1.0, width * ((nxt or t) - t) / span)
        parts.append('<rect x="{:.1f}" y="0" width="{:.1f}" height="{}" fill="{}"/>'.format(
            x, w, height, _LEVEL_COLORS[int(lv) % 4]))
    parts.append("</svg>")
    return "".join(parts)


def build_report(csv_path: str, out_path: Optional[str] = None,
                 cfg: Optional[Dict] = None, baseline_note: str = "") -> str:
    """由明细 CSV 生成单文件 HTML 报告，返回报告路径。

    参数:
        csv_path      —— 明细 CSV（fatigue_log_*.csv）。
        out_path      —— 输出 HTML；缺省则与 CSV 同目录同名（.html）。
        cfg           —— 完整配置（用于摘要关键阈值；可为 None）。
        baseline_note —— 基线状态文案（主窗口状态行同款；可为空）。
    """
    rows = read_session(csv_path)
    if not rows:
        raise ValueError("CSV 无数据行：{}".format(csv_path))
    out_path = out_path or os.path.splitext(csv_path)[0] + ".html"

    # ------- 统计 -------
    ts = [_f(r, "timestamp") for r in rows]
    ts = [t for t in ts if t is not None]
    total = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
    level_dur = [0.0] * len(LEVEL_NAMES)
    alarm_times: List[float] = []
    prev_alarm = False
    kss_vals: List[float] = []
    for i, row in enumerate(rows):
        t = _f(row, "timestamp")
        lv = _f(row, "level")
        if t is None or lv is None:
            continue
        nxt = _f(rows[i + 1], "timestamp") if i + 1 < len(rows) else t
        dt = max(0.0, (nxt or t) - t)
        if dt < 5.0:                      # 跳变（换源/seek）不计入
            level_dur[int(lv) % len(LEVEL_NAMES)] += dt
        alarm = bool(int(_f(row, "alarm") or 0))
        if alarm and not prev_alarm:
            alarm_times.append(t)
        prev_alarm = alarm
        k = _f(row, "kss")
        if k is not None:
            kss_vals.append(k)
    dur_sum = sum(level_dur) or 1.0
    avg_kss = sum(kss_vals) / len(kss_vals) if kss_vals else None

    # ------- 曲线 -------
    def series(col):
        out = []
        for r in rows:
            t, v = _f(r, "timestamp"), _f(r, col)
            if t is not None and v is not None:
                out.append((t, v))
        return out

    th = (cfg or {}).get("fusion", {}).get("level_thresholds", {})
    mild = float(th.get("mild", 0.25))
    moderate = float(th.get("moderate", 0.50))
    severe = float(th.get("severe", 0.70))
    chart_score = _svg_line_chart(
        series("fatigue_score"), 900, 220, (0.0, 1.0), theme.ACCENT,
        [(severe, "重 {:.2f}".format(severe)), (moderate, "中 {:.2f}".format(moderate)),
         (mild, "轻 {:.2f}".format(mild))])
    chart_perclos = _svg_line_chart(series("perclos"), 430, 170, (0.0, 1.0), theme.ACCENT)
    hr = series("hr")
    chart_hr = (_svg_line_chart(hr, 430, 170,
                                (min(v for _t, v in hr) - 5, max(v for _t, v in hr) + 5),
                                theme.ACCENT)
                if len(hr) >= 2 else '<div class="empty">本次会话未估计到心率</div>')

    # ------- 等级占比条 -------
    bars = []
    for i, name in enumerate(LEVEL_NAMES):
        ratio = level_dur[i] / dur_sum
        bars.append(
            '<div class="lv"><span class="dot" style="background:{c}"></span>'
            '<span class="lv-name">{n}</span>'
            '<span class="lv-bar"><i style="width:{w:.1f}%;background:{c}"></i></span>'
            '<span class="lv-val">{r:.1%} · {d:.0f}s</span></div>'.format(
                c=_LEVEL_COLORS[i], n=name, w=ratio * 100, r=ratio, d=level_dur[i]))

    alarm_html = ("、".join("{:.0f}s".format(t) for t in alarm_times)
                  if alarm_times else "本次会话无报警")

    session_name = os.path.splitext(os.path.basename(csv_path))[0]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = ("轻/中/重阈值 {:.2f}/{:.2f}/{:.2f} · 微睡眠 {}s · 持续低头 {}s · "
              "重度需 ≥{} 个模态证据").format(
        mild, moderate, severe,
        (cfg or {}).get("fusion", {}).get("microsleep_sec", "-"),
        (cfg or {}).get("fusion", {}).get("head_down_sec", "-"),
        (cfg or {}).get("fusion", {}).get("severe_min_channels", "-")) if cfg else "—"

    html = _TEMPLATE.format(
        title="疲劳检测会话报告 · {}".format(session_name),
        session=session_name, generated=generated,
        total="{:.0f} 分 {:.0f} 秒".format(total // 60, total % 60),
        rows=len(rows), alarms=len(alarm_times),
        avg_kss="{:.1f} / 9".format(avg_kss) if avg_kss is not None else "—",
        timeline=_level_timeline(rows), bars="".join(bars), alarm_list=alarm_html,
        chart_score=chart_score, chart_perclos=chart_perclos, chart_hr=chart_hr,
        baseline=baseline_note or "未记录", params=params,
        bg=theme.BG, surface=theme.SURFACE, border=theme.BORDER,
        text=theme.TEXT, dim=theme.TEXT_DIM, mute=theme.TEXT_MUTE,
        track=theme.TRACK, mono=theme.MONO, font=theme.FONT_STACK)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# 单文件 HTML 模板（内联样式，无外部依赖；配色取自 theme.py）
_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ margin:0; padding:32px; background:{bg}; color:{text};
         font-family:{font}; }}
  .wrap {{ max-width:980px; margin:0 auto; }}
  h1 {{ font-size:22px; font-weight:500; margin:0 0 4px; }}
  .sub {{ color:{mute}; font-size:13px; margin-bottom:24px; }}
  .card {{ background:{surface}; border:1px solid {border}; border-radius:12px;
           padding:20px; margin-bottom:12px; }}
  .card h2 {{ font-size:13px; color:{dim}; font-weight:400; margin:0 0 14px; }}
  .kpis {{ display:flex; gap:12px; }}
  .kpi {{ flex:1; }}
  .kpi .v {{ font-size:26px; font-family:{mono}; }}
  .kpi .k {{ font-size:12px; color:{dim}; margin-top:2px; }}
  .timeline {{ width:100%; height:26px; border-radius:6px; display:block; }}
  .legend {{ display:flex; gap:16px; margin-top:8px; font-size:12px; color:{dim}; }}
  .legend i {{ display:inline-block; width:9px; height:9px; border-radius:3px;
               margin-right:5px; }}
  .lv {{ display:flex; align-items:center; gap:10px; margin:9px 0; font-size:13px; }}
  .lv .dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
  .lv-name {{ width:64px; color:{dim}; }}
  .lv-bar {{ flex:1; height:6px; background:{track}; border-radius:3px; overflow:hidden; }}
  .lv-bar i {{ display:block; height:6px; border-radius:3px; }}
  .lv-val {{ width:120px; text-align:right; font-family:{mono}; font-size:12px; }}
  .chart {{ width:100%; height:auto; }}
  .axis {{ font-size:10px; fill:{mute}; font-family:{mono}; }}
  .cols {{ display:flex; gap:12px; }}
  .cols .card {{ flex:1; }}
  .empty {{ color:{mute}; font-size:13px; padding:24px 0; text-align:center; }}
  .meta {{ font-size:12px; color:{dim}; line-height:1.9; }}
  .foot {{ color:{mute}; font-size:12px; text-align:center; margin-top:20px; }}
</style></head><body><div class="wrap">
  <h1>疲劳检测会话报告</h1>
  <div class="sub">{session} · 生成于 {generated}</div>

  <div class="card"><h2>会话概览</h2><div class="kpis">
    <div class="kpi"><div class="v">{total}</div><div class="k">检测时长</div></div>
    <div class="kpi"><div class="v">{alarms}</div><div class="k">报警次数</div></div>
    <div class="kpi"><div class="v">{avg_kss}</div><div class="k">平均 KSS 嗜睡度</div></div>
    <div class="kpi"><div class="v">{rows}</div><div class="k">记录行数</div></div>
  </div></div>

  <div class="card"><h2>疲劳等级时间线</h2>
    {timeline}
    <div class="legend">
      <span><i style="background:#34C759"></i>清醒</span>
      <span><i style="background:#E9B949"></i>轻度疲劳</span>
      <span><i style="background:#EF9F27"></i>中度疲劳</span>
      <span><i style="background:#E24B4A"></i>重度疲劳</span>
    </div>
  </div>

  <div class="card"><h2>各等级时长占比</h2>{bars}</div>

  <div class="card"><h2>融合疲劳分（含分级阈值线）</h2>{chart_score}</div>

  <div class="cols">
    <div class="card"><h2>PERCLOS（闭眼时间占比）</h2>{chart_perclos}</div>
    <div class="card"><h2>心率 HR（rPPG 估计）</h2>{chart_hr}</div>
  </div>

  <div class="card"><h2>报警时刻</h2><div class="meta">{alarm_list}</div></div>

  <div class="card"><h2>会话参数</h2><div class="meta">
    基线：{baseline}<br>关键阈值：{params}
  </div></div>

  <div class="foot">由「疲劳检测与预警系统」自动生成 · 数据来源为本次会话的检测记录 CSV</div>
</div></body></html>
"""
