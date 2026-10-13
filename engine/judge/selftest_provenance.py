# -*- coding: utf-8 -*-
"""数字溯源防线验收 —— **双向：编造的要抓到，合法的要放过**。

跑：python -m judge.selftest_provenance   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
这一层的双向指标定义
═══════════════════════════════════════════════════════════════════════════
    漏检率 = 编造/篡改的数字里，没被抓到的比例   → 必须 0，否则防线形同虚设
    误杀率 = 合法数字里，被冤枉的比例           → 必须 0，否则噪音淹没防线

★ 两个方向都难：
    · 太松 → 10.81 改成 10.82 溜过去（漏检）
    · 太严 → 「35.68 亿」与「35680000000」对不上，「-4.76%」与「4.76%」对不上（误杀）
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from judge import provenance as PV                            # noqa: E402

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


# ── 一份「引擎输出」的样本（模拟 python -m ask 的简报）────────────
SRC = """
【事实】
  · 最新价：10.81 元（口径：实时快照）｜ 数据时点 2026-10-13 · tencent · 证据 27d00686c409
  · 涨跌幅：-4.76 %（口径：实时快照）｜ 数据时点 2026-10-13 · tencent
  · 市盈率TTM：93.13 倍｜ 数据时点 2026-10-13 · tencent
  · 市净率：13.13 倍
  · 换手率：10.08 %
  · 总市值：35.68 亿元
  · 涨停价：12.49 元 ｜ 跌停价：10.22 元
  · 归属于母公司所有者的净利润：5,992,694.22 元｜ 数据时点 2026-06-30 · sina
  · 营业总收入：295,914,290.97 元
  · 市盈率TTM历史分位：93.6 %
  · 距250日高点回撤%：23.01 %
  · MA60：8.9985 元
  · ST状态：否（不在名单中）
【三表勾稽】
  [A1] PASS｜残差 0.0
  [A2] PASS｜残差 -2.9802322387695312e-08
