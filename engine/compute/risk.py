# -*- coding: utf-8 -*-
"""风险画像 —— 波动 / 回撤 / VaR / 下行风险 + **停牌修正**。

═══════════════════════════════════════════════════════════════════
★ 这份实现里唯一「难」的部分是停牌，其余都是常规统计
═══════════════════════════════════════════════════════════════════

Tian (2019, Lancaster 工作论文) 实测：
    **CSMAR 记缺失值、WIND 记 0 收益 —— 两者都错。**
    停牌在很大程度上是 ex-ante 不可预期的；
    不修正会让规模因子被高估约 **192 bp/年**（摘要 1.92% / 正文 1.6%，原文两处不一致，如实并列）。

正确做法（照论文）：**用 Beta × 市场收益代理填充**，并且：
    · **披露填充天数**（不能悄悄填）
    · **>30 天的长停牌单独标为风险事件**（那不是一个收益缺失，是一个事件）
停牌时长分布：**D=1 占 71.38%**；D>30 天合计约 5.9%。

★★★ 停牌怎么检测 —— 用指数的交易日历 ★★★
实测：个股 K 线里**成交量为 0 的根数是 0** —— 停牌日**直接从序列里消失了**。
而单看个股的日期缺口分不清「停牌」和「放假」（国庆/春节都是 7~10 天的缺口）。

所以我们用 **`index_kline`（上证指数）的日期集合当交易日历**：
    交易日历 − 个股有数据的日期 = **停牌日**

⚠️ 一个必须排除的边界：**最近还没收盘的日子**。
    实测：`2026-10-12` 当天，指数有 bar、个股还没有 → 会被误判成 1 天停牌。
    那不是停牌，是数据还没出。

⚠️ 关于 VaR：**涨跌停会低估风险**（逻辑成立），但**没有任何量化文献给出修正幅度** →
   **本模块不编修正系数**，只在输出里披露这个方向性偏差。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import polars as pl
from polars_ta.performance.drawdown import ts_max_drawdown_rate

RISK_CALIBER = {
    "波动率": "HV（已实现，样本标准差）+ EWMA（λ=0.94，RiskMetrics 惯例）。**不用 GARCH**："
              "要拖 scipy + statsmodels 两条重依赖，且 A 股 GARCH 参数实证拿不到 → 性价比低。",
    "停牌填充": "**Beta × 市场收益**代理填充（Tian 2019）。填了几天必须披露 —— "
                "悄悄填和记 0 一样是错的，只是错的方向不同。",
    "VaR": "历史模拟（取分位数）。⚠️ **涨跌停会低估 VaR** —— 跌停日真实跌幅被截断在 -10%/-20%。"
           "这个方向性偏差**成立**，但**没有任何文献给出修正幅度** → **不修正，只披露**。",
    "下行波动": "只对负收益算标准差。**天生规避涨跌停截断问题**（涨跌停只影响上行截断的方向）。"
                "索提诺比率 = (年化收益 − 无风险) / 下行波动。",
    "回撤": "用 `polars_ta.performance.drawdown.ts_max_drawdown`。区间限 1250 根 —— "
            "A 股 1996-12-16 之前没有涨跌停制度，更早的数据与现在不可比。",
}

# 停牌相关阈值（Tian 2019 的分布实测：D=1 占 71.38%，D>30 约 5.9%）
LONG_SUSPENSION_DAYS = 30     # ≥30 天算「长停牌」，单独标为风险事件
MAX_LOOKBACK_BARS = 1250      # 回撤计算上限（涨跌停制度起点之后）


@dataclass
class Suspension:
    """一段停牌。"""
    start: str
    days: int
    is_long: bool = False      # ≥30 天

    def to_dict(self) -> dict:
        return {"起始": self.start, "天数": self.days,
                "标记": "长停牌（≥30天）" if self.is_long else ""}


@dataclass
class RiskProfile:
    """风险画像的完整结果。**带填充披露与口径声明。**"""
    bars: int = 0
    # 波动
    hv_annual: Optional[float] = None
    ewma_annual: Optional[float] = None
    downside_annual: Optional[float] = None
    # 回撤
    max_drawdown: Optional[float] = None
    # VaR
    var95: Optional[float] = None
    var99: Optional[float] = None
    # 停牌
    suspensions: list[Suspension] = field(default_factory=list)
    filled_days: int = 0
    beta: Optional[float] = None
    # 元信息
    sufficient: bool = False
    caliber: dict = field(default_factory=lambda: dict(RISK_CALIBER))
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "样本根数": self.bars,
            "样本充分": self.sufficient,
            "年化波动率_HV": _r(self.hv_annual),
            "年化波动率_EWMA": _r(self.ewma_annual),
            "年化下行波动": _r(self.downside_annual),
            "最大回撤": _r(self.max_drawdown),
            "VaR95_日": _r(self.var95),
            "VaR99_日": _r(self.var99),
            "停牌段数": len(self.suspensions),
            "停牌填充天数": self.filled_days,
            "Beta": _r(self.beta),
            "停牌明细": [s.to_dict() for s in self.suspensions],
            "提示": self.notes,
            "口径声明": self.caliber,
        }


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 4)


# ══════════════════════════════════════════════════════════════════
# 一、停牌检测（用指数交易日历）
# ══════════════════════════════════════════════════════════════════
def detect_suspensions(
    stock_dates: list[str],
    calendar: list[str],
    *,
    as_of: Optional[str] = None,
) -> list[Suspension]:
    """交易日历 − 个股有数据的日期 = 停牌日。

    ★ as_of：**必须排除「还没收盘」的最近日子**。
      实测踩到：`2026-10-12` 当天指数已有 bar、个股还没有 → 被误判成 1 天停牌。
      传 None 时按「日历最后一个日期」处理（即把它排除）。
    """
    cal = sorted(set(calendar))
    if not cal:
        return []
    # 排除日历最后一天（通常是当天，数据未完整）
    cutoff = as_of or cal[-1]
    cal = [c for c in cal if c < cutoff]
    have = set(stock_dates)

    # ★★ 只保留**个股数据覆盖范围内**的日历日。
    #   实测踩到（2026-10-13）：茅台个股取了 640 根（起点 2024-02-20），
    #   指数取了 800 根（起点 2023-06-20）→ 指数多出的 160 个交易日在
    #   「日历 − 个股」里全部算成缺失，**报出「停牌 160 天」这种假事实**。
    #   个股历史比指数短是常态（新股/取数窗口不同），不是停牌。
    if have:
        lo = min(have)
        cal = [c for c in cal if c >= lo]

    missing = [c for c in cal if c not in have]
    if not missing:
        return []

    # 把连续的缺失日聚成段
    out: list[Suspension] = []
    runs: list[list[str]] = [[missing[0]]]
    for d in missing[1:]:
        prev = runs[-1][-1]
        # 相邻（在日历里挨着）就算同一段
        i = cal.index(prev) if prev in cal else -1
        j = cal.index(d) if d in cal else -1
        if i >= 0 and j == i + 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    for run in runs:
        out.append(Suspension(start=run[0], days=len(run),
                              is_long=len(run) >= LONG_SUSPENSION_DAYS))
    return out


# ══════════════════════════════════════════════════════════════════
# 二、停牌填充（Beta × 市场收益）
# ══════════════════════════════════════════════════════════════════
def compute_beta(stock_ret: list[Optional[float]],
                 mkt_ret: list[Optional[float]]) -> Optional[float]:
    """个股日收益对市场日收益的 OLS 斜率（只用两边都有值的日子）。

    ★ 不做任何「去极值」「winsorize」—— 那会引入没人核对过的处理。
    """
    xs = [(m, s) for m, s in zip(mkt_ret, stock_ret)
          if m is not None and s is not None]
    if len(xs) < 30:
        return None
    n = len(xs)
    mx = sum(x for x, _ in xs) / n
    my = sum(y for _, y in xs) / n
    cov = sum((x - mx) * (y - my) for x, y in xs)
    var = sum((x - mx) ** 2 for x, _ in xs)
    if var <= 0:
        return None
    return cov / var


def fill_suspensions(
    stock_ret: Optional[list[Optional[float]]],
    mkt_ret: list[Optional[float]],
    stock_dates: list[str],
    calendar: list[str],
    beta: Optional[float],
) -> tuple[list[Optional[float]], list[str], int]:
    """把停牌日的收益用 `Beta × 市场收益` 填上。

    ⚠️ **返回填充天数，调用方必须披露。**
       「悄悄填上」和「记 0」一样是错的 —— 只是错的方向不同（Tian 2019）。
    """
    if beta is None:
        return list(stock_ret or []), list(stock_dates), 0

    mkt_by_date = {}
    for d, r in zip(calendar, mkt_ret):
        if r is not None:
            mkt_by_date[d] = r

    out_r: list[Optional[float]] = []
    out_d: list[str] = []
    filled = 0
    pos = 0
    have = {d: i for i, d in enumerate(stock_dates)}
    for d in calendar:
        if d in have:
            out_r.append(stock_ret[have[d]] if stock_ret and have[d] < len(stock_ret) else None)
            out_d.append(d)
        else:
            m = mkt_by_date.get(d)
            out_r.append(None if m is None else beta * m)
            out_d.append(d)
            if m is not None:
                filled += 1
    return out_r, out_d, filled


# ══════════════════════════════════════════════════════════════════
# 三、常规风险指标
# ══════════════════════════════════════════════════════════════════
def _annualize(x: float, periods: int = 244) -> float:
    """日频 → 年化。**A股一年约 244 个交易日**（不是 252，美股口径）。"""
    return x * (periods ** 0.5)


def hv(returns: list[Optional[float]], periods: int = 244) -> Optional[float]:
    """历史波动率（样本标准差，年化）。"""
    v = [r for r in returns if r is not None]
    if len(v) < 20:
        return None
    m = sum(v) / len(v)
    var = sum((x - m) ** 2 for x in v) / (len(v) - 1)     # ddof=1
    return _annualize(var ** 0.5, periods)


def ewma_vol(returns: list[Optional[float]], lam: float = 0.94,
             periods: int = 244) -> Optional[float]:
    """EWMA 波动率（λ=0.94，RiskMetrics 惯例），年化。"""
    v = [r for r in returns if r is not None]
    if len(v) < 20:
        return None
    s = v[0] ** 2
    for r in v[1:]:
        s = lam * s + (1 - lam) * r ** 2
    return _annualize(s ** 0.5, periods)


def downside_vol(returns: list[Optional[float]], periods: int = 244) -> Optional[float]:
    """下行波动（只对负收益算标准差），年化。

    ★ **天生规避涨跌停截断问题** —— 涨跌停压缩的是极端值，下行波动只用负侧。
    """
    v = [r for r in returns if r is not None]
    if len(v) < 20:
        return None
    neg = [r for r in v if r < 0]
    if len(neg) < 5:
        return None
    m = sum(neg) / len(neg)
    var = sum((x - m) ** 2 for x in neg) / (len(neg) - 1)
    return _annualize(var ** 0.5, periods)


def var_historical(returns: list[Optional[float]],
                   level: float = 0.95) -> Optional[float]:
    """历史模拟 VaR（返回**正数**表示损失）。

    ⚠️ **涨跌停会让这个数偏小** —— 真实跌幅被截断在 -10%/-20%。
       方向确定，但**修正幅度没有文献支持** → 不修正，由调用方披露。
    """
    v = sorted(r for r in returns if r is not None)
    if len(v) < 50:
        return None
    idx = int((1 - level) * len(v))
    idx = max(0, min(idx, len(v) - 1))
    return abs(v[idx])


def max_drawdown(close: list[float]) -> Optional[float]:
    """最大回撤（**比率**，正数，0.5 = 跌了 50%）。

    ★★★ polars_ta 里有两个名字很像的函数，别拿错（实测踩到）★★★
        `ts_max_drawdown(c)`      → `c - c.cum_max()`        = **绝对金额差**（-50.0 元）
        `ts_max_drawdown_rate(c)` → `c / c.cum_max() - 1`    = **比率**（-0.5）← **要这个**

    拿错不会报错：100→50 会返回 50.0，看着像"回撤 50%"，其实是"跌了 50 元"。
    真实数据上更隐蔽 —— 南华仪器会显示"最大回撤 3.75"，
    看着像 3.75%（合理），实际是 3.75 元（后复权价约 30 元 → 真实回撤约 12%）。
    """
    if len(close) < 30:
        return None
    c = close[-MAX_LOOKBACK_BARS:]
    df = pl.DataFrame({"close": c})
    try:
        v = df.select(ts_max_drawdown_rate(pl.col("close")).alias("d"))["d"].min()
        return abs(float(v)) if v is not None else None
    except Exception:
        # 兜底：自己遍历峰值（与上面同口径，返回比率）
        peak = c[0]
        mdd = 0.0
        for x in c:
            peak = max(peak, x)
            if peak > 0:
                mdd = max(mdd, (peak - x) / peak)
        return mdd


# ══════════════════════════════════════════════════════════════════
# 四、入口
# ══════════════════════════════════════════════════════════════════
def risk_profile(
    stock: pl.DataFrame,
    index: pl.DataFrame,
    *,
    as_of: Optional[str] = None,
) -> RiskProfile:
    """算一只票的风险画像。

    stock / index 都需含 `date` 与 `close` 列（升序）。
    **index 必须来自 `fetch('index_kline', ...)`** —— 用 `kline` 取 000001
    会拿到平安银行，而且不会报错。
    """
    out = RiskProfile(bars=len(stock))
    if len(stock) < 30:
        out.notes.append(f"样本不足（{len(stock)} < 30 根），不出结论")
        return out

    s_dates = [str(d)[:10] for d in stock["date"].to_list()]
    s_close = [float(x) for x in stock["close"].to_list()]
    i_dates = [str(d)[:10] for d in index["date"].to_list()]
    i_close = [float(x) for x in index["close"].to_list()]
    out.sufficient = len(stock) >= 120

    # ── 停牌检测与填充 ──
    out.suspensions = detect_suspensions(s_dates, i_dates, as_of=as_of)
    out.filled_days = sum(s.days for s in out.suspensions)
    if out.suspensions:
        longs = [s for s in out.suspensions if s.is_long]
        out.notes.append(
            f"检测到 {len(out.suspensions)} 段停牌共 {out.filled_days} 天"
            + (f"，其中 **{len(longs)} 段 ≥{LONG_SUSPENSION_DAYS} 天（单独标为风险事件）**" if longs else "")
        )

    # ── 对齐到指数日历，算收益 ──
    ret_by_date = {}
    for i in range(1, len(s_dates)):
        if s_close[i - 1] > 0:
            ret_by_date[s_dates[i]] = s_close[i] / s_close[i - 1] - 1
    mkt_ret, _ = _returns(i_dates, i_close)

    aligned_s = [ret_by_date.get(d) for d in i_dates]
    out.beta = compute_beta(aligned_s, mkt_ret)

    if out.filled_days and out.beta is not None:
        aligned_s, _, filled = fill_suspensions(aligned_s, mkt_ret, i_dates, i_dates, out.beta)
        out.filled_days = filled
    elif out.filled_days and out.beta is None:
        out.notes.append("停牌天数 >0 但 Beta 算不出来（样本不足）→ **未填充**，波动率会偏低")

    # ── 常规指标 ──
    out.hv_annual = hv(aligned_s)
    out.ewma_annual = ewma_vol(aligned_s)
    out.downside_annual = downside_vol(aligned_s)
    out.var95 = var_historical(aligned_s, 0.95)
    out.var99 = var_historical(aligned_s, 0.99)
    out.max_drawdown = max_drawdown(s_close)
    return out


def _returns(dates: list[str], close: list[float]) -> tuple[list[Optional[float]], list[str]]:
    out: list[Optional[float]] = [None]
    for i in range(1, len(close)):
        out.append(close[i] / close[i - 1] - 1 if close[i - 1] > 0 else None)
    return out, dates
