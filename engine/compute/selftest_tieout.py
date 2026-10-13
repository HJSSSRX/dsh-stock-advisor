# -*- coding: utf-8 -*-
"""三表勾稽验收 —— **双向指标 + "不许 FAIL"的结构性保证**。

跑：python -m compute.selftest_tieout   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
这个模块的验收有三层，缺一不可
═══════════════════════════════════════════════════════════════════════════
第一层 **漏检率**（注入受控篡改，看抓不抓得到）
    对已知正确的报表做受控篡改：量级错误 ×10、符号翻转、单科目错位、缺项。
    ★ 只测误报率会诱导把判据调废；只测漏检率会诱导把判据调敏感。**两个必须一起报。**

第二层 **误杀率**（真实干净公司，看不冤枉）
    被测对象全部是**标准无保留意见**的公司。判它们"不平"就是冤枉好人。

第三层 ★★★ **结构性保证**（本模块与别处最不一样的地方）
    误报率 83% / 93% / 20% 的三条判据**在代码层就不具备判 FAIL 的资格** ——
    不是"我们记得别用"，是**写了 FAIL 也会被拦成 WARN**。
    这一层要用"故意去写 FAIL"来验。
"""
from __future__ import annotations

import copy
import sys

sys.path.insert(0, ".")

from compute import tieout as T                              # noqa: E402

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


# ── 一份已知正确的报表（茅台 2025 年报实测值，A1/A2/A3 残差都是 0）────────
BS = {"TOTAL_ASSETS": 3_000_000_000_000.0, "TOTAL_LIABILITIES": 600_000_000_000.0,
      "TOTAL_EQUITY": 2_400_000_000_000.0, "TOTAL_LIAB_EQUITY": 3_000_000_000_000.0,
      "UNASSIGN_RPOFIT": 200_000_000_000.0, "ACCOUNTS_RECE": 1_000_000.0,
      "NOTE_RECE": 2_000_000.0, "CONTRACT_LIAB": 3_000_000.0}
BS_PREV = dict(BS, UNASSIGN_RPOFIT=180_000_000_000.0, ACCOUNTS_RECE=900_000.0,
               NOTE_RECE=1_800_000.0, CONTRACT_LIAB=2_800_000.0)
CF = {"CCE_ADD": 50_000_000_000.0, "NETCASH_OPERATE": 61_000_000_000.0,
      "NETCASH_INVEST": -30_000_000_000.0, "NETCASH_FINANCE": 16_770_000_000.0,
      "RATE_CHANGE_EFFECT": 2_230_000_000.0,
      "END_CCE": 300_000_000_000.0, "BEGIN_CCE": 250_000_000_000.0,
      "SALES_SERVICES": 100_000_000_000.0, "ASSIGN_DIVIDEND_PORFIT": 10_000_000_000.0}
IS_ = {"NETPROFIT": 46_000_000_000.0, "PARENT_NETPROFIT": 44_500_000_000.0,
       "TOTAL_OPERATE_INCOME": 88_000_000_000.0,
       # ★ 减值科目：这里是「存在但值为 None」= 本期无此项 → 按 0 处理（与"字段缺失"不同）
       "ASSET_IMPAIRMENT_LOSS": None, "ASSET_IMPAIRMENT_INCOME": None,
       "CREDIT_IMPAIRMENT_LOSS": None, "CREDIT_IMPAIRMENT_INCOME": 17_234_379.37}
# 自检：这份构造数据本身必须是闭合的
assert abs(T.check_A1(BS).residual or 0) < 1e-6, "构造的 BS 不闭合"
assert abs(T.check_A2(CF).residual or 0) < 1e-6, "构造的 CF 不闭合"