【看空触发条件】命中 1 条（有法规依据 0 条 / 经验或技术阈值 1 条）
"""

print("═" * 74)
print("A · ★★ 误杀率：合法的转述必须全部放过")
print("═" * 74)
# 一份「老实转述」的回答：数字都来自上面，只是换了说法
GOOD = """南华生物（000504）现价 10.81 元，今天跌了 4.76%。
市盈率 93.13 倍，市净率 13.13 倍，换手率 10.08%。
总市值 35.68 亿。
半年归母净利润 599.27 万元（5,992,694.22 元），营业总收入 2.96 亿元。
它的市盈率位于自身历史的 93.6% 分位，距离 250 日高点回撤 23.01%。
三表勾稽两项都通过了（残差为 0）。
它的 ST 状态是「否」。
"""
r = PV.check(GOOD, SRC)
print("    " + PV.render(r))
check_true("★★ 老实转述的误杀率 = 0", not r["missing"],
           str([str(m) for m in r["missing"][:3]]))
check_true("★ 而且确实抓到了数字（不是空转）", r["total"] >= 10, f"抓到 {r['total']} 个")

print()
print("═" * 74)
print("B · ★★★ 漏检率：编造/篡改的数字必须抓到")
print("═" * 74)
FABRICATED = [
    ("凭记忆补一个不存在的数", "它去年的净利润是 1.2 亿元。", "1.2"),
    ("把价格改了 1 分钱", "现价 10.82 元。", "10.82"),
    ("把 PE 改了一位", "市盈率 93.14 倍。", "93.14"),
    ("心算相加（源里没有这个和）", "市值加现金一共 47.31 亿。", "47.31"),
    ("编一个本益比", "行业平均市盈率是 28.5 倍。", "28.5"),
    ("编一个目标价", "合理价位在 15.00 元附近。", "15.00"),
    ("编一个涨跌幅", "今天涨了 3.21%。", "3.21"),
]
missed = []
for name, ans, needle in FABRICATED:
    r2 = PV.check(ans, SRC)
    hit = len(r2["missing"]) > 0
    if not hit:
        missed.append(f"{name}（{needle} 没抓到）")
    check_true(f"★★ {name}", hit,
               PV.render(r2).split("\n")[0] if hit else "**漏了**")
print(f"    编造 {len(FABRICATED)} 条 → 漏检 {len(missed)} 条")
print(f"    ★★★ 漏检率 = {len(missed)}/{len(FABRICATED)} = {len(missed)/len(FABRICATED):.2%}")
check_true("★★★ 漏检率 = 0", not missed, str(missed))

print()
print("═" * 74)
print("C · ★★ 标识符 / 日期 / 页码 / 条款号 **不许**被当成事实数字")
print("═" * 74)
IDENT = """代码 000504，公告日期 2026-08-24，报告期 2025-12-31。
证据号 27d00686c409。年报第 115 页，第十条，1.3 条。
这是第 3 项，共 5 部分。"""
r3 = PV.check(IDENT, SRC)
check_true("★★ 一整段标识符 → 0 个未溯源（全被正确跳过）",
           len(r3["missing"]) == 0 and r3["total"] == 0,
           f"total={r3['total']} missing={len(r3['missing'])}")
# 逐个确认分类正确
n = PV.scan("代码 000504 日期 2026-08-24 证据 27d00686c409")
check("★ 全是标识符时扫描结果为空", n, [])

print()
print("═" * 74)
print("D · ★ 单位与符号的归一（这是误杀的最大来源）")
print("═" * 74)
CASES = [
    ("市值：源写「35.68 亿」，回答写「3,568,000,000 元」", "它是 3568000000 元的公司。", True),
    ("市值：源写「35.68 亿」，回答写「35.68 亿」", "总市值 35.68 亿。", True),
    ("涨跌幅：源写「-4.76%」，回答写「跌了 4.76%」", "今天跌了 4.76%。", True),
    ("净利润：源写「5,992,694.22」，回答写「599.27 万」", "净利 599.27 万元。", True),
    # ★★ 精度方向是不对称的 —— 这是本层一条重要纪律：
    #   **可以少写精度，不能多写精度。**
    #   源写「35.68」，回答写「35.7」→ 少写一位，合法 → 放过
    #   源写「35.68」，回答写「35.681」→ **凭空多出一位**，那一位是你自己造的 → 必须抓
    ("少写精度：源 35.68 → 回答 35.7（合法）", "市值 35.7 亿。", True),
    ("★★ 多写精度：源 35.68 → 回答 35.681（凭空多一位）", "市值 35.681 亿。", False),
    ("★★ 但改了 1 分钱必须抓到：源 10.81 → 回答 10.82", "现价 10.82 元。", False),
]
for name, ans, should_pass in CASES:
    rr = PV.check(ans, SRC)
    ok = (not rr["missing"]) == should_pass
    check_true(f"★ {name}", ok,
               PV.render(rr).split("\n")[0])

print()
print("═" * 74)
print("D2 · ★★★ 源是**真实 JSON**（单位在另一个字段里）—— 原来整套测试都用渲染后的文本，"
      "所以这一类误杀一直没抓到")
print("═" * 74)
# ★★★ 血例（2026-10-13 收尾验收时真踩到）：
#   引擎输出的 JSON 是 {"value": "3,020.59", "unit": "亿元"} ——
#   数字和单位**分在两个字段**，平坦扫描看不见单位 → 池子里只有 3020.59；
#   而诚实回答「总市值 3020.59 亿元」会被 ×1e8 → **两边都不认对方 → 回答被拦下**。
#   上面 A~D 段的 SRC 都是**渲染后的文本**（单位紧跟在数字后面），
#   所以这一整类问题在旧测试里永远不会出现 —— 这就是它漏掉的原因。
SRC_JSON = """{
  "question": "个股分析 | 600276",
  "facts": [
    {"label": "最新价",   "value": "45.51",    "unit": "元",   "as_of": "2026-10-13"},
    {"label": "涨跌幅",   "value": "-1.17",    "unit": "%"},
    {"label": "市盈率TTM", "value": "39.09",    "unit": "倍"},
    {"label": "总市值",   "value": "3,020.59", "unit": "亿元"},
    {"label": "成交额",   "value": "18.02",    "unit": "亿元"},
    {"label": "流通股本", "value": "6.37",     "unit": "亿股"}
  ]
}"""
JSON_CASES = [
    ("★ 总市值 3020.59 亿元（**就是这条把诚实回答拦下的**）", "总市值 3020.59 亿元。", True),
    ("★ 最新价 45.51 元", "最新价 45.51 元。", True),
    ("★ 市盈率 39.09 倍", "市盈率TTM 39.09 倍。", True),
    ("★ 跌了 1.17%（源里 value=-1.17 / unit=%）", "今天跌了 1.17%。", True),
    ("★ 成交额 18.02 亿元", "成交额 18.02 亿元。", True),
    ("★★ 负对照：3021.59 亿元（改了一位）", "总市值 3021.59 亿元。", False),
    ("★★ 负对照：9999 亿元（编的）", "总市值 9999 亿元。", False),
]
for name, ans, should_pass in JSON_CASES:
    rr = PV.check(ans, SRC_JSON)
    check_true(name, (not rr["missing"]) == should_pass, PV.render(rr).split("\n")[0])
check_true("★★ 源不是 JSON 时退化成普通扫描（不许崩）",
           isinstance(PV.check("现价 10.81 元。", SRC), dict), "")
check_true("★ 声明了单位的数进池（亿 → ×1e8）",
           any(n.value > 1e10 for n in PV.scan_source(SRC_JSON)),
           f"池 {len(PV.scan_source(SRC_JSON))} 个")

print()
print("═" * 74)
print("E · ★ 边界输入不许崩")
print("═" * 74)
check("空回答 → 不崩", PV.check("", SRC)["total"], 0)
check_true("空源 → 不崩", isinstance(PV.check("10.81 元", ""), dict), "")
check_true("空源 → 所有数字都报未溯源", len(PV.check("10.81 元", "")["missing"]) == 1, "")
check("全空 → 不崩", PV.render(PV.check("", ""))[:4], "数字溯源")
check_true("render 在全部通过时给 ✅", "✅" in PV.render(PV.check(GOOD, SRC)), "")
check_true("render 在失败时给 🚫", "🚫" in PV.render(PV.check(FABRICATED[0][1], SRC)), "")
check_true("未溯源项里带上下文（便于人定位）",
           all(m.context for m in PV.check(FABRICATED[0][1], SRC)["missing"]), "")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
