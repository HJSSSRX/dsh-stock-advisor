# -*- coding: utf-8 -*-
"""事件雷达验收 —— **核心是"只说这里有事，不说这是机会"**。

跑：python -m compute.selftest_events   （在 stockharness 目录下）
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from compute import events as EV                               # noqa: E402

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
print("A · ★★★ 纪律：只报事实，不判断机会/陷阱")
print("═" * 74)
rep = EV.EventReport(code="X", events=[
    EV.Event("BB", "回购", EV.POSITIVE, "计划 15~30 亿", source="eastmoney")])
t = rep.render()
check_true("★★★ 输出里明写「不判断它是机会还是陷阱」",
           "不判断它是机会还是陷阱" in t, "")
check_true("★★ 且说明了为什么（回购可能护盘等）",
           "护盘" in t and "作秀" in t, "")
check_true("★ 方向只是分组用的图标，不是结论", "🟢" in t or "🔴" in t, "")
# 方向常量本身的名字要诚实
check("★ 方向叫「偏正面」不叫「看多」", EV.POSITIVE, "偏正面")
check("★ 叫「偏负面」不叫「看空」", EV.NEGATIVE, "偏负面")

print()
print("═" * 74)
print("B · ★★ 没取到的类别必须报出来（不是「没有」）")
print("═" * 74)
rep2 = EV.EventReport(code="X", events=[], missed=["ev_buyback", "ev_forecast"])
t2 = rep2.render()
check_true("★★ 明写「没取到」", "没取到" in t2, "")
check_true("★★ 且明说「不是「没有」」", "不是「没有」" in t2, "")
rep3 = EV.EventReport(code="X", events=[])
check_true("★ 什么都没发现时也不暗示「没问题」",
           "没有检测到异常事件" in rep3.render(), rep3.render()[:60])

print()
print("═" * 74)
print("C · ★ 单项检查崩了 → 记进 missed，不许让整件事消失")
print("═" * 74)


def _boom(_code):
    raise ZeroDivisionError("故意崩")


_orig = EV.CHECKS
try:
    EV.CHECKS = (_boom,)
    r = EV.run("600519")
    check("★ 崩掉的检查被记进 missed", len(r.missed), 1)
    check("★ 且没有事件混进来", len(r.events), 0)
finally:
    EV.CHECKS = _orig

print()
print("═" * 74)
print("D · ★★★ 真实数据（联网）：五个类都要真的跑一遍")
print("═" * 74)
try:
    r = EV.run("600519")
    ran = len(EV.CHECKS) - len(r.missed)
    print(f"    贵州茅台：扫了 {ran}/{len(EV.CHECKS)} 类，发现 {len(r.events)} 件事")
    for e in r.events:
        print(f"      {e.direction} [{e.id:9s}] {e.detail[:56]}")
    check_true("★★★ 五类都跑到了（没有静默失败）", ran == len(EV.CHECKS),
               f"没取到：{r.missed}")
    check_true("★ 且确实发现了事件（断言不是空转）", len(r.events) > 0,
               f"{len(r.events)} 件")
    check_true("★★ 每条事件都带来源和日期",
               all(e.source and e.date for e in r.events),
               str([(e.id, bool(e.source), bool(e.date)) for e in r.events]))
except Exception as e:                                          # noqa: BLE001
    print(f"    （实网不可用，跳过：{type(e).__name__}: {str(e)[:70]}）")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
