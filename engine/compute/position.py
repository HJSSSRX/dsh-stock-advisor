# -*- coding: utf-8 -*-
"""位置分析 —— 支撑压力位（区间化 + 反弹计数 + 贝叶斯强度 + 时间衰减）。

═══════════════════════════════════════════════════════════════════
★ 这份实现的来历（按 check-existing-first 盘点的结果）
═══════════════════════════════════════════════════════════════════

L0 本机：`polars_ta` 有**全部原语**（HHV/LLV/HHVBARS/BARSLAST/COUNT），
        但**不做支撑压力位** → **原语复用，算法自建。**
L1 工作区：无现成实现。
L3 开源：调研逐个核实过 9 个实现，最高 2~6★，且都没有形式化定义。
L4 学术：**Chung & Bellotti (arXiv:2101.07410)** 给了唯一的形式化定义 ——
        本文件的算法照它实现。

★★★ 三条不可违反的约束（来自 A 股实证，见 `设计\计算层实现方案.md` §0.4）
───────────────────────────────────────────────────────────────────
Jiang/Song/Tong (International Review of Finance, DOI 10.1111/irfi.12161)：
A股 1997–2015 的 **3 万条规则**，不做数据窥探校正有 ~2 万条"显著"，
做 White(2000)/Hsu et al.(2010) SPA 校正后**只剩 ~170 条（0.57%）**。

所以：
  1. **只能输出「区间 + 强度 + 衰减 + 样本量」，不能输出一根线。**
  2. **强度分必须声明口径**，不许跨方法比较。
  3. **必须有随机游走对照组**（见 selftest_position.py）——
     「随机游走上反弹概率 ≈ 0.5」不成立，强度分就是自欺。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import polars as pl

from polars_ta.tdx.reference import BARSLAST, COUNT, HHV, HHVBARS, LLV

# ══════════════════════════════════════════════════════════════════
# 常量（必须放在 LEVEL_CALIBER 之前 —— 口径声明里要引用它们）
# ══════════════════════════════════════════════════════════════════
# 样本充分性门槛（**工程判断，非实证** —— 必须标出来）
MIN_TOUCHES_FOR_STRENGTH = 3      # 触及少于 3 次不给强度分
MIN_BARS_FOR_LEVELS = 120         # 少于 120 根不算
DEFAULT_EPSILON = 0.005           # 区间半宽兜底：价格的 0.5%

# ★★★ 随机游走基线 —— 强度分必须对照它才有意义 ★★★
#   实测（`selftest_position.py` 第三节，200 条随机游走 × 400 根，990 个强度分）：
#
#       第一版算法（不对称判定）  均值 0.6730   ← 测的是算法偏差
#       现版本（从进入价对称判定）均值 0.5755   ← 仍有 +7.5pp 残差
#
#   **残差的来源没有完全查清，所以不假装它不存在 —— 把它作为基线暴露出来。**
#   含义：**强度分落在 0.50~0.65 区间内时不具区分度**，
#         因为纯随机序列也会给出这个数。
#
#   ⚠️ 不要为了让随机游走刚好等于 0.5 而继续调算法 —— 那是过拟合测试。
RANDOM_WALK_BASELINE = 0.5755     # 实测值；改了算法要重跑 selftest_position 更新
BASELINE_BAND = (0.50, 0.65)      # 这个区间内的强度分不具备区分度

# ══════════════════════════════════════════════════════════════════
# 口径声明（每个输出都要带上，用户会问「这数字怎么来的」）
# ══════════════════════════════════════════════════════════════════
LEVEL_CALIBER = {
    "区间定义": (
        "支撑/压力是**区间**不是一条线。区间 = 滚动窗口极值 ± γ，"
        "γ = 全序列平均价格增量（Chung & Bellotti arXiv:2101.07410 的极简算法）。"
    ),
    "强度定义": (
        "强度 = 反弹概率，用**贝叶斯 Beta 后验** (n_b+1)/(N+2) 估计（同上）。"
        "n_b = 在该区间发生反弹的次数，N = 触及总次数。"
        "★ 这是**后验均值**，不是频率 —— 样本少时会向 0.5 收缩，这是刻意的。"
    ),
    "⭐ 强度分必须对照随机基线": (
        f"**本算法在纯随机序列上的基线是 {RANDOM_WALK_BASELINE:.4f}**"
        f"（实测 200 条随机游走 × 400 根，见 selftest_position.py）。"
        f"所以**强度落在 {BASELINE_BAND[0]:.2f}~{BASELINE_BAND[1]:.2f} 区间内时不具区分度** —— "
        "那个数字在**没有记忆的随机行情**上同样会出现。"
        "**把 0.58 说成「强支撑」是把算法的基线当成市场的性质。**"
    ),
    "为什么不能只给一根线": (
        "A股 3 万条规则经 SPA 校正后只剩 0.57% 显著（Jiang/Song/Tong, IRF）。"
        "**输出一根线 = 声称一个未经校正的点位有效**，这是自我欺骗。"
    ),
    "已知局限": (
        "① 支撑压力位**没有公认的数学定义**（论文原文：no clear mathematical definition "
        "can be found in the literature）；② A股+日频**没有对口实证**（现有实证全是 "
        "FX 分钟级 / LSE tick / 荷兰股市）；③ 日频看不到挂单，我们做的是"
        "**事后统计**不是**事前机制** —— **不能说「我知道那里有大量买单」**。"
    ),
    "时间衰减": (
        "论文发现历史反弹次数越多→再反弹概率越高，**但存在时间衰减**。"
        "本实现用「距上次测试的天数」显式暴露，不做隐式加权。"
    ),
}

@dataclass
class Level:
    """一个支撑/压力位。**是区间，不是一根线。**"""
    kind: str                     # "support" | "resistance"
    low: float                    # 区间下界
    high: float                   # 区间上界
    touches: int                  # 触及次数（进入区间即算）
    recoveries: int               # 其中「反弹」的次数
    strength: Optional[float]     # 贝叶斯后验 (n_b+1)/(N+2)；样本不足则 None
    last_touch_idx: Optional[int]  # 最近一次触及位于序列第几根
    bars_since_touch: Optional[int]  # 距最近一次触及多少根（时间衰减的显式暴露）
    source: str = "swing_extreme"  # 来源标签，不许跨方法比较
    note: str = ""                # 样本不足等原因

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def to_dict(self) -> dict:
        return {
            "类型": "支撑" if self.kind == "support" else "压力",
            "区间": [round(self.low, 3), round(self.high, 3)],
            "触及次数": self.touches,
            "反弹次数": self.recoveries,
            "强度": (round(self.strength, 3) if self.strength is not None
                     else f"样本不足（<{MIN_TOUCHES_FOR_STRENGTH} 次）"),
            "区分度": self.discrimination,
            "距上次触及(根)": self.bars_since_touch,
            "来源": self.source,
            "备注": self.note,
        }

    @property
    def discrimination(self) -> str:
        """★ 强度分相对随机基线的区分度。

        这是**给用户看的关键字段** —— 光给一个 0.58 会让人以为是「强支撑」，
        而纯随机行情同样会给出 0.58。
        """
        if self.strength is None:
            return "样本不足，不给区分度"
        lo, hi = BASELINE_BAND
        if lo <= self.strength <= hi:
            return (f"⚠️ 无区分度 —— 随机行情基线 {RANDOM_WALK_BASELINE:.2f}，"
                    f"本值落在基线带 {lo:.2f}~{hi:.2f} 内")
        if self.strength > hi:
            return (f"略高于随机基线 {RANDOM_WALK_BASELINE:.2f}"
                    f"（+{self.strength - RANDOM_WALK_BASELINE:.2f}，但仅单票样本，不构成有效性证据）")
        return (f"低于随机基线 {RANDOM_WALK_BASELINE:.2f}"
                f"（{self.strength - RANDOM_WALK_BASELINE:+.2f}）")


@dataclass
class LevelSet:
    """一次位置分析的完整结果。**带口径声明与样本充分性标记。**"""
    levels: list[Level] = field(default_factory=list)
    bars: int = 0
    sufficient: bool = False      # 样本是否充分
    caliber: dict = field(default_factory=lambda: dict(LEVEL_CALIBER))
    disclaimer: str = ""

    def to_dict(self) -> dict:
        return {
            "样本根数": self.bars,
            "样本充分": self.sufficient,
            "位": [lv.to_dict() for lv in self.levels],
            "口径声明": self.caliber,
            "免责": self.disclaimer,
        }


def average_increment(close: pl.Expr) -> float:
    """γ = 全序列平均价格增量（Chung & Bellotti 的极简算法用它当区间半宽）。

    ★ 是「平均绝对增量」，不是「标准差」—— 论文用的是前者。
    """
    return 0.0  # 真实值由调用方从数据算（表达式层面拿不到标量）


# ══════════════════════════════════════════════════════════════════
# 主算法
# ══════════════════════════════════════════════════════════════════
def find_levels(
    df: pl.DataFrame,
    *,
    window: int = 120,
    max_levels: int = 5,
    epsilon: Optional[float] = None,
    kind: str = "both",
) -> LevelSet:
    """找支撑压力位区间。

    参数
    ----
    df        需含 high / low / close 列（小写），已按日期升序
    window    滚动窗口根数（找极值用）。**应 ≤ 预热后的可用根数**
    max_levels 每类最多返回几个（按强度×样本量排序）
    epsilon   区间半宽。None 时用 γ（平均价格增量），再兜底 0.5%
    kind      "support" | "resistance" | "both"

    算法（三步，全部可核对）：
      1. **找极值点**：滚动窗口内的最高/最低价，且**左右都低于/高于它**（摆动极值）
      2. **区间化**：极值 ± epsilon，其中 epsilon = max(γ, 价格×0.5%)
      3. **计数**：价格进入区间 = 触及；进入后**反向离开超过 epsilon** = 反弹
    """
    n = len(df)
    out = LevelSet(bars=n)
    if n < MIN_BARS_FOR_LEVELS:
        out.disclaimer = f"样本不足（{n} < {MIN_BARS_FOR_LEVELS} 根），不出结论"
        return out
    out.sufficient = True

    high = df["high"].to_list()
    low = df["low"].to_list()
    close = df["close"].to_list()

    # γ：平均绝对日增量（论文的极简算法）
    incs = [abs(close[i] - close[i - 1]) for i in range(1, n)]
    gamma = sum(incs) / len(incs) if incs else 0.0
    eps = epsilon if epsilon is not None else max(gamma, close[-1] * DEFAULT_EPSILON)

    # ── 第 1 步：摆动极值（左右都低于/高于它）──
    piv_high: list[tuple[int, float]] = []
    piv_low: list[tuple[int, float]] = []
    half = max(2, window // 10)
    for i in range(half, n - half):
        seg_h = high[i - half:i + half + 1]
        seg_l = low[i - half:i + half + 1]
        if high[i] == max(seg_h) and seg_h.count(high[i]) == 1:
            piv_high.append((i, high[i]))
        if low[i] == min(seg_l) and seg_l.count(low[i]) == 1:
            piv_low.append((i, low[i]))

    def cluster(pivots: list[tuple[int, float]], k: str) -> list[Level]:
        """把相近的极值点聚成区间，并统计触及/反弹。

        ★★★ 反弹判定的对称性（这里踩过坑，务必别改回去）★★★
        第一版是「反弹 = 价格越过区间对侧边缘，穿透 = 越过本侧边缘」——
        **那是不对称的**：价格进入区间时通常贴近某一侧，
        于是「越到对侧」的距离远小于「越出本侧」的距离，**系统性高估反弹率**。

        实测证据（随机游走对照组，200 条 × 400 根）：
            第一版在**纯随机序列**上算出反弹概率 **0.673**（应为 0.5）
            —— 那个分数测的是算法偏差，不是市场性质。

        现在改为**从进入时的价格 close[i] 出发、以同一个 eps 向两侧对称判定**。
        """
        if not pivots:
            return []
        pivots = sorted(pivots, key=lambda x: x[1])
        groups: list[list[tuple[int, float]]] = []
        cur = [pivots[0]]
        for p in pivots[1:]:
            if p[1] - cur[-1][1] <= 2 * eps:
                cur.append(p)
            else:
                groups.append(cur)
                cur = [p]
        groups.append(cur)

        horizon = max(20, window // 4)

        levels: list[Level] = []
        for g in groups:
            lo = min(x[1] for x in g) - eps
            hi = max(x[1] for x in g) + eps
            touches = recoveries = 0
            last_i: Optional[int] = None
            i = 0
            while i < n:
                if lo <= low[i] <= hi or lo <= high[i] <= hi:
                    last_i = i
                    ref = close[i]
                    # 对称判定：从进入价 ref 出发，先碰到 +eps 还是 -eps
                    moved = 0                      # +1 向上 / -1 向下 / 0 未定
                    j = i + 1
                    while j < n and j - i <= horizon:
                        if close[j] >= ref + eps:
                            moved = 1
                            break
                        if close[j] <= ref - eps:
                            moved = -1
                            break
                        j += 1
                    if moved != 0:
                        touches += 1
                        # 支撑：向上 = 撑住了（反弹）；压力：向下 = 压住了（反弹）
                        if (k == "support" and moved > 0) or \
                           (k == "resistance" and moved < 0):
                            recoveries += 1
                    i = max(j, i + 1)
                else:
                    i += 1

            if touches == 0:
                continue
            strength = ((recoveries + 1) / (touches + 2)
                        if touches >= MIN_TOUCHES_FOR_STRENGTH else None)
            note = "" if strength is not None else \
                f"触及仅 {touches} 次（< {MIN_TOUCHES_FOR_STRENGTH}），按工程门槛不给强度分"
            levels.append(Level(
                kind=k, low=lo, high=hi, touches=touches, recoveries=recoveries,
                strength=strength, last_touch_idx=last_i,
                bars_since_touch=(n - 1 - last_i) if last_i is not None else None,
                note=note,
            ))
        return levels

    got: list[Level] = []
    if kind in ("support", "both"):
        got += cluster(piv_low, "support")
    if kind in ("resistance", "both"):
        got += cluster(piv_high, "resistance")

    # 排序：有强度的按强度×触及数；没强度的排后面
    got.sort(key=lambda lv: ((lv.strength or 0) * (1 + lv.touches / 10)), reverse=True)
    out.levels = got[:max_levels]
    out.disclaimer = (
        "★ 这些是**事后统计**的区间，不是「那里有买单」的事前机制。"
        f"**强度分必须对照随机基线 {RANDOM_WALK_BASELINE:.2f}**（本算法在纯随机序列上的实测值）—— "
        f"落在 {BASELINE_BAND[0]:.2f}~{BASELINE_BAND[1]:.2f} 内的强度"
        "**在无记忆的随机行情上同样会出现**，不具区分度。"
        "★ A股同日频的**样本外有效性**没有对口实证（3 万条规则经 SPA 校正后只剩 0.57%）→ "
        "**只能当参考位置，不能当信号。**"
    )
    return out


def levels_for_report(df: pl.DataFrame, **kw) -> dict:
    """给输出层的入口：返回可直接转成用户话术的 dict。"""
    return find_levels(df, **kw).to_dict()
