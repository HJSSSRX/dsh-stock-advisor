# -*- coding: utf-8 -*-
"""口径层验收 —— 拦的是 15 亿那种错。

跑：python -m evidence.selftest_caliber   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 这一层唯一的存在理由：**拦住"看起来对但其实错了口径"的数字**
═══════════════════════════════════════════════════════════════════
只测正例（"能加总"）没意义。要证明这层有用，必须证明它**能拦住**：
    · 没口径的数字（不许报给用户）
    · 不同口径直接比较（净利润 vs 归属净利润 差 15 亿）
    · 子集口径比增长率（扣非 vs 归属 —— 差的是一次性损益，比出来的增长是假的）
    · 跨报告期加总
    · 金额与比率混用
    · 破坏了的加总关系
"""
from __future__ import annotations

import sys
from decimal import Decimal as D

sys.path.insert(0, ".")

from evidence.caliber import (
    CALIBERS, Calibered, alias_of, caliber_of_label, can_compare, can_sum,
    is_ratio, name_of, verify_additive,
)

N_PASS = N_FAIL = 0


def check(name: str, got, want) -> None:
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


def check_true(name: str, cond: bool, detail: str = "") -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      {detail}")


# 实测自茅台 2026 半年报
NET = Calibered(D("46033330566.78"), "net_profit", "元", "cninfo公告", "2026-06-30",
                verify="verified", raw_text="46,033,330,566.78")
PARENT = Calibered(D("44516880421.86"), "net_profit_parent", "元", "cninfo公告",
                   "2026-06-30", verify="verified", raw_text="44,516,880,421.86")
MINORITY = Calibered(D("1516450144.92"), "minority_interest", "元", "cninfo公告",
                     "2026-06-30", verify="verified", raw_text="1,516,450,144.92")
DEDUCTED = Calibered(D("44464207646.01"), "net_profit_parent_deducted", "元",
                     "cninfo公告", "2026-06-30", verify="verified")
REVENUE = Calibered(D("90703260964.48"), "operating_revenue", "元", "cninfo公告",
                    "2026-06-30", verify="verified")
EPS = Calibered(D("35.57"), "eps", "元/股", "cninfo公告", "2026-06-30", verify="verified")

# ══════════════════════════════════════════════════════════════════
print("═" * 72)
print("A · ★ 正例：构成项加总 = 合计（这是最硬的核验）")
print("═" * 72)
c = verify_additive(NET, [PARENT, MINORITY])
check_true("归属净利润 + 少数股东损益 = 净利润", c.ok, c.reason)
print(f"      {PARENT.value} + {MINORITY.value} = {PARENT.value + MINORITY.value}")
print(f"      声称合计 {NET.value}")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 72)
print("B · ★★ 反例：没口径的数字，不许报给用户")
print("═" * 72)
naked = Calibered(D("46033330566.78"), "", "元", "某接口", "2026-06-30")
try:
    s = naked.render()
    check_true("无口径 render() 必须抛错", False, f"竟然返回了「{s}」")
except ValueError as e:
    check_true("无口径 render() 抛 ValueError", True, str(e)[:60])
try:
    naked.yin()
    check_true("无口径 yin() 也必须抛错", False, "竟然返回了")
except ValueError:
    check_true("无口径 yin() 抛 ValueError", True)
try:
    Calibered(D("1"), "某个不存在的口径").render()
    check_true("未知口径必须抛错", False, "竟然返回了")
except ValueError as e:
    check_true("未知口径抛 ValueError", True, str(e)[:50])

print()
print("═" * 72)
print("C · ★★ 反例：这个 15 亿的坑必须被拦住")
print("═" * 72)
c1 = can_compare(NET, PARENT)
check_true("★ 净利润 vs 归属净利润 → 不许直接比较", not c1.ok, c1.reason)
print(f"        提示：{c1.hint}")
check_true("  两者差 15.16 亿", abs(NET.value - PARENT.value) == D("1516450144.92"),
           f"实差 {NET.value - PARENT.value}")

c2 = can_compare(PARENT, DEDUCTED)
check_true("★ 归属净利润 vs 扣非净利润 → 不许比增长率", not c2.ok, c2.reason)
print(f"        提示：{c2.hint}")

