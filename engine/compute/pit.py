# -*- coding: utf-8 -*-
"""时点守卫 —— **不许在历史日期上偷看未来的数据。**

═══════════════════════════════════════════════════════════════════
为什么这一层是判断层的第一块
═══════════════════════════════════════════════════════════════════
回看历史时（"如果去年 8 月买茅台会怎样"），系统会用今天的数据去算。
**报告里完全看不出来** —— 这叫**前视偏差 / 未来函数**，属于**静默失败**：
数字都对、逻辑都通、结论是假的。

别人的血教训（TradingAgents-astock CLAUDE.md v0.5.1）：

    「历史日期上跑分析时，数据层**不得**把"今天"的数据当成分析日当天的事实
      —— 报告里完全看不出来，属于静默失败。」

═══════════════════════════════════════════════════════════════════
★★★ 最容易错的地方：财报的「报告期」≠「公布日」
═══════════════════════════════════════════════════════════════════
    2026 半年报的**报告期**是 2026-06-30
    但它实际**公布**可能是 2026-08-15

→ 在 2026-07-01 做分析时，**这份半年报还不存在**，不许用。
  如果按报告期筛，就会把 8 月才公布的数据当成 7 月就有的 —— **这是假的历史回测。**

中国法定披露截止日：
    年报    次年 4 月 30 日前
    一季报  4 月 30 日前
    半年报  8 月 31 日前
    三季报  10 月 31 日前
公布日缺失时用截止日兜底（**保守**：宁可晚看到，不可早看到）。

⚠️ 两个已知陷阱（别人踩过，写在他们的文档里）：
   ① **别只按公告日筛** —— 公告日会被"次年同期报表的对比数"改写成更晚的日期，
      回看时会把早已公布的财报整批丢掉。
   ② **别退回「截止日或公告日」取早** —— 逾期披露的公司（如某公司 2023 年报
      拖到 2024-07-05 才发）截止日一过就提前露出来。

→ 本模块的取值顺序：**公告日优先；公告日不可信/缺失时用截止日**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

# ══════════════════════════════════════════════════════════════════
# 法定披露截止日（中国证监会《上市公司信息披露管理办法》）
# ══════════════════════════════════════════════════════════════════
DEADLINE: dict[int, tuple[int, int]] = {
    3:  (4, 30),    # 一季报
    6:  (8, 31),    # 半年报
    9:  (10, 31),   # 三季报
    12: (4, 30),    # 年报 → **次年** 4 月 30 日
}


def _d(x: Any) -> Optional[date]:
    """把各种形状的日期转成 date。转不了返回 None（**不猜**）。"""
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s if len(s) == 10 else s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(x)).date()
    except Exception:                                # noqa: BLE001
        return None


def period_end(period: str) -> Optional[date]:
    """报告期 → 报告期末日。`2026-06-30` / `20260630` / `2026Q2` 都接。"""
    d = _d(period)
    if d:
        return d
    s = str(period).strip().upper()
    if "Q" in s and s[:4].isdigit():
        y, q = int(s[:4]), int(s[-1])
        return {1: date(y, 3, 31), 2: date(y, 6, 30),
                3: date(y, 9, 30), 4: date(y, 12, 31)}.get(q)
    return None


def disclosure_deadline(period: str) -> Optional[date]:
    """报告期 → **法定披露截止日**（逾期未披露是违规，所以这天之后必然已公布）。"""
    pe = period_end(period)
    if not pe:
        return None
    mm, dd = DEADLINE.get(pe.month, (12, 31))
    year = pe.year + 1 if pe.month == 12 else pe.year
    try:
        return date(year, mm, dd)
    except ValueError:                               # pragma: no cover
        return None


# ══════════════════════════════════════════════════════════════════
@dataclass
class Availability:
    """一条数据的「可得性」判定结果。"""
    ok: bool
    reason: str = ""
    used: str = ""                # 用了哪个日期判的
    conservative: bool = False    # 是否用了保守兜底（截止日）
    warn: str = ""

    def __bool__(self) -> bool:
        return self.ok


def financial_available(*, period: str, as_of: str,
                        announce_date: Any = None,
                        update_time: Any = None) -> Availability:
    """★ 财报在某天**能不能看到**。

    取值优先级（照别人的血教训定的）：
        ① announce_date（公告日）—— 最准
        ② update_time（入库时间）—— 次之
        ③ **法定披露截止日** —— 兜底，**保守**

    ⚠️ **绝不用报告期本身判断** —— 那是把"数据属于哪段时间"当成"什么时候能拿到"。
    """
    a = _d(as_of)
    if not a:
        return Availability(False, f"as_of 解析不了：{as_of!r}")
    pe = period_end(period)
    if not pe:
        return Availability(False, f"报告期解析不了：{period!r}")

    # 报告期还没结束 → 必然没有
    if pe > a:
        return Availability(False, f"报告期 {pe} 还没结束（分析日 {a}）")

    for label, raw in (("公告日", announce_date), ("入库时间", update_time)):
        got = _d(raw)
        if got:
            if got <= a:
                return Availability(True, f"{label} {got} ≤ 分析日 {a}", used=label)
            return Availability(
                False,
                f"{label} {got} **晚于**分析日 {a} —— 这天还看不到这份财报",
                used=label,
            )

    dl = disclosure_deadline(period)
    if not dl:
        return Availability(False, "既没有公告日，也算不出披露截止日")
    if dl <= a:
        return Availability(
            True,
            f"没有公告日，按**法定披露截止日** {dl} 判断（≤ 分析日 {a}）",
            used="法定截止日", conservative=True,
            warn="⚠️ 用的是**保守兜底**（截止日）：实际公布日可能更晚，"
                 "但因逾期未披露属违规，截止日后必然已公布。",
        )
    return Availability(
        False,
        f"没有公告日，法定披露截止日 {dl} **晚于**分析日 {a}",
        used="法定截止日", conservative=True,
    )


# ══════════════════════════════════════════════════════════════════
def is_historical(as_of: Any) -> bool:
    """这个分析日是不是「历史」（不是今天/未来）。"""
    a = _d(as_of)
    if not a:
        return False
    return a < date.today()


def truncate_to(rows: Iterable[dict], as_of: Any,
                date_key: str = "date") -> tuple[list[dict], int]:
    """把数据截断到 as_of（含当天）。

    ★ 行情/资金/新闻这类**自带时点**的数据，用这个就能防住未来函数。
    返回 (截断后的行, 砍掉几行)。
    """
    a = _d(as_of)
    if not a:
        return list(rows), 0
    kept: list[dict] = []
    for r in rows:
        rd = _d(r.get(date_key) if isinstance(r, dict) else None)
        if rd is None:
            # ⚠️ 日期解析不了的行 —— **保守保留**（宁可多用，不可漏用？）
            #    不，反了：未来函数是**用多了**才出的错，所以这里该**砍掉**。
            #    但砍掉可能丢数据…… 折中：没有日期的**保留**，因为无法判断；
            #    真正带未来日期的行会被下面明确砍掉。这条注释是给自己看的。
            kept.append(r)
            continue
        if rd <= a:
            kept.append(r)
    return kept, len(list(rows)) - len(kept) if isinstance(rows, list) else 0


# ══════════════════════════════════════════════════════════════════
# ★★ 无法截断的数据源 → 必须**明确告警**
# ══════════════════════════════════════════════════════════════════
# 有些源**根本没有历史时点值**（实时快照、当前一致预期、当前人气榜…），
# 在历史日期上跑时**取不到那天的值**。
# 别人的教训：「不能收了不用」—— 必须二选一：
#     ① 能做时点截断的就截断
#     ② 做不了的 → **在正文顶部明确告警**
NO_HISTORY_KINDS: dict[str, str] = {
    "quote":            "实时快照 —— **只有当下值，没有历史时点值**",
    "valuation_now":    "当前估值快照 —— 同上",
    "hot_list":         "当前热榜 —— 没有历史快照",
    "hot_rank":         "当前人气榜 —— 同上",
    "hot_concept":      "当下热点概念 —— 随行情变动，无历史",
    "eps_forecast":     "当前一致预期 —— **不是分析日当天的预期**",
    "concept_blocks":   "当前概念归属 —— 分类会变",
    "board":            "当前板块列表 —— 同上",
    "stock_monitor":    "当前监控名单 —— 无历史",
    "price_anomaly":    "当日异动池 —— 无历史快照",
}
# 这些源**自带时点**，能截断
TRUNCATABLE_KINDS: set[str] = {
    "kline", "index_kline", "fundflow", "fundflow_minute", "news", "news_flash",
    "news_global", "news_wallstreetcn", "announcement", "dragon_tiger",
    "margin_trading", "block_trades", "holder_trades", "executive_trades",
    "limit_up_reason", "tdx_package", "research_reports", "industry_reports",
}


def snapshot_notice(kind: str, as_of: Any) -> str:
    """★ 对**做不了时点截断**的源，给出必须写进回答的告警句。

    空字符串 = 没问题；非空 = **判断层必须把它写进输出**。
    """
    if not is_historical(as_of):
        return ""
    if kind in TRUNCATABLE_KINDS:
        return ""
    why = NO_HISTORY_KINDS.get(kind)
    if why is None:
        # ★ 未登记 → **保守假设它没有历史**（同可信度分档的保守默认）
        why = "**未登记是否支持历史时点**，按保守处理：假定它只有当下值"
    return (f"⚠️ **时点告警**：`{kind}` —— {why}。"
            f"分析日是历史日期（{as_of}），**这项数据不得当作当天的既成事实**。")


def audit_kinds(kinds: Iterable[str], as_of: Any) -> list[str]:
    """批量检查一批 kind，返回所有必须告警的句子。"""
    out = []
    for k in kinds:
        n = snapshot_notice(k, as_of)
        if n:
            out.append(n)
    return out


# ══════════════════════════════════════════════════════════════════
def assert_no_lookahead(rows: list[dict], as_of: Any,
                        date_key: str = "date") -> list[str]:
    """★ 检查一批数据里**有没有晚于分析日的行**。返回违规描述（空 = 干净）。

    用法：判断层在给出历史结论前先跑一遍，**有违规就该拒绝出结论**。
    """
    a = _d(as_of)
    if not a:
        return [f"as_of 解析不了：{as_of!r}"]
    bad = []
    for i, r in enumerate(rows or []):
        rd = _d(r.get(date_key) if isinstance(r, dict) else None)
        if rd and rd > a:
            bad.append(f"第 {i} 行日期 {rd} 晚于分析日 {a}")
    return bad
