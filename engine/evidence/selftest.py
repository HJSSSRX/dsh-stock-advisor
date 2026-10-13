# -*- coding: utf-8 -*-
"""证据层自测 —— 用真实场景验证双层校验与拒答机制。

跑：python -m evidence.selftest   （在 stockharness 目录下）
"""
from __future__ import annotations

import sys
from decimal import Decimal

sys.path.insert(0, ".")
from evidence import (  # noqa: E402
    ClaimKind, Evidence, RefusalReason, Verdict,
    extract_amounts, locate_span, SpanMatch, verify_computed, verify_span,
)

PASS, FAIL = 0, 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


# ── 一段模拟的 A 股年报原文（含全角数字、千分位、多单位混用）──
ANNUAL_REPORT = """
佛山南华仪器股份有限公司 2026 年半年度报告
第三节 管理层讨论与分析
报告期内，公司实现营业总收入 125,342,891.55 元，较上年同期增长 12.3%；
归属于上市公司股东的净利润 1,098,549.90 元，上年同期为 -1,146,363.00 元，
实现扭亏为盈。基本每股收益 0.0082 元。
本报告期毛利率为 18.6%，较上年同期 17.2% 上升 1.4 个百分点。
公司注册地址：广东省佛山市。注册资本 1.07 亿元。股本 1.07 亿股。
2026 年 8 月 29 日
"""

print("═" * 68)
print("一、证据记录：字段齐备性（★ 所有字段可空是设计，缺字段=拒答不是异常）")
print("═" * 68)

e_ok = Evidence(company="南华仪器", period="2026H1", metric="归母净利润",
                value="1098549.90", unit="元", source="2026半年报 P12",
                caliber="合并", span="归属于上市公司股东的净利润 1,098,549.90 元")
check("六元组齐 → is_complete", e_ok.is_complete, True)
check("缺字段列表为空", e_ok.missing_fields, [])

e_bad = Evidence(company="南华仪器", metric="净利润", value="100")
check("缺 3 个字段 → 不完整", e_bad.is_complete, False)
check("缺字段识别", sorted(e_bad.missing_fields), ["period", "source", "unit"])
r = e_bad.refusal_if_incomplete()
check("不完整 → 给出拒答（不抛异常）", r.reason, RefusalReason.MISSING_FIELD)

e_zero = Evidence(company="X", period="2026Q1", metric="净利润", value="0",
                  unit="元", source="接口")
check("★ 数值 0 不算缺失（VeriFin 的坑）", e_zero.is_complete, True)

check("证据号是内容寻址", e_ok.eid, Evidence(company="南华仪器", period="2026H1",
      metric="归母净利润", value="1098549.90", unit="元", source="2026半年报 P12",
      caliber="合并", span="归属于上市公司股东的净利润 1,098,549.90 元").eid)

print()
print("═" * 68)
print("二、span 第 1 层：片段在不在原文")
print("═" * 68)
check("逐字命中", locate_span("归属于上市公司股东的净利润 1,098,549.90 元", ANNUAL_REPORT)[0],
      SpanMatch.EXACT)
check("归一化命中（原文是全角括号）",
      locate_span("公司注册地址：（广东省佛山市）", ANNUAL_REPORT)[0], SpanMatch.NOT_FOUND)
check("不存在的片段", locate_span("本公司净利润为 999 亿元", ANNUAL_REPORT)[0], SpanMatch.NOT_FOUND)

print()
print("═" * 68)
print("三、span 第 2 层：数值能不能从片段推出来")
print("═" * 68)
v, why = verify_span(e_ok, ANNUAL_REPORT)
check("数值正确 → PASS", v, Verdict.PASS.value)

e_wrong = Evidence(company="南华仪器", period="2026H1", metric="归母净利润",
                   value="1098.55", unit="万元", source="2026半年报 P12",
                   span="归属于上市公司股东的净利润 1,098,549.90 元")
v, why = verify_span(e_wrong, ANNUAL_REPORT)
check("★ 片段真但数值抄错 → FAIL", v, Verdict.FAIL.value)
print(f"      → {why}")

e_fake = Evidence(company="南华仪器", period="2026H1", metric="归母净利润",
                  value="1098549.90", unit="元", source="2026半年报 P12",
                  span="本公司本期净利润为 1,098,549.90 元")
