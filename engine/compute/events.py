# -*- coding: utf-8 -*-
"""事件雷达 —— **不预测涨跌，只报告"这里发生了不寻常的事"**。

═══════════════════════════════════════════════════════════════════════════
它解决的是哪一类错过
═══════════════════════════════════════════════════════════════════════════
散户错过的机会，大多不是"看不懂"，而是**根本没看到**：
    · 公司在真金白银回购，不知道
    · 大股东在增持，不知道
    · 业绩预告大幅预增，不知道
    · 有大额限售要解禁（利空），不知道

**这些每一条都是公开披露的。**

═══════════════════════════════════════════════════════════════════════════
★★★ 铁律：它**只说"这里有事"，不说"这是机会"**
═══════════════════════════════════════════════════════════════════════════
    可以： 「公司公告回购 30 亿元，已实施 29.99 亿」（事实）
    不可以：「回购说明公司看好自己，可以关注」（**判断**）

**回购也可能是护盘、增持也可能是作秀、预增也可能是一次性损益。**
系统不替用户做这个判断 —— 它只保证**不漏报**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 信号方向只用来**排序和分组**，不用来做结论
POSITIVE, NEGATIVE, NEUTRAL = "偏正面", "偏负面", "中性"


@dataclass
class Event:
    id: str
    name: str
    direction: str          # 只用于分组陈列，**不是结论**
    detail: str = ""
    source: str = ""
    date: str = ""
    magnitude: float = 0.0  # 只用于排序

    def render(self) -> str:
        icon = {POSITIVE: "🟢", NEGATIVE: "🔴", NEUTRAL: "⚪"}.get(self.direction, "⚪")
        d = f"｜{self.date}" if self.date else ""
        return (f"{icon} [{self.id}] {self.name}{d}\n"
                f"      {self.detail}\n"
                f"      来源：{self.source}")


# ══════════════════════════════════════════════════════════════════
def ev_buyback(code: str) -> Optional[Event]:
    """回购。★ 真金白银，是上市公司最实的动作之一。"""
    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("share_buyback", code)
    if not r.ok or not r.data:
        return None
    rows = [x for x in (r.data if isinstance(r.data, list) else [r.data])
            if isinstance(x, dict)]
    if not rows:
        return None
    x = rows[0]
    lo, hi = x.get("amount_lower"), x.get("amount_upper")
    done = x.get("done_amount")
    try:
        lo_f = float(lo) if lo is not None else 0.0
        done_f = float(done) if done is not None else 0.0
    except (TypeError, ValueError):
        lo_f = done_f = 0.0
    if lo_f <= 0 and done_f <= 0:
        return None
    txt = f"计划 {lo_f/1e8:.2f}~{float(hi or 0)/1e8:.2f} 亿元"
    if done_f:
        txt += f"，**已实施 {done_f/1e8:.2f} 亿元**"
    if x.get("done_price_low") and x.get("done_price_high"):
        txt += f"（成交价 {x['done_price_low']}~{x['done_price_high']} 元）"
    return Event("BUYBACK", "回购", POSITIVE, txt,
                 source=getattr(r, "source", "eastmoney"),
                 date=str(x.get("latest_notice", ""))[:10], magnitude=done_f or lo_f)


def ev_holder_trade(code: str) -> Optional[Event]:
    """大股东/高管增减持。"""
    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("holder_trades", code)
    if not r.ok or not r.data:
        return None
    rows = [x for x in (r.data if isinstance(r.data, list) else ([r.data] if r.data else []))
            if isinstance(x, dict)]
    if not rows:
        return None
    add = [x for x in rows if "增" in str(x.get("direction", ""))]
    red = [x for x in rows if "减" in str(x.get("direction", ""))]
    if not add and not red:
        return None
    parts = []
    if add:
        tot = sum(float(x.get("change_shares_10k") or 0) for x in add)
        parts.append(f"**增持 {len(add)} 笔**（合计约 {tot/1e4:.2f} 亿股）")
    if red:
        tot = sum(abs(float(x.get("change_shares_10k") or 0)) for x in red)
        parts.append(f"减持 {len(red)} 笔（合计约 {tot/1e4:.2f} 亿股）")
    who = "、".join(str(x.get("holder", ""))[:14]
                    for x in (add or red)[:2])
    return Event("HOLDER", "大股东/高管增减持",
                 POSITIVE if add and not red else (NEGATIVE if red and not add else NEUTRAL),
                 "；".join(parts) + f"｜{who}",
                 source=getattr(r, "source", "eastmoney"),
                 date=str((add or red)[0].get("end_date", ""))[:10], magnitude=len(add) + len(red))


def ev_forecast(code: str) -> Optional[Event]:
    """业绩预告。★ 大幅预增是"值得看一眼"，大幅预亏是"要当心"。"""
    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("earnings_forecast", code)
    if not r.ok or not r.data:
        return None
    rows = [x for x in (r.data if isinstance(r.data, list) else [r.data])
            if isinstance(x, dict)]
    if not rows:
        return None
    rows.sort(key=lambda x: str(x.get("report_date") or x.get("date") or ""), reverse=True)
    x = rows[0]
    try:
        lo = float(x.get("change_pct_lower"))
        hi = float(x.get("change_pct_upper"))
    except (TypeError, ValueError):
        return None
    d = POSITIVE if lo > 30 else (NEGATIVE if lo < 0 else NEUTRAL)
    return Event("FORECAST", "业绩预告", d,
                 f"最近一期预告同比变动 **{lo:+.2f}% ~ {hi:+.2f}%**"
                 + (f"｜{str(x.get('content',''))[:40]}" if x.get("content") else ""),
                 source=getattr(r, "source", "eastmoney"),
                 date=str(x.get("report_date") or x.get("date") or "")[:10], magnitude=abs(lo))


def ev_unlock(code: str) -> Optional[Event]:
    """限售解禁。"""
    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("restricted_release", code)
    if not r.ok or not r.data:
        return None
    d0 = r.data[0] if isinstance(r.data, list) and r.data else r.data
    if not isinstance(d0, dict):
        return None
    up = d0.get("upcoming") or []
    if not up:
        return None
    f = up[0] if isinstance(up[0], dict) else {}
    return Event("UNLOCK", "限售解禁", NEGATIVE,
                 f"有 **{len(up)} 批**待解禁，最近一批 {f.get('date', '日期未知')}"
                 + (f"（约 {f.get('shares', '')}）" if f.get("shares") else ""),
                 source=getattr(r, "source", "eastmoney"),
                 date=str(f.get("date", ""))[:10], magnitude=len(up))


def ev_pledge_change(code: str) -> Optional[Event]:
    """股权质押（只在比例较高时作为事件报出）。"""
    from data.vendor_bridge import fetch_via_vendor
    r = fetch_via_vendor("equity_pledge", code)
    if not r.ok or not r.data:
        return None
    row = next((x for x in (r.data if isinstance(r.data, list) else [r.data])
                if isinstance(x, dict)), None)
    if row is None:
        return None
    try:
        pct = float(row.get("pledge_ratio_pct") or 0)
    except (TypeError, ValueError):
        return None
    if pct < 20:
        return None
    return Event("PLEDGE", "股权质押比例较高", NEGATIVE,
                 f"全公司质押比例 **{pct:.2f}%**（质押 {row.get('pledge_count')} 笔）",
                 source=getattr(r, "source", "eastmoney"),
                 date=str(row.get("date", ""))[:10], magnitude=pct)


CHECKS = (ev_buyback, ev_holder_trade, ev_forecast, ev_unlock, ev_pledge_change)


@dataclass
class EventReport:
    code: str
    events: list = field(default_factory=list)
    missed: list = field(default_factory=list)   # ★ 没取到的检查也记下来

    def render(self) -> str:
        L = [f"【事件雷达】扫了 {len(CHECKS)} 类，"
             f"**发现 {len(self.events)} 件值得看一眼的事**"
             + (f"｜{len(self.missed)} 类没取到" if self.missed else "")]
        if not self.events:
            L.append("  （本次没有检测到异常事件）")
        for e in sorted(self.events, key=lambda x: -x.magnitude):
            L.append("  " + e.render().replace("\n", "\n  "))
        if self.missed:
            L.append(f"  ⚪ 没取到的类别：{'、'.join(self.missed)}"
                     " —— **这几类本次没查，不是「没有」**")
        L.append("  ⚠️ **系统只报告「这里发生了不寻常的事」，"
                 "不判断它是机会还是陷阱** ——"
                 " 回购可能是护盘、增持可能是作秀、预增可能来自一次性损益。")
        return "\n".join(L)


def run(code: str) -> EventReport:
    out = EventReport(code=code)
    for fn in CHECKS:
        try:
            e = fn(code)
        except Exception:                                    # noqa: BLE001
            out.missed.append(fn.__name__.replace("ev_", ""))
            continue
        if e is not None:
            out.events.append(e)
    return out
