# -*- coding: utf-8 -*-
"""可信度分档验收。

跑：python -m evidence.selftest_trust   （在 stockharness 目录下）

★ 这一层要证明的是：**它能把"不可验证"的东西挡在结论之外。**
  所以反例测试比正例重要 —— 一个永远返回 True 的 can_enter_conclusion() 也能过正例。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from evidence.trust import (
    LEVELS, SOURCES, VERIFIABILITY, audit, can_enter_conclusion, spec_of,
    unverifiable_kinds,
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


print("═" * 72)
print("A · 分档总览")
print("═" * 72)
a = audit()
print(f"    登记 {a['总数']} 个源：可进结论 {a['可进结论']}，不可进结论 {a['不可进结论']}")
print(f"    按等级：{a['按等级']}")
print(f"    ★ 不可跨源验证的：{a['不可跨源验证']}")
check_true("登记了源", a["总数"] > 15, f"{a['总数']} 个")

print()
print("═" * 72)
print("B · ★★ 反例：不能验证的必须被挡在结论外")
print("═" * 72)
# 这三个是实测确认「保真但不可验证」的
for kind, why in (("fundflow", "东财自家口径，无第二源"),
                  ("concept_blocks", "三家分类不同，无唯一答案"),
                  ("hot_rank", "散户关注度，与基本面无关")):
    check(f"★ {kind} 不能进结论", can_enter_conclusion(kind), False)
    s = spec_of(kind)
    check_true(f"  且标明了原因", bool(s and s.verifiability in
                                      ("no_second_source", "definitional")),
               f"{s.verifiability if s else '-'}")

# 反例中的反例：如果哪天有人把 fundflow 改成 True，这个测试会红
check_true("★ fundflow 的画像存在且明确标了 no_second_source",
           spec_of("fundflow").verifiability == "no_second_source", "")

print()
print("═" * 72)
print("C · ★ 未登记的源默认**不能**进结论（保守默认）")
print("═" * 72)
check("没登记的 kind → False", can_enter_conclusion("某个不存在的kindxyz"), False)
check("spec_of 未登记 → None", spec_of("某个不存在的kindxyz"), None)
check_true("★ 保守默认：忘了标画像的源**默认进不了结论**",
           can_enter_conclusion("新接的源还没标") is False, "")

print()
print("═" * 72)
print("D · 可验证的源要能进结论")
print("═" * 72)
for kind, why in (("quote", "2400 交易日零差异"),
                  ("kline", "同上"),
                  ("announcement", "PDF 可逐字核对"),
                  ("financial_sina", "5 数字在公告原文找到"),
                  ("valuation_history", "与腾讯 PE/PB 差 <2.3%")):
    check(f"{kind} 可进结论", can_enter_conclusion(kind), True)

print()
print("═" * 72)
print("E · 每个源必须有完整的画像字段")
print("═" * 72)
bad = []
for k, v in SOURCES.items():
    if v.level not in LEVELS:
        bad.append(f"{k}: 等级 {v.level} 未定义")
    if v.verifiability not in VERIFIABILITY:
        bad.append(f"{k}: 可验证性 {v.verifiability} 未定义")
    if not v.label:
        bad.append(f"{k}: 缺 label")
check_true("★ 所有源的等级/可验证性都在枚举内", len(bad) == 0, "; ".join(bad[:4]))
check_true("★ 不可进结论的源都写了 note（说明为什么）",
           all(v.note for v in SOURCES.values() if not v.can_enter_conclusion),
           "")

print()
print("═" * 72)
print("F · 可验证性四类都定义了（不然分档是空的）")
print("═" * 72)
for key in ("cross_source", "primary_only", "no_second_source", "definitional"):
    check_true(f"定义了 {key}", key in VERIFIABILITY, VERIFIABILITY[key][:40])
check_true("★ 每个源用的都是已定义的可验证性",
           all(v.verifiability in VERIFIABILITY for v in SOURCES.values()), "")

print()
print("═" * 72)
print("G · 演示：给用户看的说法")
print("═" * 72)
for kind in ("fundflow", "financial_sina"):
    print(f"  ── {kind} ──")
    print("    " + spec_of(kind).describe().replace("\n", "\n    "))

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
