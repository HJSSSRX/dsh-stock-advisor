# -*- coding: utf-8 -*-
"""资金流 —— **只做"可复算量"，不做"厂商黑箱"**。

═══════════════════════════════════════════════════════════════════
★★★ 开工前调研的三条结论（决定了本模块只做这一件事）
═══════════════════════════════════════════════════════════════════
**① 东财「主力资金流」不能进结论。**
   · 交易所自己的行情公司把它列为 **Level-2 数据的衍生加工指标**；
   · 东财用**未公开的"还原算法"**，同花顺又是另一套**相对阈值**口径 ——
     **同名数字不可比、不可互证**；
   · 预测力：**没有单向证据** —— 成熟市场机构订单流是**负向**预测
     （Campbell–Ramadorai–Vuolteenaho），A 股两篇顶刊**符号相反**。
   → 本模块**不产出任何基于"主力资金流"的方向**。

**② 北向资金「净流入」这个字段已经不存在。**
   港交所官方每日统计表头只有 Total Turnover / Trade Count / DQB / ETF Turnover。
   → 任何还在输出"今日北向净流入 X 亿"的设计**必须删掉，不是降级**。

**③ 真正硬的是两融 —— 因为交易所公布了会计恒等式。**
   深交所原文：**本日融资余额 = 前日融资余额 + 本日融资买入额 − 本日融资偿还额**
   → 「融资净买入」= **融资余额一阶差分**，是**可复算量，不是估计量**。
   ★★ 这条恒等式可以直接当**外部标准答案**：拿它逐日校验数据源 ——
      **三方对质**：数据源给的数据 vs 交易所公布的定义。

═══════════════════════════════════════════════════════════════════
边界（写死在代码里，不靠自觉）
═══════════════════════════════════════════════════════════════════
    ✅ 做：融资余额 / 融资买入 / 还 / 融券余额 / 融资融券余额
           + 恒等式推出的「净融资买入」+ 趋势 + **恒等式自校验**
    ⛔ 不做：主力资金流进结论、北向净流入（字段不存在）、游资席位标签
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 融资金额字段（单位：元）
FIELDS = {
    "rzye": "融资余额",
    "rzmre": "融资买入额",
    "rzche": "融资偿还额",
    "rqye": "融券余额",
    "rzrqye": "融资融券余额",
}

# 恒等式容差：官方数据保留到元，四舍五入会差 1~2 元
IDENTITY_TOL = 2.0


@dataclass
class MarginRow:
    date: str
    rzye: Optional[float] = None       # 融资余额
    rzmre: Optional[float] = None      # 融资买入额
    rzche: Optional[float] = None      # 融资偿还额
    rqye: Optional[float] = None       # 融券余额
    rzrqye: Optional[float] = None     # 融资融券余额


@dataclass
class MarginReport:
    code: str = ""
    source: str = ""
    as_of: str = ""
    rows: list = field(default_factory=list)
    # ── 恒等式自校验（**外部标准答案**）──
    checked: int = 0
    identity_ok: int = 0
    max_deviation: float = 0.0
    big_deviations: list = field(default_factory=list)   # (日期, 差额)
    # ── 可复算指标 ──
    net_margin_buy: Optional[float] = None    # 最近一日净融资买入（= 一阶差分）
    chg_5d: Optional[float] = None
    chg_20d: Optional[float] = None
    trend: str = ""
    notes: list = field(default_factory=list)
    error: str = ""

    @property
    def identity_rate(self) -> Optional[float]:
        return (self.identity_ok / self.checked) if self.checked else None

    def render(self) -> str:
        if self.error:
            return f"⚠️ 两融数据取不到：{self.error}"
        L = [f"两融（来源 {self.source}｜数据时点 {self.as_of}，{len(self.rows)} 个交易日）"]
        if self.net_margin_buy is not None:
            L.append(f"  · 最近一日**净融资买入**（= 融资余额一阶差分，可复算）："
                     f"{self.net_margin_buy/1e8:+.4f} 亿元")
        if self.chg_5d is not None:
            L.append(f"  · 融资余额 5 日变化：{self.chg_5d/1e8:+.2f} 亿元")
        if self.chg_20d is not None:
            L.append(f"  · 融资余额 20 日变化：{self.chg_20d/1e8:+.2f} 亿元")
        if self.trend:
            L.append(f"  · 趋势：{self.trend}")
        if self.checked:
            L.append(f"  ★ **恒等式自校验**（交易所公布：本日余额 = 前日余额 + 买入 − 偿还）："
                     f"{self.identity_ok}/{self.checked} 天成立，最大偏差 {self.max_deviation:.0f} 元")
            if self.big_deviations:
                L.append(f"    ⚠️ 有 {len(self.big_deviations)} 天偏差超出容差"
                         f"（{IDENTITY_TOL:.0f} 元），**不调和、原样报出**：")
                for d, dev in self.big_deviations[:5]:
                    L.append(f"      - {d}：差 {dev:+,.0f} 元")
            else:
                L.append("    ✅ 全部在容差内 —— 这份数据**自洽**（这是它能进结论的前提）")
        # ★★ 边界声明放在 render() 里，**不放在 load() 里** ——
        #   放 load() 的话，任何别的构造路径（构造数据、回放、测试）都会丢掉它。
        #   边界说明必须是**结构性的**，不能依赖走哪条路。
        L.append("  · ⚠️ **两融是「谁在借钱买」的官方口径，但它不预测方向** ——"
                 "融资余额上升只说明杠杆资金在增，可能是看好也可能是追高。"
                 "本系统**不据此单独给方向**。")
        L.append("  · ⛔ **本模块刻意不做**：东财「主力资金流」（厂商黑箱、无第二来源、"
                 "实证符号矛盾）、「北向净流入」（**该字段已不存在**）、"
                 "游资席位标签（民间观测）。")
        for n in self.notes:
            L.append(f"  · {n}")
        return "\n".join(L)


def _f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def verify_identity(rep: MarginReport) -> None:
    """★★ **外部标准答案**：用交易所公布的会计恒等式逐日校验数据源。

        本日融资余额 = 前日融资余额 + 本日融资买入额 − 本日融资偿还额

    这不是我们定的规则，是**交易所自己公布的定义**。
    成立 → 这份数据自洽（转抄没出错）；不成立 → 原样报出来，**不调和**。
    """
    for i in range(1, len(rep.rows)):
        a, b = rep.rows[i - 1], rep.rows[i]
        if a.rzye is None or b.rzye is None:
            continue
        rep.checked += 1
        lhs = b.rzye - a.rzye
        rhs = (b.rzmre or 0.0) - (b.rzche or 0.0)
        dev = abs(lhs - rhs)
        if dev > rep.max_deviation:
            rep.max_deviation = dev
        if dev <= IDENTITY_TOL:
            rep.identity_ok += 1
        else:
            rep.big_deviations.append((b.date, lhs - rhs))


def load(code: str) -> MarginReport:
    """取两融数据并做恒等式自校验。**取不到就如实说取不到。**"""
    from data.vendor_bridge import fetch_via_vendor
    rep = MarginReport(code=code)
    try:
        res = fetch_via_vendor("margin_trading", code)
    except Exception as e:                                  # noqa: BLE001
        rep.error = f"{type(e).__name__}: {e}"
        return rep
    if not res.ok:
        rep.error = res.why() if hasattr(res, "why") else (res.error or "取数失败")
        return rep
    rep.source = str(res.source)
    rep.as_of = str(res.as_of)
    raw = [r for r in (res.data or []) if isinstance(r, dict) and r.get("rzye") is not None]
    raw.sort(key=lambda r: str(r.get("date", "")))
    rep.rows = [MarginRow(date=str(r.get("date", "")),
                          **{k: _f(r.get(k)) for k in FIELDS}) for r in raw]
    if len(rep.rows) < 2:
        rep.error = f"只有 {len(rep.rows)} 个交易日的数据，算不出趋势"
        return rep

    verify_identity(rep)

    last, prev = rep.rows[-1], rep.rows[-2]
    if last.rzye is not None and prev.rzye is not None:
        rep.net_margin_buy = last.rzye - prev.rzye
    if len(rep.rows) >= 6 and rep.rows[-6].rzye is not None and last.rzye is not None:
        rep.chg_5d = last.rzye - rep.rows[-6].rzye
    if len(rep.rows) >= 21 and rep.rows[-21].rzye is not None and last.rzye is not None:
        rep.chg_20d = last.rzye - rep.rows[-21].rzye
    if rep.chg_20d is not None:
        y = rep.chg_20d / 1e8
        rep.trend = (f"近 20 个交易日融资余额**增加 {y:.2f} 亿**（杠杆资金在进）"
                     if y > 1 else
                     f"近 20 个交易日融资余额**减少 {abs(y):.2f} 亿**（杠杆资金在退）"
                     if y < -1 else
                     f"近 20 个交易日融资余额基本持平（{y:+.2f} 亿）")

    return rep
