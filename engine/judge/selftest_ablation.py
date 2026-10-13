# -*- coding: utf-8 -*-
"""证据消融的验收 —— **重点在"它会不会自己骗自己"**。

跑：python -m judge.selftest_ablation   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
这一层最容易骗自己的地方（调研点名的三个）
═══════════════════════════════════════════════════════════════════
**① 空转**：如果「方向」不是「依据」的函数，那删掉任何一条方向都不变，
   翻转次数恒为 0 —— 于是每条结论都会显示成 STABLE，
   **看起来"很稳"，实际什么都没测。**
   → 必须有一条测试：**用一个恒返回同一方向的假规则，证明 flip_count 恒为 0**
     （即证明这个检测能识破空转），再用真规则证明它不是恒 0。

**② 把"没法评估"伪装成"它很稳"**：规则没注册时如果回退成 STABLE，
   就是拿"我不知道"冒充"它没问题"。
   → 必须 NOT_ESTIMABLE，且**报告里要写明「不等于它很稳」**。

**③ 拿小样本的"0 翻转"当"很稳"**：
   n=5 零翻转时，Clopper–Pearson 95% 上界**仍是 0.451** ——
   只报 `0/5` 是在误导。
   → 报率时必须同时报上界，且样本太少时**干脆不给率**。

★ 双向指标依然要给：漏检率（该判 FRAGILE 的漏了）与误杀率（不该判的被冤了）。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from evidence.record import Evidence                        # noqa: E402
from judge import ablation as AB                             # noqa: E402
from judge import claim as C                                 # noqa: E402
from judge.ledger import BEAR, BULL, HOLD                    # noqa: E402

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


def mk(metric: str, value: str, kind: str = "quote") -> C.Citation:
    ev = Evidence(company="测试", period="2026H1", metric=metric, value=value,
                  unit="元", source="测试源", caliber="测试口径")
    return C.Citation(evidence=ev, kind=kind)


def build_claim(cites, direction, conf=0.6):
    return C.build(code="TEST", as_of="2026-10-13", direction=direction,
                   text="测试结论", citations=cites, caliber="测试口径",
                   confidence=conf, invalidators=["测试失效条件"])


# ── 一个真的会读依据的规则：三票制，票多者胜 ──
def rule_majority(cites) -> str:
    if not cites:
        raise C.NoEvidenceError("没有依据")
    up = sum(1 for c in cites
             if (c.evidence.metric.endswith("_看多")))
    down = sum(1 for c in cites
               if (c.evidence.metric.endswith("_看空")))
    return BULL if up > down else (BEAR if down > up else HOLD)


# ── 一个**假规则**：恒返回同一方向（用来证明空转检测有效）──
def rule_constant(cites) -> str:
    if not cites:
        raise C.NoEvidenceError("没有依据")
    return BULL


print("═" * 74)
print("A · ★★★ 空转检测：恒定规则必须被识破（这条是调研点名的第一坑）")
print("═" * 74)
c3 = [mk("A_看多", "1"), mk("B_看多", "2"), mk("C_看空", "3")]
claim_const = build_claim(c3, BULL)
rep_c = AB.analyze(claim_const, rule=rule_constant)
check("★★ 恒返回同一方向的假规则 → flip_count 恒为 0（证明这检测能识破空转）",
      rep_c.flip_count, 0)
check("  且它会被判成 STABLE —— **正是空转会造成的那种假象**", rep_c.state, AB.STABLE)
# 负对照：真规则在同一条结论上必须能测出翻转
rep_r = AB.analyze(claim_const, rule=rule_majority)
check_true("★★ 负对照：真规则测出翻转（不是恒 0）", rep_r.flip_count > 0,
           f"flip_count={rep_r.flip_count}")

print()
print("═" * 74)
print("B · ★★ 一致性断言：规则算的方向 ≠ 结论声称的方向 → 必须抛，不许静默选一个")
print("═" * 74)
bad = build_claim(c3, BEAR)          # 声称看空，但 rule_majority 算出看多
try:
    AB.analyze(bad, rule=rule_majority)
    check_true("★ 不一致 → 抛 RuleMismatch", False, "竟然静默通过了")
except AB.RuleMismatch as e:
    check_true("★ 不一致 → 抛 RuleMismatch", True, str(e)[:56] + "…")
# 负对照：一致时必须正常出结果
ok_rep = AB.analyze(build_claim(c3, BULL), rule=rule_majority)
check_true("★ 负对照：方向一致 → 正常出报告", ok_rep.state in
           (AB.STABLE, AB.SENSITIVE, AB.FRAGILE), ok_rep.state)

print()
print("═" * 74)
print("C · ★★★ NOT_ESTIMABLE：**不许把「没法评估」伪装成「它很稳」**")
print("═" * 74)
no_rule = AB.analyze(build_claim(c3, BULL))          # 不传规则
check("★★ 没规则 → NOT_ESTIMABLE（**绝不是 STABLE**）", no_rule.state, AB.NOT_ESTIMABLE)
check_true("  且写明原因", bool(no_rule.not_estimable_reason), no_rule.not_estimable_reason[:48])
check_true("★★ 且报告里明说「无法评估 ≠ 它很稳」",
           "不等于" in no_rule.render() and "很稳" in no_rule.render(),
           no_rule.render().split("\n")[0][:60])
empty = AB.analyze(build_claim([], BULL, conf=0.5) if False else
                   C.Claim(code="X", as_of="2026-10-13", direction=BULL, text="t"),
                   rule=rule_majority)
check("★ 零依据 → NOT_ESTIMABLE", empty.state, AB.NOT_ESTIMABLE)

print()
print("═" * 74)
print("D · ★★ 小样本纪律：n=5 的「零翻转」证明不了「很稳」")
print("═" * 74)
check("★★ Clopper–Pearson n=5,k=0 的上界 ≈ 0.451（不是 0！）",
      round(AB.cp_upper(0, 5), 3), 0.451)
check("★ n=10,k=0 → 0.259", round(AB.cp_upper(0, 10), 3), 0.259)
check("★ n=20,k=0 → 0.139", round(AB.cp_upper(0, 20), 3), 0.139)
check("★ k=n → 1.0（全翻）", AB.cp_upper(3, 3), 1.0)
check("★ n=0 → None（不给）", AB.cp_upper(0, 0), None)
# 要宣称翻转率 < 0.05，需要多少条零翻转？
need = next(n for n in range(1, 200) if AB.cp_upper(0, n) < 0.05)
check("★★ 要宣称「翻转率<0.05」需要 **59 条**依据全部零翻转（现实中没有）", need, 59)
# 样本太少时**不给率**
few = [mk("A_看多", "1"), mk("B_看空", "2")]
rep_few = AB.analyze(build_claim(few, HOLD), rule=rule_majority)
check("★ 依据 2 条（< 3）→ **不给翻转率**（给了也是误导）", rep_few.flip_rate, None)
check("★ 且不给上界", rep_few.flip_rate_upper_95, None)
check_true("  且报告里说明了为什么不给", "样本太少" in rep_few.render(),
           rep_few.render().split("\n")[-1][:58])
# 样本够 → 给率**必须同时给上界**
enough = [mk(f"X{i}_看多", str(i)) for i in range(5)]
rep_en = AB.analyze(build_claim(enough, BULL), rule=rule_majority)
check_true("★★ 给了率就**必须同时给 95% 上界**",
           rep_en.flip_rate is not None and rep_en.flip_rate_upper_95 is not None,
           f"率={rep_en.flip_rate} 上界={rep_en.flip_rate_upper_95}")

print()
print("═" * 74)
print("E · ★★ 删到无依据 = 最强的一档承重（判翻转，且标 unconstructible）")
print("═" * 74)
one = [mk("唯一_看多", "1")]
rep_one = AB.analyze(build_claim(one, BULL), rule=rule_majority)
check("★ 只有 1 条依据 → 去掉它结论构造不出来 → 判翻转", rep_one.flip_count, 1)
check("★ 且标成 unconstructible", len(rep_one.unconstructible_eids), 1)
check("★ 状态 FRAGILE", rep_one.state, AB.FRAGILE)
check_true("  且报告里说明是「构造不出来」这条最强的理由",
           "构造不出来" in rep_one.render(), rep_one.render()[:70])

print()
print("═" * 74)
print("F · ★ 冗余 vs 承重：去掉不承重的那条，不许误报 FRAGILE")
print("═" * 74)
# 6 看多 vs 0 看空 —— 去掉任一条仍然是看多
redundant = [mk(f"P{i}_看多", str(i)) for i in range(6)]
rep_red = AB.analyze(build_claim(redundant, BULL), rule=rule_majority)
check("★ 依据冗余 → flip_count = 0", rep_red.flip_count, 0)
check_true("★ 且状态**不是** FRAGILE", rep_red.state != AB.FRAGILE, rep_red.state)
check("★ 承重依据列表为空", list(rep_red.load_bearing_eids), [])

print()
print("═" * 74)
print("G · 最小可用子集：EXACT / GREEDY_APPROXIMATION 不许混")
print("═" * 74)
rep_mv = AB.analyze(build_claim(redundant, BULL), rule=rule_majority)
check("★ 依据 6 条 ≤ 12 → 用 EXACT 枚举", rep_mv.min_viable_method, "EXACT")
check_true("★ 且标 exact=True", rep_mv.min_viable_exact is True, "")
check("★★ 最小可用子集 = 1 条（1 看多 vs 0 看空就是看多）",
      len(rep_mv.min_viable_subset or ()), 1)
# 依赖太多时走贪心，且**绝不叫「最小」**
many = [mk(f"Q{i}_看多", str(i)) for i in range(14)]
rep_many = AB.analyze(build_claim(many, BULL), rule=rule_majority)
check("★ 依据 14 条 > 12 → 走贪心近似", rep_many.min_viable_method, "GREEDY_APPROXIMATION")
check_true("★★ 且明确标 exact=False（近似结果**绝不叫「最小」**）",
           rep_many.min_viable_exact is False, "")

print()
print("═" * 74)
print("H · ★ 确定性：同一输入跑两次必须逐字节一致")
print("═" * 74)
a1 = AB.analyze(build_claim(c3, BULL), rule=rule_majority)
a2 = AB.analyze(build_claim(c3, BULL), rule=rule_majority)
check("★ flip_count 一致", a1.flip_count, a2.flip_count)
check("★ state 一致", a1.state, a2.state)
check("★ 承重依据列表一致", list(a1.load_bearing_eids), list(a2.load_bearing_eids))
check("★ 最小子集一致", list(a1.min_viable_subset or ()), list(a2.min_viable_subset or ()))
check("★ policy_hash 一致（可进台账、可跨版本 diff）", a1.policy_hash, a2.policy_hash)

print()
print("═" * 74)
print("I · ★★ 双向指标：漏检率（该判 FRAGILE 的漏了）与 误杀率（不该判的被冤了）")
print("═" * 74)
# 应当判 FRAGILE：删掉任一条方向就变的
SHOULD_FRAGILE = [
    [mk("A_看多", "1")],                                   # 只有 1 条
    [mk("A_看空", "1"), mk("B_看空", "2"), mk("C_看多", "3")],  # 去掉 C 仍看空 → 不翻？
]
# 构造确凿的翻转场景：2 看多 vs 2 看空（平 → HOLD），去掉任一票都变
tie = [mk("A_看多", "1"), mk("B_看多", "2"), mk("C_看空", "3"), mk("D_看空", "4")]
rep_tie = AB.analyze(build_claim(tie, HOLD), rule=rule_majority)
check("★★ 平票(HOLD) 结论：去掉任一票都变方向 → 4 条全部承重", rep_tie.flip_count, 4)
check("★★ 状态 FRAGILE", rep_tie.state, AB.FRAGILE)

miss: list[str] = []
wrong: list[str] = []
CASES = [
    ("只有1条依据 → 必须 FRAGILE", [mk("A_看多", "1")], BULL, AB.FRAGILE),
    ("平票4条 → 必须 FRAGILE", tie, HOLD, AB.FRAGILE),
    ("6条同向冗余 → 不许 FRAGILE", redundant, BULL, AB.STABLE),
]
for name, cites, d, want in CASES:
    got = AB.analyze(build_claim(cites, d), rule=rule_majority).state
    if want == AB.FRAGILE and got != AB.FRAGILE:
        miss.append(f"{name}：实际 {got}")
    if want != AB.FRAGILE and got == AB.FRAGILE:
        wrong.append(f"{name}：实际 {got}")
print(f"    应判 FRAGILE {sum(1 for _,_,_,w in CASES if w==AB.FRAGILE)} 例 → 漏检 {len(miss)} 例")
print(f"    不应判 FRAGILE {sum(1 for _,_,_,w in CASES if w!=AB.FRAGILE)} 例 → 误杀 {len(wrong)} 例")
check_true("★★ 漏检率 = 0", not miss, str(miss))
check_true("★★ 误杀率 = 0", not wrong, str(wrong))

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