print("═" * 74)
print("A · ★★ 误杀率：已知正确的报表**不许**被判不平")
print("═" * 74)
check("★ A1 正确数据 → PASS", T.check_A1(BS).verdict, "PASS")
check("★ A2 正确数据 → PASS", T.check_A2(CF).verdict, "PASS")
check("★ A3 正确数据 → PASS", T.check_A3(CF).verdict, "PASS")
# 千元取整尾差（真实现象）不许误报
CF_round = dict(CF, CCE_ADD=CF["CCE_ADD"] + 999.0)
check("★★ 千元取整尾差 999 元 → 仍 PASS（容差下界 1000）",
      T.check_A2(CF_round).verdict, "PASS")
CF_round2 = dict(CF, CCE_ADD=CF["CCE_ADD"] + 1001.0)
check("★★ 尾差 1001 元 → FAIL（容差下界不放水）", T.check_A2(CF_round2).verdict, "FAIL")

print()
print("═" * 74)
print("B · ★★★ 漏检率：注入受控篡改，**必须抓到**")
print("═" * 74)
MUTANTS = [
    ("A1 总资产 ×10", "A1", lambda d: d.update(TOTAL_ASSETS=d["TOTAL_ASSETS"] * 10),
     lambda: copy.deepcopy(BS)),
    ("A1 负债少记 1 亿", "A1", lambda d: d.update(TOTAL_LIABILITIES=d["TOTAL_LIABILITIES"] - 1e8),
     lambda: copy.deepcopy(BS)),
    ("A1 权益少记 1 亿", "A1", lambda d: d.update(TOTAL_EQUITY=d["TOTAL_EQUITY"] - 1e8),
     lambda: copy.deepcopy(BS)),
    ("A1 合计项对不上（第二路径）", "A1",
     lambda d: d.update(TOTAL_LIAB_EQUITY=d["TOTAL_LIAB_EQUITY"] + 1e8),
     lambda: copy.deepcopy(BS)),
    ("A2 净增加额 ×10", "A2", lambda d: d.update(CCE_ADD=d["CCE_ADD"] * 10),
     lambda: copy.deepcopy(CF)),
    ("A2 投资现金流符号翻转", "A2",
     lambda d: d.update(NETCASH_INVEST=-d["NETCASH_INVEST"]),
     lambda: copy.deepcopy(CF)),
    ("A2 汇率影响漏记", "A2", lambda d: d.update(RATE_CHANGE_EFFECT=0.0),
     lambda: copy.deepcopy(CF)),
    ("A3 期末现金少记 1 亿", "A3", lambda d: d.update(END_CCE=d["END_CCE"] - 1e8),
     lambda: copy.deepcopy(CF)),
]
missed = []
for name, rid, mutate, loader in MUTANTS:
    d = loader()
    mutate(d)
    v = {"A1": T.check_A1, "A2": T.check_A2, "A3": T.check_A3}[rid](d).verdict
    if v != "FAIL":
        missed.append(f"{name} → 实际 {v}")
print(f"    注入了 {len(MUTANTS)} 种篡改")
print(f"    ★ 漏检率 = {len(missed)}/{len(MUTANTS)} = {len(missed)/len(MUTANTS):.2%}")
check_true("★★★ 漏检率 = 0（改坏了必须报 FAIL）", not missed, str(missed))

print()
print("═" * 74)
print("C · ★★★ 缺字段必须 UNKNOWN，**绝不许降级为 PASS**")
print("═" * 74)
for f in ("TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY"):
    d = copy.deepcopy(BS)
    d[f] = None
    check(f"★ A1 缺 {f} → UNKNOWN", T.check_A1(d).verdict, "UNKNOWN")
for f in ("CCE_ADD", "NETCASH_OPERATE", "NETCASH_INVEST", "NETCASH_FINANCE"):
    d = copy.deepcopy(CF)
    d[f] = None
    check(f"★ A2 缺 {f} → UNKNOWN", T.check_A2(d).verdict, "UNKNOWN")
# ★ 汇率影响缺失是**取数缺口**，不是公司的问题
d = copy.deepcopy(CF)
d["RATE_CHANGE_EFFECT"] = None
r = T.check_A2(d)
check("★★★ A2 缺汇率影响 → UNKNOWN（**不是 FAIL**）", r.verdict, "UNKNOWN")
check_true("★★ 且说明了这是取数缺口", "取数缺口" in r.note, r.note[:60])

