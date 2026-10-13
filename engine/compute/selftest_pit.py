# -*- coding: utf-8 -*-
"""时点守卫验收 —— **拦的是"回看历史时偷看未来"。**

跑：python -m compute.selftest_pit   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 这一层拦的是**静默失败**
═══════════════════════════════════════════════════════════════════
「取不到数据」是看得见的；「用了当时还不存在的数据」是**看不见**的 ——
数字都对、逻辑都通、结论是假的。

所以验收重点全是反例：
    在半年报公布**之前**的分析日 → 断言看不到这份半年报
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from compute.pit import (          # noqa: E402
    NO_HISTORY_KINDS, TRUNCATABLE_KINDS, assert_no_lookahead, audit_kinds,
    disclosure_deadline, financial_available, is_historical, period_end,
    snapshot_notice, truncate_to,
)

N_PASS = N_FAIL = 0


def check_true(name: str, cond: bool, detail: str = "") -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      {detail}")


def check(name: str, got, want) -> None:
    check_true(name, got == want, f"期望 {want!r}，实际 {got!r}")


print("═" * 74)
print("A · ★★ 反例：财报公布之前，不许看到它（这就是 15 亿那个坑的近亲）")
print("═" * 74)
# 真实情形：2026 半年报，报告期 2026-06-30，公告日 2026-08-15
PERIOD = "2026-06-30"
ANNOUNCE = "2026-08-15"

a1 = financial_available(period=PERIOD, as_of="2026-07-01", announce_date=ANNOUNCE)
check_true("★★ 7月1日分析 → **看不到**半年报（8月15才公布）", not a1.ok, a1.reason)

a2 = financial_available(period=PERIOD, as_of="2026-08-14", announce_date=ANNOUNCE)
check_true("★ 公布前一天 → 仍看不到", not a2.ok, a2.reason)

a3 = financial_available(period=PERIOD, as_of="2026-08-15", announce_date=ANNOUNCE)
check_true("★ 公布当天 → 能看到（含当天）", a3.ok, a3.reason)

a4 = financial_available(period=PERIOD, as_of="2026-09-01", announce_date=ANNOUNCE)
check_true("公布后 → 能看到", a4.ok, a4.reason)
check("  用了公告日判定", a4.used, "公告日")

print()
print("═" * 74)
print("B · ★★ 反例：绝不能用「报告期」当「可用日」")
print("═" * 74)
# 报告期 6-30，但如果错误地用报告期判断，7月1日就会"看得见"
a5 = financial_available(period=PERIOD, as_of="2026-07-01", announce_date=ANNOUNCE)
check_true("★ 报告期已过 ≠ 数据已公布（7月1日报告期已过但仍然看不到）",
           not a5.ok, "如果这里 ok=True 就是按报告期判断了，那是未来函数")
# 报告期还没结束 → 必然没有
a6 = financial_available(period="2026-12-31", as_of="2026-10-12", announce_date=None)
check_true("★ 报告期还没结束 → 必然看不到", not a6.ok, a6.reason)

print()
print("═" * 74)
print("C · 法定披露截止日（没有公告日时的保守兜底）")
print("═" * 74)
check("半年报截止日 = 当年 8/31", str(disclosure_deadline("2026-06-30")), "2026-08-31")
check("三季报截止日 = 当年 10/31", str(disclosure_deadline("2026-09-30")), "2026-10-31")
check("一季报截止日 = 当年 4/30", str(disclosure_deadline("2026-03-31")), "2026-04-30")
check("年报截止日 = **次年** 4/30", str(disclosure_deadline("2025-12-31")), "2026-04-30")

a7 = financial_available(period=PERIOD, as_of="2026-09-15", announce_date=None)
check_true("没公告日 + 已过截止日 → 保守放行", a7.ok, a7.reason)
check_true("  且标记为「保守兜底」", a7.conservative, "")
check_true("  且带告警（说明是兜底不是实际公布日）", bool(a7.warn), a7.warn[:50])

a8 = financial_available(period=PERIOD, as_of="2026-07-01", announce_date=None)
check_true("★ 没公告日 + 未到截止日 → **不放行**（宁可晚看到）", not a8.ok, a8.reason)

print()
print("═" * 74)
print("D · ★★ 反例：逾期披露的公司，不许提前露出来")
print("═" * 74)
# 真实案例形态：某公司 2023 年报拖到 2024-07-05 才发（截止日是 2024-04-30）
a9 = financial_available(period="2023-12-31", as_of="2024-05-01",
                         announce_date="2024-07-05")
check_true("★★ 截止日已过但公司逾期未发 → **仍不许露出来**（有公告日时以公告日为准）",
           not a9.ok, a9.reason)
a10 = financial_available(period="2023-12-31", as_of="2024-07-05",
                          announce_date="2024-07-05")
check_true("★ 它真发的那天才可见", a10.ok, a10.reason)
check_true("  且**不是**保守兜底（用了真实公告日）", not a10.conservative, "")

print()
print("═" * 74)
print("E · 入库时间兜底（公告日缺失时）")
print("═" * 74)
a11 = financial_available(period=PERIOD, as_of="2026-07-01",
                          announce_date=None, update_time="2026-08-16")
check_true("公告日缺失 → 用入库时间判 → 7月1日看不到",
           not a11.ok, a11.reason)
a12 = financial_available(period=PERIOD, as_of="2026-08-20",
                          announce_date=None, update_time="2026-08-16 03:00:00")
check_true("入库时间早于分析日 → 可见", a12.ok, a12.reason)
check("  用了入库时间", a12.used, "入库时间")

print()
print("═" * 74)
print("F · 数据截断（行情/资金/新闻这类自带时点的）")
print("═" * 74)
rows = [
    {"date": "2026-06-25", "close": 1},
    {"date": "2026-06-30", "close": 2},
    {"date": "2026-07-01", "close": 3},   # ← 分析日是 6-30，这天不该有
    {"date": "2026-08-15", "close": 4},
]
kept, cut = truncate_to(rows, "2026-06-30")
check("★ 截断后只剩 2 行（6-25 / 6-30）", len(kept), 2)
check_true("★ 7-01 那行被砍掉（它是未来）",
           all(r["date"] != "2026-07-01" for r in kept), f"{[r['date'] for r in kept]}")
check("  报告砍掉 2 行", cut, 2)

bad = assert_no_lookahead(rows, "2026-06-30")
check_true("★ assert_no_lookahead 报出 2 条违规", len(bad) == 2, f"{bad}")
clean = assert_no_lookahead(kept, "2026-06-30")
check_true("截断后断言通过", len(clean) == 0, f"{clean}")

print()
print("═" * 74)
print("G · ★★ 做不了截断的源 → 必须告警（「不能收了不用」）")
print("═" * 74)
HIST = "2026-07-01"
n_quote = snapshot_notice("quote", HIST)
check_true("★ quote 在历史日 → 必须告警", bool(n_quote), n_quote[:70])
check_true("  告警里点明「不得当作当天既成事实」", "既成事实" in n_quote, "")
check_true("  告警里给了原因", "只有当下值" in n_quote, "")

n_kline = snapshot_notice("kline", HIST)
check_true("kline 自带时点 → 不告警（能截断）", n_kline == "", f"「{n_kline[:40]}」")

n_today = snapshot_notice("quote", "2099-01-01")
check_true("非历史日期 → 不告警", n_today == "", f"「{n_today[:40]}」")

n_unknown = snapshot_notice("some_unknown_kind_xyz", HIST)
check_true("★★ 未登记的 kind → **保守假设它没有历史**，也告警",
           bool(n_unknown), n_unknown[:70])

multi = audit_kinds(["quote", "kline", "eps_forecast"], HIST)
check("批量检查：3 个里 2 个要告警", len(multi), 2)

print()
print("═" * 74)
print("H · 边界与解析")
print("═" * 74)
check("period_end 接 20260630", str(period_end("20260630")), "2026-06-30")
check("period_end 接 2026Q2", str(period_end("2026Q2")), "2026-06-30")
check("period_end 接 2026-09-30", str(period_end("2026-09-30")), "2026-09-30")
check_true("period_end 解析不了 → None（不猜）", period_end("乱码") is None, "")
check_true("财务判定解析不了 as_of → 明确失败", not financial_available(
    period=PERIOD, as_of="乱码", announce_date=ANNOUNCE).ok, "")
check_true("is_historical 判过去", is_historical("2020-01-01"), "")
check_true("is_historical 判未来为 False", not is_historical("2099-01-01"), "")
check_true("无日期的行不参与截断（保留）",
           len(truncate_to([{"close": 1}, {"date": "2026-06-30"}], "2026-06-30")[0]) == 2, "")

print()
print("═" * 74)
print("I · 两张登记表不能重叠（否则判定会自相矛盾）")
print("═" * 74)
overlap = set(NO_HISTORY_KINDS) & TRUNCATABLE_KINDS
check_true("★ 同一个 kind 不能既「无历史」又「可截断」",
           len(overlap) == 0, f"重叠：{overlap}")
check_true("登记了若干无历史源", len(NO_HISTORY_KINDS) >= 5, f"{len(NO_HISTORY_KINDS)} 个")
check_true("登记了若干可截断源", len(TRUNCATABLE_KINDS) >= 8, f"{len(TRUNCATABLE_KINDS)} 个")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
