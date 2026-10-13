# -*- coding: utf-8 -*-
"""踩雷排查清单验收 —— **核心是"无法判定"绝不等于"没问题"**。

跑：python -m compute.selftest_minefield   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
这一层最重的失败模式
═══════════════════════════════════════════════════════════════════════════
    把「数据没取到」混进「查过了没问题」。

后果：用户读到「全部通过」，以为都查过了 —— 而实际上有一半根本没查。
**这比不查更糟**，因为它给了一个假的安心。

所以本文件的重点是三态纪律，不是检查本身。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from compute import minefield as MF                            # noqa: E402

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
print("A · ★★★ 三态：UNKNOWN 绝不等于 CLEAR")
print("═" * 74)
check("★ 三个态名字对", (MF.HIT, MF.CLEAR, MF.UNKNOWN), ("命中", "未发现", "无法判定"))
# 没数据源 → 必须是 UNKNOWN
r = MF.CheckResult("X", "假检查", MF.UNKNOWN, "取不到", source="")
check("★★ 无数据源 → UNKNOWN（不是 CLEAR）", r.verdict, MF.UNKNOWN)
check_true("★★ 且 render 里明写「没查，不是没问题」",
           "不是「没问题」" in r.render(), r.render().split("\n")[-1][:60])
# 有数据源、没发现问题 → CLEAR
r2 = MF.CheckResult("X", "假检查", MF.CLEAR, "查了没有", source="someapi")
check_true("★ 有数据源 + 未发现 → render 里**不**出现那句警告",
           "不是「没问题」" not in r2.render(), "")
# 汇总必须三类分列
rep = MF.MinefieldReport(code="X", results=[r, r2])
check("★★ 汇总里命中数", rep.n_hit, 0)
check("★★ 汇总里未发现数", rep.n_clear, 1)
check("★★ 汇总里无法判定数", rep.n_unknown, 1)
txt = rep.render()
check_true("★★★ 汇总三类分列", all(k in txt for k in ("命中 0", "未发现 1", "无法判定 1")),
           txt.split("\n")[0])
check_true("★★★ 汇总明写「无法判定 ≠ 没问题」", "≠「没问题」" in txt, "")
check_true("★★ 汇总明写覆盖不到什么", "覆盖不了" in txt and "财务造假" in txt, "")

print()
print("═" * 74)
print("B · ★★ 检查自己崩了 → 记成 UNKNOWN，**不许消失**")
print("═" * 74)


def _boom(_code):
    raise ZeroDivisionError("故意崩")


_orig = MF.CHECKS
try:
    MF.CHECKS = (_boom,)
    rep2 = MF.run("600519")
    check("★★ 崩掉的检查仍然出现在结果里", len(rep2.results), 1)
    check("★★ 且被记成 UNKNOWN", rep2.results[0].verdict, MF.UNKNOWN)
    check_true("★★ 且写明了崩的原因", "故意崩" in rep2.results[0].detail,
               rep2.results[0].detail[:50])
finally:
    MF.CHECKS = _orig

print()
print("═" * 74)
print("C · ★ 每一项都要报数据源（取不到就说取不到）")
print("═" * 74)
for fn in MF.CHECKS:
    r = fn("600519") if fn.__name__ != "check_goodwill" else fn("600519")
    has_src = bool(r.source)
    is_unknown = r.verdict == MF.UNKNOWN
    check_true(f"★ {r.id:9s} 有源 或 标了无法判定",
               has_src or is_unknown,
               f"源={r.source!r} 态={r.verdict}")

print()
print("═" * 74)
print("D · ★★ 经验阈值必须标出来（不许伪装成监管线）")
print("═" * 74)
gw = MF.check_goodwill("600519")
# ⚠️ 断言要按**实质**写，不要按措辞写：
#    第一版写死「经验阈值」四个字，而代码里写的是「经验值」—— 测试自己挂了，代码没错。
check_true("★★ 商誉检查必须**自认**它没有法规依据",
           "经验" in gw.citation and "无法规依据" in gw.citation, gw.citation[:60])
check_true("★★ 且明说只做提示、不进结论",
           "不进结论" in gw.citation or "只做提示" in gw.citation, gw.citation[:60])
pl = MF.check_pledge("600519")
check_true("★ 质押检查引了法条并说明阈值性质",
           "第25条" in pl.citation and "非监管线" in pl.threshold,
           f"{pl.citation[:40]}｜{pl.threshold}")

print()
print("═" * 74)
print("E · ★★★ 真实数据（联网）：六项都要真的跑出结论")
print("═" * 74)
try:
    rep3 = MF.run("600519")
    print(f"    贵州茅台：共 {len(rep3.results)} 项")
    for x in rep3.results:
        print(f"      {x.verdict:5s} [{x.id:9s}] {x.detail[:58]}")
    check("★★ 六项都在", len(rep3.results), len(MF.CHECKS))
    check_true("★★★ 「无法判定」为 0（说明每一项都真的查了）",
               rep3.n_unknown == 0,
               f"无法判定 {rep3.n_unknown} 项：" +
               str([x.id for x in rep3.results if x.verdict == MF.UNKNOWN]))
    check_true("★ 且没有全部塌成 UNKNOWN（断言不是空转）", rep3.n_clear > 0,
               f"未发现 {rep3.n_clear} 项")
except Exception as e:                                          # noqa: BLE001
    print(f"    （实网不可用，跳过：{type(e).__name__}: {str(e)[:70]}）")

print()
print("═" * 74)
print("F · ★ 适配层：ST 名单必须走适配层（vendor 有已知缺陷）")
print("═" * 74)
try:
    from data import adapters as AD
    rows, path, note = AD.st_snapshot()
    check_true("★ 适配层取到了名单", len(rows) > 0, f"{len(rows)} 只，路径 {path}")
    check_true("★★ 修的是「北交所 ST 重复」（vendor 原报那个错）",
               path == AD.PATH_ADAPTER and "重复" in note, note[:70])
    hit, p2, n2 = AD.find("600519")
    check("★ 茅台不在 ST 名单里", hit, False)
    check_true("★ 返回里带「走的是哪条路」", p2 in (AD.PATH_VENDOR, AD.PATH_ADAPTER), p2)
except Exception as e:                                          # noqa: BLE001
    print(f"    （实网不可用，跳过：{type(e).__name__}: {str(e)[:70]}）")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