v, why = verify_span(e_fake, ANNUAL_REPORT)
check("★ 片段是编的 → FAIL", v, Verdict.FAIL.value)
print(f"      → {why}")

print()
print("═" * 68)
print("四、★ 我们相对 VeriFin 的改进 ①：修掉「年份被当金额」的缺陷")
print("═" * 68)
amts = extract_amounts("公司注册地址：广东省佛山市。注册资本 1.07 亿元。2026 年 8 月 29 日")
vals = [str(a[0]) for a in amts]
check("只抽到 1.07 亿元这个金额", len(amts), 1)
check("金额换算到元正确", Decimal(vals[0]) == Decimal("107000000"), True)
print(f"      → 抽到：{[(str(a[0]), a[1], a[2]) for a in amts]}")
print("      → 2026 / 8 / 29 被正确排除（VeriFin 会把 2026 当金额）")

v, why = verify_span(
    Evidence(company="南华仪器", period="2026H1", metric="注册资本",
             value="2026", unit="元", source="2026半年报",
             span="公司注册地址：广东省佛山市。注册资本 1.07 亿元。2026 年 8 月 29 日"),
    ANNUAL_REPORT)
check("★ 拿年份冒充金额 → FAIL", v, Verdict.FAIL.value)

print()
print("═" * 68)
print("五、★ 改进 ②：A 股单位量纲（学界空白，差 10⁴ 倍）")
print("═" * 68)
cases = [
    ("1098549.90", "元", True, "原样"),
    ("109.85499", "万元", True, "万元 → 元"),
    ("0.0109854990", "亿元", True, "亿元 → 元"),
    ("1098.5499", "千元", True, "千元 → 元"),
    ("9999", "元", False, "差 3 个数量级"),
]
for val, unit, want_ok, label in cases:
    e = Evidence(company="南华仪器", period="2026H1", metric="归母净利润",
                 value=val, unit=unit, source="2026半年报 P12",
                 span="归属于上市公司股东的净利润 1,098,549.90 元")
    v, _ = verify_span(e, ANNUAL_REPORT)
    ok = (v in (Verdict.PASS.value, Verdict.WARN.value)) == want_ok
    check(f"{label}：{val}{unit}", ok, True)

print()
print("═" * 68)
print("六、计算类声明必须真重算（FinGround：幻觉率 28.4%，容差 ±0.5%）")
print("═" * 68)
v, why = verify_computed("18.6", "18.6")
check("精确相等 → PASS", v, Verdict.PASS.value)
v, why = verify_computed("18.62", "18.6")
check("±0.5% 内 → WARN", v, Verdict.WARN.value)
print(f"      → {why}")
v, why = verify_computed("18.6", "28.6")
check("差异过大 → FAIL", v, Verdict.FAIL.value)
print(f"      → {why}")
v, why = verify_computed("毛利率", "18.6")
check("不是数字 → REFUSE（不硬算）", v, Verdict.REFUSE.value)

print()
print("═" * 68)
print("七、接口类数据（无 PDF 片段）也要有出处")
print("═" * 68)
e_api = Evidence(company="300417", period="2026-10-10", metric="收盘价",
                 value="11.88", unit="元", source="eastmoney.quote",
                 caliber="前复权", kind=ClaimKind.NUMERICAL)
v, why = verify_span(e_api, "")
check("有出处 → PASS", v, Verdict.PASS.value)
e_nosrc = Evidence(company="300417", period="2026-10-10", metric="收盘价",
                   value="11.88", unit="元")
v, why = verify_span(e_nosrc, "")
check("★ 无片段又无出处 → REFUSE", v, Verdict.REFUSE.value)

print()
print("═" * 68)
print("八、拒答文本（调研结论：不要把不确定性变成数字，要说「缺什么」+「你怎么自查」）")
print("═" * 68)
from evidence import Refusal  # noqa: E402
ref = Refusal(
    reason=RefusalReason.SOURCE_SILENT,
    detail="这只股票在 2026 年 3 月没有公告披露记录。",
    missing=("该期间的公告",),
    hint="巨潮资讯网搜「300417」看披露时间轴",
)
txt = ref.to_user()
check("拒答文本含「你可以自己核对」", "你可以自己核对" in txt, True)
print(f"      → {txt}")

print()
print("═" * 68)
print(f"结果：{PASS} 通过 / {FAIL} 失败")
print("═" * 68)
sys.exit(1 if FAIL else 0)
