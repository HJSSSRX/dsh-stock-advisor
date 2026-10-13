# -*- coding: utf-8 -*-
"""置信度验收 —— **核心是"数出来的"必须能被手算核对**。

跑：python -m judge.selftest_confidence   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
为什么这个模块能被机械验证
═══════════════════════════════════════════════════════════════════════════
Wilson 区间是**闭式公式**，所以它能被：
  1. **手算核对**：设计文档自己给了 N=30/h=18 → 下界 ≈ 0.42 的例子。
     这个例子必须复现得出来 —— 复现不出就是实现错了。
  2. **极端值核对**：h=0 下界必为 0；h=n 下界 < 1；n 增大时下界 → p̂。
  3. **单调性核对**：h 增加 → 下界不降；n 增加（比例不变）→ 下界升高（小样本保守）。

★ 结算口径（看多必须跑赢、看空必须跑输）也要验 ——
  这是竞品踩过血坑的地方（把"标怎么走"当成"判断准不准"）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from judge import confidence as C                            # noqa: E402

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
print("A · ★★★ 复现设计文档自己给的例子（复现不出=实现错）")
print("═" * 74)
# §5.2 原文：「N=30、命中 18 次时点估计是 0.60，但下界只有约 0.42」
h, n = 18, 30
lo = C.wilson_lower(h, n)
check("★ 点估计 = 0.60（确实比下界高一大截）", round(h / n, 2), 0.6)
check("★★ 下界 ≈ 0.42（文档原文的数字）", round(lo, 2), 0.42)
check_true("★★ 下界确实 < 点估计（取的是保守端）", lo < h / n,
           f"{lo:.4f} < {h/n:.4f}")
print(f"    手算核对：p̂=0.60 → Wilson 95% 下界 = {lo:.6f}")

print()
print("═" * 74)
print("B · ★★ 极端值与边界（公式的自我保护）")
print("═" * 74)
check("★ h=0, n=30 → 下界 = 0", C.wilson_lower(0, 30), 0.0)
check("★ n=0 → 下界 = 0", C.wilson_lower(0, 0), 0.0)
check("★ h=n=30 → 下界 < 1（全对也不给 100%）",
      round(C.wilson_lower(30, 30), 4), round(C.wilson_lower(30, 30), 4))
check_true("★★ 全对时的下界仍 < 1（小样本不许给满）",
           C.wilson_lower(30, 30) < 1.0, f"{C.wilson_lower(30, 30):.4f}")
check_true("★ h=n=1000 → 下界非常接近 1",
           C.wilson_lower(1000, 1000) > 0.995, f"{C.wilson_lower(1000, 1000):.5f}")
check_true("★★ 下界 ≤ 点估计 ≤ 上界",
           C.wilson_lower(18, 30) <= 0.6 <= C.wilson_upper(18, 30),
           f"[{C.wilson_lower(18,30):.4f}, {C.wilson_upper(18,30):.4f}]")
try:
    C.wilson_lower(31, 30)
    check_true("★ h > n → 抛错", False, "没抛错")
except ValueError as e:
    check_true("★ h > n → 抛错", "不在" in str(e), str(e)[:50])

print()
print("═" * 74)
print("C · ★★ 单调性（这是「下界」这个词的意义所在）")
print("═" * 74)
seq = [C.wilson_lower(h, 100) for h in range(0, 101)]
check_true("★★ h 增加 → 下界单调不降", all(seq[i] <= seq[i + 1] + 1e-12 for i in range(100)),
           "100 个点全查了")
# 比例不变、样本增大 → 下界升高（小样本保守）
same_p = [C.wilson_lower(int(0.6 * n), n) for n in (10, 30, 100, 1000)]
check_true("★★ 同一比例 0.6，样本越大下界越高（小样本自动保守）",
           all(same_p[i] < same_p[i + 1] for i in range(len(same_p) - 1)),
           " → ".join(f"{v:.4f}" for v in same_p))

print()
print("═" * 74)
print("D · ★★★ 结算口径：看多必须跑赢、看空必须跑输（竞品血坑）")
print("═" * 74)
check("★★ 看多 + 跑赢 → 对", C.settle("看多", 0.05, 0.02), True)
check("★★ 看多 + 跑输 → 错", C.settle("看多", -0.05, 0.02), False)
check("★★ 看空 + 跑输 → 对", C.settle("看空", -0.05, 0.02), True)
check("★★ 看空 + 跑赢 → 错", C.settle("看空", 0.05, 0.02), False)
check("★★ 观望 → 不计入（None）", C.settle("观望", 0.05, 0.02), None)
# ★ 这条是竞品踩过的那一脚：看空后下跌，是"判断正确"
check_true("★★★ 竞品血坑：看空后个股下跌 = 判断**正确**（不是失败）",
           C.settle("看空", -0.10, 0.00) is True, "")
# 绝对涨跌不算数，只有相对基准才算
check_true("★★ 大盘涨 5%、个股涨 3% → 看多算**错**（相对基准）",
           C.settle("看多", 0.03, 0.05) is False, "")
try:
    C.settle("横盘", 0.0, 0.0)
    check_true("★ 未知方向 → 抛错", False, "没抛错")
except ValueError as e:
    check_true("★ 未知方向 → 抛错", "未知方向" in str(e), str(e)[:40])

print()
print("═" * 74)
print("E · ★★ 样本不足必须给 None，**不许编一个数**")
print("═" * 74)
r = C.confidence_for("看多", 0.60, 0.20, {})
check("★★ 没有台账 → value 是 None", r.value, None)
check("★★ 且 source 标明不足", r.source, "insufficient_samples")
check_true("★★ 且明说不给数字的理由", "不给数字" in r.note, r.note[:60])
# N=29（差一条）→ 仍然 None
st29 = {("中", "看多"): C.BucketStat(n=29, hits=20)}
r29 = C.confidence_for("看多", 0.60, 0.20, st29)
check("★★ N=29（差一条）→ 仍然 None", r29.value, None)
# N=30 → 给数，且给的是**下界**
st30 = {("中", "看多"): C.BucketStat(n=30, hits=20)}
r30 = C.confidence_for("看多", 0.60, 0.20, st30)
check_true("★★ N=30 → 给数", r30.value is not None, str(r30.value))
check("★★ 来源标为 ledger_wilson", r30.source, "ledger_wilson")
check("★★ 给的确实是 Wilson 下界（不是点估计 0.667）",
      round(r30.value, 6), round(C.wilson_lower(20, 30), 6))
check_true("★★ 且 ≠ 点估计", abs(r30.value - 20 / 30) > 0.01,
           f"下界 {r30.value:.4f} vs 点估计 {20/30:.4f}")

print()
print("═" * 74)
print("F · ★ 分数桶（不同把握程度的样本不许混着算）")
print("═" * 74)
D = 0.20
check("★ |S|=0.10（<δ） → 桶「观望」", C.bucket_of(0.10, D), "观望")
check("★ |S|=0.30 → 桶「低」", C.bucket_of(0.30, D), "低")
check("★ |S|=0.50 → 桶「中」（边界归上档）", C.bucket_of(0.50, D), "中")
check("★ |S|=0.80 → 桶「高」（边界归上档）", C.bucket_of(0.80, D), "高")
check("★ 负分用绝对值分桶", C.bucket_of(-0.90, D), "高")
check("★ 桶边界与设计一致", C.BUCKET_EDGES, (0.50, 0.80))
# 桶必须真的把样本分开
stats = {("低", "看多"): C.BucketStat(n=40, hits=22),
         ("高", "看多"): C.BucketStat(n=40, hits=34)}
lo_c = C.confidence_for("看多", 0.30, D, stats).value
hi_c = C.confidence_for("看多", 0.90, D, stats).value
check_true("★★ 高桶的置信度必须高于低桶（分桶是有意义的）", hi_c > lo_c,
           f"低桶 {lo_c:.3f} < 高桶 {hi_c:.3f}")

print()
print("═" * 74)
print("G · ★★ 与 claim.py 的 CONF_CAP 叠加（两个来源都要打出来）")
print("═" * 74)
check("★ 1 条依据 → 上限 0.50", C.cap_for(1), 0.50)
check("★ 2 条 → 0.65", C.cap_for(2), 0.65)
check("★ 3 条 → 0.75", C.cap_for(3), 0.75)
check("★ 5 条 → 0.85（封顶）", C.cap_for(5), 0.85)
# 打分器给 0.80，但只有 2 条依据 → 必须被压到 0.65
st = {("高", "看多"): C.BucketStat(n=200, hits=180)}
rc = C.combine(C.confidence_for("看多", 0.90, D, st), n_evidence=2)
check_true("★★ 被依据数上限压过", rc.value <= 0.65 + 1e-12, f"{rc.value:.4f}")
check_true("★★ 且**明说了**是被谁压的", "依据数上限" in rc.capped_by, rc.capped_by[:60])
# 负对照：依据多时不该被压
rc2 = C.combine(C.confidence_for("看多", 0.90, D, st), n_evidence=8)
check_true("★ 负对照：依据 8 条 → 不被压", rc2.value > 0.65, f"{rc2.value:.4f}")
# 样本不足时，仍要说明口径上限
rc3 = C.combine(C.confidence_for("看多", 0.90, D, {}), n_evidence=6)
check_true("★ 样本不足时也要说明上限是多少", "口径上限" in rc3.capped_by, rc3.capped_by[:70])
check_true("★★ 且明说「这是我们定的策略，不是实证结论」",
           "不是实证结论" in rc3.capped_by, "")

print()
print("═" * 74)
print("H · ★ 观望不给置信度（观望不计入方向正确率）")
print("═" * 74)
rz = C.confidence_for("观望", 0.05, D, {})
check("★ 观望 → None", rz.value, None)
check("★ 来源标为 not_applicable", rz.source, "not_applicable")
check_true("★ 且说明了为什么", "观望不计入" in rz.note, rz.note)

print()
print("═" * 74)
print("I · ★ build_stats：从结算记录建表，观望被正确排除")
print("═" * 74)
recs = [
    ("看多", 0.60, D, 0.05, 0.02),   # 对 → (中, 看多)
    ("看多", 0.60, D, -0.05, 0.02),  # 错 → (中, 看多)
    ("看空", 0.90, D, -0.05, 0.02),  # 对 → (高, 看空)
    ("观望", 0.05, D, 0.50, 0.02),   # 不计入
]
st2 = C.build_stats(recs)
check("★ (中,看多) 记 2 条", st2[("中", "看多")].n, 2)
check("★ 其中命中 1 条", st2[("中", "看多")].hits, 1)
check("★ (高,看空) 记 1 条", st2[("高", "看空")].n, 1)
check("★★ 观望没有进任何桶", sum(v.n for v in st2.values()), 3)
check("★ 桶名与方向都对了", sorted(st2), [("中", "看多"), ("高", "看空")])

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