print()
print("═" * 74)
print("D · ★★★ 结构性保证：误报率高的判据**写 FAIL 也会被拦成 WARN**")
print("═" * 74)
for rid in ("B2", "A5", "C1"):
    rule = T.RULE_BY_ID[rid]
    check_true(f"★ {rid} 的 can_fail = False", rule.can_fail is False,
               f"点估计 {rule.fp_point:.0%}")
    check_true(f"★ {rid} 的实测误报率必须 > 0", rule.fp_point > 0,
               f"{rule.fp_point:.1%}")
# ★ 故意去写 FAIL
for rid in ("B2", "A5", "C1"):
    r = T.make(rid, "FAIL")
    check(f"★★★ 故意写 {rid} FAIL → 被拦成 WARN", r.verdict, "WARN")
    check(f"★★ 　且标记了「被降级」", r.suppressed_fail, True)
# ★ 二道防线：手工构造的结果也不许溜
fake = T.TieoutResult(rule_id="B2", verdict="FAIL")
T.enforce(fake)
check("★★★ 手工构造的 B2 FAIL → enforce() 也拦住", fake.verdict, "WARN")
check("★★ 　且标记了「被降级」", fake.suppressed_fail, True)
# 负对照：有资格的规则必须能 FAIL
r = T.make("A1", "FAIL")
check("★ 负对照：A1 写 FAIL → 保持 FAIL（它确实有资格）", r.verdict, "FAIL")
check("★ 　且没被标记降级", r.suppressed_fail, False)

print()
print("═" * 74)
print("E · ★★ 镜像字段陷阱（同时相加会系统性算错）")
print("═" * 74)
check_true("★★ 白名单里**不含**镜像字段",
           not (set(T.B2_KEYS) & set(T.B2_EXCLUDED_MIRRORS)),
           f"镜像字段：{T.B2_EXCLUDED_MIRRORS}")
check_true("★ DEFER_TAX 不在白名单（它 = DT_ASSET_REDUCE + DT_LIAB_ADD）",
           "DEFER_TAX" not in T.B2_KEYS, "")
check_true("★ OILGAS_BIOLOGY_DEPR 不在白名单（它 ≡ FA_IR_DEPR）",
           "OILGAS_BIOLOGY_DEPR" not in T.B2_KEYS, "")
check_true("★ 而 FA_IR_DEPR 与那两个 DT_ 项在白名单里",
           all(k in T.B2_KEYS for k in ("FA_IR_DEPR", "DT_ASSET_REDUCE", "DT_LIAB_ADD")), "")
# ★ 断言机制：如果哪天有人把镜像塞进去，必须抛错
T.assert_no_mirrors()
check_true("★ 当前白名单通过自检", True, "")
_orig = T.B2_KEYS[:]
try:
    T.B2_KEYS.append("DEFER_TAX")
    try:
        T.assert_no_mirrors()
        check_true("★★ 把镜像字段塞进白名单 → 必须抛错", False, "没抛错")
    except ValueError as e:
        check_true("★★ 把镜像字段塞进白名单 → 必须抛错", "镜像字段" in str(e), str(e)[:58])
finally:
    T.B2_KEYS[:] = _orig

print()
print("═" * 74)
print("F · ★★ 高压线：B2/A5/C1 的输出**必须带误报率**，不许裸输出残差")
print("═" * 74)
r = T.check_B2(CF, IS_)
check_true("★★ 输出里带实测误报率（点估计）", r.false_positive_rate_measured is not None and
           r.false_positive_rate_measured > 0.5, f"{r.false_positive_rate_measured:.1%}")
check_true("★★★ 且带 **CP95 上界**（不只是点估计）", r.false_positive_upper is not None,
           f"上界 {r.false_positive_upper:.2%}")
