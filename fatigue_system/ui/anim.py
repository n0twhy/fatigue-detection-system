# -*- coding: utf-8 -*-
"""动画工具（DESIGN.md §7.0）：统一的 QVariantAnimation 封装。

规范约束：时长只用 theme.ANIM_FAST/BASE/SLOW（150/220/300ms 上限）；
缓动只用 OutCubic（进场/状态变化）与 InCubic（退场）；禁止 Linear/弹跳；
实时数据流（视频帧/曲线追加）不加动画——动画只解释"用户操作或状态变化"。

QSS 没有 CSS transition，颜色/数值过渡都用 QVariantAnimation 逐帧回调驱动。
"""

from PyQt5.QtCore import QEasingCurve, QVariantAnimation

EASE_OUT = QEasingCurve.OutCubic   # 进场/状态变化统一
EASE_IN = QEasingCurve.InCubic     # 仅退场使用


def animate(owner, key: str, start, end, ms: int, on_frame, easing=EASE_OUT,
            on_finish=None):
    """在 owner 上跑一段属性动画；同 key 的旧动画会被打断（后到优先）。

    参数:
        owner    —— 动画宿主 QObject（生命周期随宿主，防止被 GC）。
        key      —— 宿主上保存动画对象的属性名（同名互斥）。
        start/end—— 起止值（float / int / QColor 均可，Qt 自带插值器）。
        ms       —— 时长毫秒（勿超 theme.ANIM_SLOW=300，§7.8）。
        on_frame —— 每帧回调 on_frame(当前值)。
        easing   —— 缓动曲线，默认 OutCubic。
        on_finish—— 可选完成回调。
    返回动画对象（一般无需持有）。
    """
    old = getattr(owner, key, None)
    if old is not None:
        old.stop()
    anim = QVariantAnimation(owner)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(int(ms))
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(on_frame)
    if on_finish is not None:
        anim.finished.connect(on_finish)
    setattr(owner, key, anim)
    anim.start()
    return anim
