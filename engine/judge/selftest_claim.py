# -*- coding: utf-8 -*-
"""结论构造器验收 —— **重点在"拦没拦住"，所以反例比正例多。**

跑：python -m judge.selftest_claim   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
这一层怎么验（用户只认这个写法：正例 + 反例/负对照）
═══════════════════════════════════════════════════════════════════
「没有依据的结论构造不出来」——**光测"有依据时能构造出来"是没有意义的**：
一个永远抛错的构造函数也能过正例。

所以每组都配一个**真·反例**：
    零依据        → 必须抛错          ｜ 一条可用依据 → 必须构造得出来
    伪造引用号    → 必须作废           ｜ 正常引用号   → 必须通过
    重复引用同一条 → 只算 1 条依据      ｜ 两条不同证据 → 算 2 条
    未登记的来源  → 不许进结论         ｜ 已登记的     → 可以进
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from evidence.record import Evidence                     # noqa: E402
from judge import claim as C                             # noqa: E402
from judge.ledger import BEAR, BULL, HOLD                # noqa: E402

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


def has(claim: C.Claim, flag: str) -> bool:
    return flag in claim.flags


def mk_ev(metric="归属于母公司股东的净利润", value="44516880421.86", unit="元",
          company="贵州茅台", period="2026H1", source="财务三表（新浪整理）",
          caliber="net_profit_parent", **kw) -> Evidence:
    return Evidence(company=company, period=period, metric=metric, value=value,
                    unit=unit, source=source, caliber=caliber, **kw)


def cite(metric="归属于母公司股东的净利润", value="44516880421.86",
         kind="financial_sina", verify="", note="", **kw) -> C.Citation:
    return C.Citation(evidence=mk_ev(metric=metric, value=value, **kw),
                      kind=kind, verify=verify, note=note)


def good_pair() -> list[C.Citation]:
    """两条都可用、互不冲突、口径一致的引用。"""
    return [
        cite(metric="归属于母公司股东的净利润", value="44516880421.86"),
        cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total"),
    ]


def build(citations, **kw) -> C.Claim:
    base = dict(code="600519", as_of="2026-10-13", direction=BULL,
                text="可以关注", caliber="合并报表｜不复权")
    base.update(kw)
    return C.build(citations=citations, **base)


print("═" * 74)
print("A · ★★★ 没有依据 → 构造不出来（抛错，不是提醒）")
print("═" * 74)
for label, cites in (("零条依据（空列表）", []), ("None 当依据", None)):
    try:
        build(cites)
        check_true(f"★ {label} → 抛 NoEvidenceError", False, "居然构造成功了")
    except C.NoEvidenceError as e:
        check_true(f"★ {label} → 抛 NoEvidenceError", True, str(e)[:44] + "…")

# 反例里的反例：**依据全废** 也等于没有依据
try:
    build([cite(verify="FAIL", note="片段在原文里找不到"),
           cite(metric="营业总收入", value="1", company="", period="")])
    check_true("★ 每一条依据都不可用 → 同样抛错（结论作废）", False, "居然构造成功了")
except C.NoEvidenceError as e:
    check_true("★ 每一条依据都不可用 → 同样抛错（结论作废）", True, str(e)[:44] + "…")
    check_true("  且说清了「结论作废」", "作废" in str(e), "")

# 负对照：有一条能用的，就必须构造得出来
try:
    c0 = build(good_pair())
    check_true("★ 负对照：有 2 条可用依据 → 构造成功", c0.sufficient,
               f"依据 {len(c0.citations)} 条，标记 {list(c0.flags)}")
except C.NoEvidenceError as e:
    check_true("★ 负对照：有 2 条可用依据 → 构造成功", False, str(e)[:60])

print()
print("═" * 74)
print("B · ★★ 依据不全 → 标 [依据不足]，且不可用的引用**不许被丢掉**")
print("═" * 74)
c = build([cite(metric="归属于母公司股东的净利润"),
           cite(metric="营业总收入", value="91435510499.62", period="",   # ← 缺报告期
                caliber="operating_revenue_total")])
check_true("★ 标了 [依据不足]", has(c, C.F_INSUFFICIENT), str(c.flags.get(C.F_INSUFFICIENT)))
check("  有效依据只有 1 条（缺字段那条**不算数**）", len(c.citations), 1)
check("  ★ 不可用的引用**没被丢掉**，进 unusable", len(c.unusable), 1)
check_true("  且写明了原因（缺报告期）",
           "period" in " ".join(c.unusable[0][1]), str(c.unusable[0][1]))

# 负对照：全都齐 → 不许标
c_ok = build(good_pair())
check_true("★ 负对照：依据齐全 → 不标 [依据不足]", not has(c_ok, C.F_INSUFFICIENT),
           str(list(c_ok.flags)))

print()
print("═" * 74)
print("C · 来源分档：不能进结论的 kind，引用它等于没引用")
print("═" * 74)
# fundflow：东财自家口径，全市场没有第二个源可比 → trust.py 登记为不可进结论
c_f = build([cite(metric="主力净流入", value="1000", kind="fundflow"),
             cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total")])
check_true("★ 资金流（无第二来源）→ 依据不足", has(c_f, C.F_INSUFFICIENT), "")
check_true("  且说明「不能进结论」",
           "不能进结论" in " ".join(c_f.unusable[0][1]), str(c_f.unusable[0][1]))

# 负对照：quote 是登记过、可进结论的
c_q = build([cite(metric="收盘价", value="1288.0", kind="quote", caliber="不复权"),
             cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total")])
check_true("★ 负对照：行情快照 → **不**标依据不足", not has(c_q, C.F_INSUFFICIENT), "")

# 保守默认：没标 kind / 没登记过的 kind
c_k = build([cite(metric="收盘价", value="1288.0", kind=""),
             cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total")])
check_true("★ 没标 kind → 保守默认不能进结论", has(c_k, C.F_INSUFFICIENT), "")
c_u = build([cite(metric="收盘价", value="1288.0", kind="某个还没登记的kind"),
             cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total")])
check_true("★ 没登记过的 kind → 同样挡在结论外", has(c_u, C.F_INSUFFICIENT), "")

# span 核验 FAIL → 这条引用作废（"片段是编的"）
c_s = build([cite(metric="收盘价", value="1288.0", kind="quote", verify="FAIL",
                  note="片段真实，但数值对不上"),
             cite(metric="营业总收入", value="91435510499.62", caliber="operating_revenue_total")])
check_true("★ span 核验 FAIL → 该引用作废", len(c_s.citations) == 1 and len(c_s.unusable) == 1,
           f"可用 {len(c_s.citations)} / 不可用 {len(c_s.unusable)}")

print()
print("═" * 74)
print("D · ★★ 引用号核验 + 去重：同一条证据不能当两条用")
print("═" * 74)
# 伪造 eid：内容寻址的东西，改过就对不上
forged = Evidence(company="贵州茅台", period="2026H1", metric="归属于母公司股东的净利润",
                  value="44516880421.86", unit="元", source="财务三表（新浪整理）",
                  caliber="net_profit_parent", eid="deadbeef1234")
c_forged = build([C.Citation(evidence=forged, kind="financial_sina"),
                  cite(metric="营业总收入", value="91435510499.62",
                       caliber="operating_revenue_total")])
check_true("★ 引用号对不上 → 该引用作废", len(c_forged.unusable) == 1,
           str(c_forged.unusable[0][1])[:60])

# 负对照：正常构造的 eid 必须通过
check_true("★ 负对照：正常证据的引用号能重算出来",
           C._eid_matches(mk_ev()), mk_ev().eid)          # noqa: SLF001

# ★ 同一条证据引两次：不能变成"2 条依据"
same = cite(metric="归属于母公司股东的净利润")
c_dup = build([same, same])
check("★★ 重复引用同一条 → 只算 1 条有效依据", len(c_dup.citations), 1)
check_true("★★ 且被判为「同一条证据，不能算两条」", len(c_dup.unusable) == 1,
           str(c_dup.unusable[0][1])[:70])
check_true("  并因此标了 [单一依据]", has(c_dup, C.F_SINGLE), "")
# 负对照：真正两条不同证据 → 2 条
check("★ 负对照：两条不同证据 → 2 条依据", len(build(good_pair()).citations), 2)

print()
print("═" * 74)
print("E · ★★ 证据冲突：只报冲突，不许调和")
print("═" * 74)
c_cf = build([cite(metric="归属于母公司股东的净利润", value="44516880421.86",
                   source="财务三表（新浪整理）"),
              cite(metric="归属于母公司股东的净利润", value="46033330566.78",
                   source="公告 PDF P83")])
check_true("★ 同一(主体,期间,指标)两个值 → 标 [证据冲突]", has(c_cf, C.F_CONFLICT),
           str(c_cf.flags.get(C.F_CONFLICT))[:80])
msg = c_cf.flags.get(C.F_CONFLICT, "")
check_true("★★ 两个值**都保留**（既没被平均，也没挑一个）",
           "44516880421.86" in msg and "46033330566.78" in msg, msg[:90])
check_true("  且写明「只报冲突，不调和」", "不调和" in msg, "")
check("  两条都还在依据列表里（不许因为冲突就删一条）", len(c_cf.citations), 2)
# 负对照：两条不同指标 → 不是冲突
check_true("★ 负对照：不同指标 → 不标冲突", not has(build(good_pair()), C.F_CONFLICT), "")

print()
print("═" * 74)
print("F · 口径：没口径的数字不许进结论，混口径的不许比较")
print("═" * 74)
c_mix = build([cite(metric="归属于母公司股东的净利润", value="44516880421.86",
                    caliber="net_profit_parent"),
               cite(metric="归属于母公司股东的净利润", value="44516880421.86",
                    caliber="net_profit_parent_deducted", source="公告 PDF P83")])
check_true("★ 同指标两个口径 → 标 [口径不一致]", has(c_mix, C.F_CALIBER_MISMATCH),
           str(c_mix.flags.get(C.F_CALIBER_MISMATCH))[:70])
check_true("  且说明「不许直接比较」",
           "不许直接比较" in c_mix.flags.get(C.F_CALIBER_MISMATCH, ""), "")
c_nocal = build(good_pair(), caliber="")
check_true("★ 结论没写口径 → 标 [口径缺失]", has(c_nocal, C.F_CALIBER_MISSING), "")
check_true("★ 负对照：写了口径 → 不标", not has(c_ok, C.F_CALIBER_MISSING), "")

print()
print("═" * 74)
print("G · 单一依据 = 撑不起结论")
print("═" * 74)
c_one = build([cite(metric="归属于母公司股东的净利润")])
check_true("★ 只有 1 条依据 → 标 [单一依据]", has(c_one, C.F_SINGLE),
           str(c_one.flags.get(C.F_SINGLE)))
check_true("★ 负对照：2 条依据 → 不标", not has(build(good_pair()), C.F_SINGLE), "")

print()
print("═" * 74)
print("H · 置信度必须跟着依据条数走")
print("═" * 74)
c_hi = build([cite(metric="归属于母公司股东的净利润")], confidence=0.95)
check("★ 1 条依据 + 自报 95% → 压到上限 50%", c_hi.confidence, 0.50)
check_true("  且标了 [置信度超限]", has(c_hi, C.F_CONF_CAP), str(c_hi.flags.get(C.F_CONF_CAP))[:70])
check("★ 自报值**没被丢掉**（保留在 requested_confidence 里）", c_hi.requested_confidence, 0.95)
# 负对照：低报不动它
c_lo = build([cite(metric="归属于母公司股东的净利润")], confidence=0.40)
check("★ 负对照：自报 40% 在上限内 → 不动", c_lo.confidence, 0.40)
check_true("  且不标超限", not has(c_lo, C.F_CONF_CAP), "")
# 4 条依据：上限 85%
four = [cite(metric=f"指标{i}", value=f"{100+i}") for i in range(4)]
check("  4 条依据 + 自报 85% → 刚好在上限，不动",
      build(four, confidence=0.85).confidence, 0.85)
c_99 = build(four, confidence=0.99)
check("  4 条依据 + 自报 99% → 压到 85%", c_99.confidence, 0.85)
check_true("  且标了超限", has(c_99, C.F_CONF_CAP), "")

print()
print("═" * 74)
print("I · 方向必须是三个之一（不是的话，台账也没法结算）")
print("═" * 74)
try:
    build(good_pair(), direction="买入")
    check_true("★ 非法方向 → 抛 ClaimInvalid", False, "居然过了")
except C.ClaimInvalid as e:
    check_true("★ 非法方向 → 抛 ClaimInvalid", True, str(e)[:44] + "…")
for d in (BULL, BEAR, HOLD):
    ok = True
    try:
        build(good_pair(), direction=d)
    except Exception:                                        # noqa: BLE001
        ok = False
    check_true(f"  合法方向 {d} 可用", ok, "")

print()
print("═" * 74)
print("J · 渲染：标记必须出现在正文里（不许藏在字段里）")
print("═" * 74)
r = build([cite(metric="归属于母公司股东的净利润"),
           cite(metric="营业总收入", value="91435510499.62", period="",
                caliber="operating_revenue_total")]).render()
check_true("★ 正文里出现 [依据不足]", "[依据不足" in r, r.split("\n")[0])
check_true("★ 正文里带证据号（可回查）", "证据号" in r,
           ([ln for ln in r.split("\n") if "证据号" in ln] or [""])[0][:60])
check_true("★ 正文里带不可用原因", "证据不完整" in r or "缺 period" in r, "")
check_true("★ 正文里带口径", "合并报表" in r, "")
r_ok = build(good_pair()).render()
check_true("★ 负对照：站得住的结论正文里**不该**出现标记", "[依据不足" not in r_ok,
           r_ok.split("\n")[0])
check_true("  且列出了 2 条依据", "依据（2 条）" in r_ok, "")

print()
print("═" * 74)
print("K · 结构：反方/失效条件插槽 + 可序列化")
print("═" * 74)
c_bear = build(good_pair(), bear="估值处于近三年高位，若增速回落则逻辑不成立",
               bear_evidence_ids=["abc123"], invalidators=["下季营收增速转负"])
check_true("★ 反方观点存得住", bool(c_bear.bear), c_bear.bear[:30] + "…")
check_true("★ 反方也带依据（bear.py 要用）", c_bear.bear_evidence_ids == ["abc123"], "")
check_true("★ 失效条件存得住", c_bear.invalidators == ["下季营收增速转负"], "")
check_true("★ sufficient：有标记 → False", c.sufficient is False, "")
check_true("★ sufficient：没标记 → True", build(good_pair()).sufficient is True, "")
try:
    blob = json.dumps(build(good_pair()).to_dict(), ensure_ascii=False)
    check_true("★ to_dict 可 JSON 序列化", len(blob) > 100, f"{len(blob)} 字符")
except Exception as e:                                       # noqa: BLE001
    check_true("★ to_dict 可 JSON 序列化", False, repr(e))
d = build(good_pair()).to_dict()
check_true("★ to_dict 带 evidence_ids（台账要记这个）", len(d["evidence_ids"]) == 2,
           str(d["evidence_ids"]))

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