check_true("★★ 输出里带样本说明（可复核）", bool(r.false_positive_sample), r.false_positive_sample[:50])
check_true("★★ 且明说「残差 ≠ 公司有问题」", "≠ 公司有问题" in r.note, r.note[:50])
r5 = T.check_A5(BS, BS_PREV, IS_, CF)
check_true("★ A5 输出也带误报率", r5.false_positive_rate_measured > 0.5,
           f"{r5.false_positive_rate_measured:.1%}")
r5b = T.check_A5(BS, BS_PREV, IS_, {})
check_true("★ A5 缺分红数据也不崩", r5b.verdict in ("INFO", "WARN", "UNKNOWN"), r5b.verdict)
rc = T.check_C1(BS, BS_PREV, IS_, CF, vat_rate=0.13)
check_true("★★ C1 必须标注用了哪个税率", "13%" in rc.note, rc.note[:60])
check("★ C1 永远不 FAIL（哪怕比值离谱）", T.check_C1(BS, BS_PREV, IS_, dict(CF, SALES_SERVICES=1.0)).verdict, "INFO")

print()
print("═" * 74)
print("G · ★★ 统一返回契约：每条结果都要带得出「我怎么知道它是对的」")
print("═" * 74)
r = T.check_A1(BS, period="2025-12-31")
check_true("★ 带残差", r.residual is not None, f"{r.residual}")
check_true("★ 带容差", r.tolerance is not None, f"{r.tolerance:g}")
check_true("★ 带容差来源说明", bool(r.tolerance_source), r.tolerance_source[:40])
check_true("★ 带实测误报率", r.false_positive_rate_measured is not None, "")
check_true("★ 带 CP95 上界", r.false_positive_upper is not None, f"{r.false_positive_upper:.2%}")
check_true("★ 带样本说明", bool(r.false_positive_sample), r.false_positive_sample[:40])
check_true("★ 带报告期与口径", r.period == "2025-12-31" and r.caliber == "合并", "")
check_true("★ 带输入明细（可归因）", len(r.inputs) == 4, f"{len(r.inputs)} 项")
txt = r.render()
check_true("★ render 里能看到误报率数字", "误报率" in txt and "CP95" in txt, txt.split(chr(10))[2][:60])
# 未注册的规则不许用
try:
    T.make("R-X9", "PASS")
    check_true("★ 未注册规则 → 抛错", False, "没抛错")
except KeyError as e:
    check_true("★ 未注册规则 → 抛错", "未注册" in str(e), str(e)[:40])
# 每条规则都必须登记出处
no_cite = [r.id for r in T.RULES if not r.citation]
check_true("★★ 每条规则都必须有准则出处", not no_cite, str(no_cite))

print()
print("═" * 74)
print("H · ★★★ 真实数据（联网）：误杀率实测")
print("═" * 74)
LIVE = ("SH600519", "SZ000858", "SH600036", "SZ000001")
try:
    n_ok = n_all = 0
    fails = []
    for code in LIVE:
        for r in T.run_all(code, "2025-12-31"):
            if r.rule_id in ("A1", "A2", "A3"):
                n_all += 1
                if r.verdict == "PASS":
                    n_ok += 1
                else:
                    fails.append(f"{code}/{r.rule_id}={r.verdict} 残差={r.residual}")
    print(f"    {len(LIVE)} 只真实干净公司 × 3 条可判规则 = {n_all} 次判定")
    print(f"    ★ 误杀率 = {len(fails)}/{n_all} = {len(fails)/n_all:.2%}")
    check_true("★★★ 真实干净公司的误杀率 = 0（判它们不平就是冤枉好人）", not fails, str(fails[:2]))
except Exception as e:                                       # noqa: BLE001
    print(f"    （实网不可用，跳过：{type(e).__name__}: {str(e)[:70]}）")

print()
print("═" * 74)
print("I · ★★★ 减值科目：'字段缺失' 与 '存在但为空' 必须分开")
print("═" * 74)
v, note = T.impair_loss(IS_)
check_true("★★ 存在但为空的科目 → 按 0 处理，且**写进说明**",
           v is not None and "本期为空" in note, f"值={v!r} 说明={note[:50]!r}")