c3 = can_compare(PARENT, REVENUE)
check_true("★ 归属净利润 vs 营业收入 → 不许比较（不同口径）", not c3.ok, c3.reason)

c4 = can_compare(NET, EPS)
check_true("★ 金额 vs 每股收益（比率）→ 不许比较", not c4.ok, c4.reason)

c5 = can_compare(PARENT, Calibered(PARENT.value, "net_profit_parent", "元",
                                   "另一接口", "2026-06-30"))
check_true("同一口径可以比较", c5.ok, c5.reason)

# 跨报告期
old = Calibered(D("46986681449.24"), "net_profit_parent", "元", "cninfo", "2025-06-30")
c6 = can_compare(PARENT, old)
check_true("同一口径但跨报告期 → 仍可比较（增长率才有意义）", c6.ok, c6.reason)

print()
print("═" * 72)
print("D · ★★ 反例：不许乱加总")
print("═" * 72)
c = can_sum([PARENT, DEDUCTED])
check_true("★ 归属净利润 + 扣非净利润 → 拒绝（会重复计算）", not c.ok, c.reason)
c = can_sum([PARENT, MINORITY, DEDUCTED])
check_true("★ 三项相加（含子集）→ 拒绝", not c.ok, c.reason)
c = can_sum([PARENT, Calibered(D("1"), "net_profit_parent", "元", "s", "2025-12-31")])
check_true("★ 跨报告期相加 → 拒绝", not c.ok, c.reason)
c = can_sum([PARENT, Calibered(D("1"), "net_profit_parent", "万元", "s", "2026-06-30")])
check_true("★ 单位不一致 → 拒绝", not c.ok, c.reason)
c = can_sum([PARENT, MINORITY])
check_true("构成项相加 → 允许", c.ok, c.reason)

print()
print("═" * 72)
print("E · ★ 反例：破坏了的加总关系必须被发现")
print("═" * 72)
bad = Calibered(D("46033330567.78"), "net_profit", "元", "s", "2026-06-30")
c = verify_additive(bad, [PARENT, MINORITY])
check_true("★ 合计改 1 分 → 不闭合，被发现", not c.ok, c.reason[:80])

c = verify_additive(NET, [PARENT, Calibered(D("999"), "minority_interest", "元",
                                            "s", "2026-06-30")])
check_true("★ 构成项改掉 → 不闭合，被发现", not c.ok, c.reason[:80])

print()
print("═" * 72)
print("F · 口径识别：只认原文写法，不认外号")
print("═" * 72)
LABELS = {
    "归属于上市公司股东的净利润": "net_profit_parent",
    "归属于上市公司股东的扣除非经常性损益的净利润": "net_profit_parent_deducted",
    "五、净利润（净亏损以“-”号填 列）": "net_profit",
    "2.少数股东损益（净亏损以“-”号填列）": "minority_interest",
    "利润总额": "total_profit",
    "一、营业总收入": "operating_revenue_total",
    "营业成本": "operating_cost",
    "减：所得税费用": "income_tax",
    "（二）按所有权归属分类": None,
}
for label, want in LABELS.items():
    check(f"「{label[:24]}…」→ {want}", caliber_of_label(label), want)
check_true("★ 口语缩写「归属净利润」判不出（认外号＝允许模糊匹配）",
           caliber_of_label("归属净利润") is None, "")
check_true("★ 归属口径**不会**被误判成合并口径（这就是 15 亿的来源）",
           caliber_of_label("归属于上市公司股东的净利润") != "net_profit", "")

print()
print("═" * 72)
print("G · 报给用户的说法必须自带口径")
print("═" * 72)
s1 = PARENT.yin()
check_true("亿元说法带口径", "归属" in s1 and "亿" in s1, s1)
s2 = NET.yin()
check_true("净利润口径与归属口径说法不同", s1 != s2, f"{s1}  vs  {s2}")
print(f"      净利润（合并）：{s2}")
print(f"      归属母公司   ：{s1}")
check_true("别名表可用", alias_of("net_profit_parent") == "归属净利润", "")
check_true("比率口径被识别", is_ratio("eps") and is_ratio("roe"), "")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
