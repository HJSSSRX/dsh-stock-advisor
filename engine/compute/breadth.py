# -*- coding: utf-8 -*-
"""市场宽度 —— **全市场情绪快照**（回答「今天市场怎么样」）。

═══════════════════════════════════════════════════════════════════
为什么要有这一层
═══════════════════════════════════════════════════════════════════
实测豆包（2026-10-12）能给出这样的回答：
    「沪指 3797.24（-0.43%）… 成交 1.54 万亿，缩量 3627 亿
      上涨 1167 家，下跌 4325 家
      涨停 48 只，跌停 23 只，封板未遂 32 只，封板率约 60%」
**这些我们原来全都没有。** 我们的 a-stock-data 桥里：
    · `limit_up_sentiment`  **失效**（逐日返回全 0）
    · `em_zt_pool` 等四池     **失效**（返回 0 条）
    · `ths_limit_up_pool`     **失效**（返回 0 条）
→ 三个现成端点全坏了。**但原料还在**：`tdx_daily_package(date)` 给全市场日线。
**这一层就是从原料自己算出来。**

═══════════════════════════════════════════════════════════════════
★★★ 三个坑（都是实测踩出来的）
═══════════════════════════════════════════════════════════════════
① **`000001` 在通达信包里是「上证指数」不是「平安银行」**
   同一个代码在不同市场含义不同 → **必须按 (market, code) 配对识别**，
   只看代码会把指数当股票算进去。

② **成交额不能把全表加起来**
   全表 52,158 行包含 债券/ETF/LOF/可转债/指数，
   加总得到 **136 万亿**（A 股日成交的 90 倍）。
   **正确做法：取指数行的 amount**
       上证指数 999999 的 amount = 沪市成交额
       深证成指 399001 的 amount = 深市成交额
   实测 7149 + 8229 = **15378 亿 = 1.54 万亿** —— 与豆包完全一致 ✓

③ **涨停判定必须按板块取限幅，ST 是 5%**
   用硬编码 10/20/30 会**漏掉 ST 股**（实测：54 只里 8 只是 ST）。
   本模块用 `compute/rules.limit_pct()` —— 它处理了
   主板 10% / 创业板科创板 20% / 北交所 30% / **ST 5%** / 新股上市首日不设限。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import pandas as pd

from .rules import Status, limit_pct


@dataclass
class MarketBreadth:
    """某交易日的全市场情绪快照。"""
    date: str = ""
    # 成交与家数
    amount_yi: Optional[float] = None        # 沪深成交额（亿元），取指数行
    amount_sh_yi: Optional[float] = None
    amount_sz_yi: Optional[float] = None
    stocks: int = 0                          # 当日有成交的股票数
    up: int = 0
    down: int = 0
    flat: int = 0
    # 涨跌停
    limit_up: int = 0                        # 收盘在涨停价
    limit_down: int = 0
    limit_up_st: int = 0                     # 其中 ST 股（5% 限幅）
    limit_up_gem_star: int = 0               # 其中创业板/科创板（20%）
    limit_up_main: int = 0                   # 其中主板（10%）
    limit_up_bse: int = 0                    # 其中北交所（30%）
    # 封板
    opened_up: int = 0                       # 盘中触及涨停但收盘没封住
    seal_rate: Optional[float] = None        # 封板率 = 封住 /（封住+打开）
    # 指数
    indices: dict = field(default_factory=dict)
    # 元信息
    source: str = ""
    caliber: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "日期": self.date,
            "成交额_亿": self.amount_yi,
            "其中沪市_亿": self.amount_sh_yi,
            "其中深市_亿": self.amount_sz_yi,
            "有成交股票数": self.stocks,
            "上涨": self.up, "下跌": self.down, "平盘": self.flat,
            "涨停": self.limit_up, "跌停": self.limit_down,
            "涨停明细": {"主板_10%": self.limit_up_main, "创业板科创板_20%": self.limit_up_gem_star,
                         "ST_5%": self.limit_up_st, "北交所_30%": self.limit_up_bse},
            "封板率": (round(self.seal_rate, 4) if self.seal_rate is not None else None),
            "指数": self.indices,
            "来源": self.source,
            "口径": self.caliber,
            "提示": self.notes,
        }


CALIBER = (
    "全市场宽度。**成交额取指数行的 amount**（上证指数=沪市、深证成指=深市），"
    "不是全表加总 —— 全表含债券/ETF/指数，加总会得到 90 倍的数字。"
    "**涨跌停用 `rules.limit_pct()` 按板块+ST 取限幅**（主板10 / 双创20 / 北交所30 / ST5），"
    "收盘价触及涨停价即计入。**与媒体口径的差异主要来自 ST 的 5%** —— "
    "多数数据源按 10% 判 ST，会漏掉。"
)


def _q2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _is_stock(market: str, code: str) -> bool:
    """按 **(market, code) 配对**识别股票。

    ★ 只看代码会错：`000001` 在 sh 是上证指数、在 sz 才是平安银行。
    """
    c, m = str(code).zfill(6), str(market).lower()
    if m == "sh":
        return c.startswith(("600", "601", "603", "605", "688", "689"))
    if m == "sz":
        return c.startswith(("000", "001", "002", "003", "300", "301"))
    if m == "bj":
        return c.startswith(("43", "83", "87", "92"))
    return False


def breadth_from_package(df: pd.DataFrame, *, on: str, source: str = "tdx") -> MarketBreadth:
    """从通达信盘后包（全市场日线）算市场宽度。

    df 需含列：market / code / name / prev_close / open / high / low / close / amount
    """
    out = MarketBreadth(date=on, source=source, caliber=CALIBER)
    if df is None or len(df) == 0:
        out.notes.append("输入为空")
        return out

    d = df.copy()
    d["code"] = d["code"].astype(str).str.zfill(6)
    d["mkt"] = d["market"].astype(str).str.lower()

    # ── 成交额：取指数行（不是全表加总）──
    for code, attr, nm in (("999999", "amount_sh_yi", "上证指数"),
                           ("399001", "amount_sz_yi", "深证成指")):
        row = d[d["code"] == code]
        if len(row):
            a = pd.to_numeric(row.iloc[0]["amount"], errors="coerce")
            if pd.notna(a):
                setattr(out, attr, float(a) / 1e8)
                out.indices[nm] = {
                    "收盘": float(pd.to_numeric(row.iloc[0]["close"], errors="coerce")),
                    "成交额_亿": float(a) / 1e8,
                }
    if out.amount_sh_yi is not None and out.amount_sz_yi is not None:
        out.amount_yi = out.amount_sh_yi + out.amount_sz_yi
    else:
        out.notes.append("⚠️ 未取到指数行，成交额缺失（**不用全表加总兜底** —— 那会得到 90 倍的数）")

    # ── 创业板指（顺带）──
    row = d[d["code"] == "399006"]
    if len(row):
        out.indices["创业板指"] = {
            "收盘": float(pd.to_numeric(row.iloc[0]["close"], errors="coerce")),
            "成交额_亿": float(pd.to_numeric(row.iloc[0]["amount"], errors="coerce")) / 1e8,
        }

    # ── 只留股票 ──
    stk = d[[_is_stock(m, c) for m, c in zip(d["mkt"], d["code"])]].copy()
    for c in ("prev_close", "close", "low", "high", "amount"):
        if c in stk.columns:
            stk[c] = pd.to_numeric(stk[c], errors="coerce")
    stk = stk.dropna(subset=["prev_close", "close"])
    stk = stk[(stk["amount"].fillna(0) > 0) & (stk["prev_close"] > 0)]
    out.stocks = len(stk)

    up = down = flat = 0
    lu = ld = opened = 0
    lu_main = lu_gs = lu_st = lu_bse = 0
    uncertain = 0
    for _, r in stk.iterrows():
        pc, cl = float(r["prev_close"]), float(r["close"])
        if pc <= 0:
            continue
        if cl > pc:
            up += 1
        elif cl < pc:
            down += 1
        else:
            flat += 1

        nm = str(r["name"])
        st = Status.ST if ("ST" in nm[:5] or nm.startswith("*")) else Status.NORMAL
        try:
            rule = limit_pct(str(r["code"]), on, st)
        except Exception:                      # noqa: BLE001
            continue
        if rule.limit_pct is None:
            continue
        if rule.uncertain:
            uncertain += 1
        p = float(rule.limit_pct) * 100
        lu_price = _q2(pc * (1 + p / 100))
        ld_price = _q2(pc * (1 - p / 100))
        lo = float(r["low"]) if pd.notna(r.get("low")) else cl
        hi = float(r["high"]) if pd.notna(r.get("high")) else cl

        if cl >= lu_price - 0.001:
            lu += 1
            if p <= 5.5:
                lu_st += 1
            elif p <= 10.5:
                lu_main += 1
            elif p <= 20.5:
                lu_gs += 1
            else:
                lu_bse += 1
        elif pd.notna(r.get("high")) and hi >= lu_price - 0.001:
            opened += 1                        # 盘中触及但收盘没封住
        if cl <= ld_price + 0.001:
            ld += 1

    out.up, out.down, out.flat = up, down, flat
    out.limit_up, out.limit_down = lu, ld
    out.limit_up_main, out.limit_up_gem_star = lu_main, lu_gs
    out.limit_up_st, out.limit_up_bse = lu_st, lu_bse
    out.opened_up = opened
    denom = lu + opened
    out.seal_rate = (lu / denom) if denom else None
    if uncertain:
        out.notes.append(f"{uncertain} 只的限幅判定标记为「不确定」（新上市/特殊情形），已按最保守处理")
    out.notes.append(
        "★ 涨停数含 **ST 股（5% 限幅）**。多数媒体只按 10% 判，会少报 —— "
        f"本次 ST 涨停 {lu_st} 只，去掉后为 {lu - lu_st} 只。"
    )
    return out


def market_snapshot(on: Optional[str] = None) -> MarketBreadth:
    """取某交易日的市场宽度快照（自动拉全市场日线）。"""
    if on is None:
        from .load import load_index_kline
        di, _ = load_index_kline("000001", bars=5)
        on = str(di["date"][-1])[:10]

    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("tdx_package", "", {"date": on})
    if not r.ok:
        out = MarketBreadth(date=on)
        out.notes.append(f"取全市场日线失败：{r.error}")
        return out
    rows = r.data if isinstance(r.data, list) else []
    if not rows:
        out = MarketBreadth(date=on)
        out.notes.append("全市场日线为空（该日通达信盘后包尚未发布；**通常收盘后数小时才有**）")
        return out
    df = pd.DataFrame(rows)
    return breadth_from_package(df, on=on, source=r.source)