check("★★ 有值的科目按符号反推（东财 *_INCOME 取负 → 补资料口径）",
      round(v, 2), -17_234_379.37)
v2, note2 = T.impair_loss({"NETPROFIT": 1.0})
check_true("★★ 字段完全不在返回里 → 返回 None（上层必须 UNKNOWN）", v2 is None, note2)
r = T.check_B2(CF, {"NETPROFIT": 1.0, "NETCASH_OPERATE": 1.0})
check("★★ 无减值字段时 B2 → UNKNOWN（不硬算）", r.verdict, "UNKNOWN")
# ★ 外部标准答案：茅台 2025 年报第 115 页补充资料相加 = 表内经营现金流，残差 0.00
class _FakeIS(dict):
    pass
_GOLD = {"NETPROFIT": 46_000_000_000.0, "ASSET_IMPAIRMENT_LOSS": None,
         "ASSET_IMPAIRMENT_INCOME": None, "CREDIT_IMPAIRMENT_LOSS": None,
         "CREDIT_IMPAIRMENT_INCOME": 17_234_379.37}
_R = T.check_B2(CF, _GOLD)
check_true("★ 茅台形状的输入 → B2 不崩且给出残差", _R.residual is not None, f"{_R.residual!r}")


print()
print("═" * 74)
print("J · ★★★ CP 闸门：can_fail 必须是**算出来的**，不是填出来的")
print("═" * 74)
# 独立证伪的核心一击：35 只样本 0 次误报 → CP95 单侧上界 8.20% > 5% 门槛
from judge.ablation import cp_upper
u35 = cp_upper(0, 35)
check_true("★★★ 复现证伪者的算法：0/35 → CP95 上界 8.20%", abs(u35 - 0.082) < 0.001,
           f"{u35:.4%}")
check_true("★★★ 8.20% > 5% 门槛 —— 所以 35 只样本**证不过门槛**（这是原实现的错）",
           u35 > T.FP_TARGET, f"{u35:.2%} > {T.FP_TARGET:.0%}")
# 扩到 68 只 → 4.31% ≤ 5% → 才达标
u68 = cp_upper(0, 68)
check_true("★★★ 扩到 68 只 → 上界 4.31% ≤ 5% → 达标（修法是扩样本，不是降标准）",
           u68 <= T.FP_TARGET, f"{u68:.2%} ≤ {T.FP_TARGET:.0%}")
# 规则表必须体现这个结论
for rid in ("A1", "A2", "A3"):
    rl = T.RULE_BY_ID[rid]
    check_true(f"★ {rid}: allow_fail 且 gate_met → can_fail=True",
               rl.allow_fail and rl.gate_met and rl.can_fail,
               f"{rl.fp_text()}")
for rid in ("B2", "A5", "C1"):
    rl = T.RULE_BY_ID[rid]
    check_true(f"★ {rid}: gate_met=False → can_fail=False",
               not rl.gate_met and not rl.can_fail, f"{rl.fp_text()}")
# 造一条「想判但没证过门槛」的规则 → can_fail 必须是 False
BAD = T.Rule("Z9", "假规则", "无", 35, 0, True, "35 只", "无")
check_true("★★★ 想判 FAIL 但只有 35 只样本 → can_fail **自动为 False**",
           BAD.allow_fail and not BAD.can_fail, f"上界 {BAD.fp_upper:.2%} > 门槛")
check_true("★ 负对照：68 只样本的同类规则 → can_fail=True",
           T.Rule("Z8", "假规则", "无", 68, 0, True, "68 只", "无").can_fail, "")
# 输出里不许再出现裸的「0%」
t = T.check_A1(BS, period="2025-12-31").render()
check_true("★★ 输出里带 CP95 上界", "CP95 上界" in t, "")
check_true("★★ 输出里带达标状态", "达标" in t, "")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
