# -*- coding: utf-8 -*-
"""输出契约的**效能验收** —— 重点在"违规输出必须被拦下、合规输出不许被冤枉"。

跑：python -m judge.selftest_contract   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
本层的两个数字
═══════════════════════════════════════════════════════════════════
    漏检率  违规输出被放过去了多少   ← 不为 0 = 契约形同虚设
    误杀率  合规输出被拦下了多少     ← 不为 0 = 没法用

★ 只测一个方向没有意义：**永远报 BLOCKER** 的实现在"漏检率"上是满分。
  所以下面每条规则都配一个**正对照（必须拦）**和一个**负对照（必须放行）**。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from judge.contract import (                       # noqa: E402
    BLOCKER, WARN, OutputContext, blockers, report,
)
from judge.contract import check as c_check        # ★ 名字别和本文件的 check 撞

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


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {(a / b * 100 if b else 0):.2f}%"


def bl(ctx: OutputContext) -> int:
    return len(blockers(c_check(ctx)))


# 一份"该说的都说了"的合规回答（负对照的基准）
# ★ 注意：缺口/分歧的**内容本身**必须出现在正文里（新判据要求）
GOOD = """【必须说明】
  ⚠️ 这是 20.0 小时前的数据，不是现在的
  ⚠️ 有 1 项数据没取到：公告原文没取到
  ⚠️ 跨源分歧：腾讯 1288 vs 通达信 1289 —— 只报分歧不调和
【依据不足·单一依据】 本结论依据不足
【结论】方向：看空｜证据 950c2c2a89ce｜口径：归属净利润
  失效条件：若同比转正，基本面依据失效
【看空触发条件】其中 1 条因为数据没取到而无法判定（缺：st_list）—— 这不等于没有风险
"""

print("═" * 74)
print("A · ★★★ 逐条规则：正对照（必须拦）↔ 负对照（必须放行）")
print("═" * 74)
CASES = [
    ("C1 依据不足的标记没写进正文",
     dict(claim_flags={"依据不足": "2 条引用不可用"}, text="这份回答只说好话"),
     dict(claim_flags={"依据不足": "2 条引用不可用"}, text="【依据不足】这份回答说清了")),
    ("C2 用了过期数据却没说时效",
     dict(stale_notes=["这是 20 小时前的数据"], text="茅台现在 1288 元"),
     dict(stale_notes=["这是 20 小时前的数据"], text="茅台 1288 元（2.5 小时前的数据）")),
    ("C3 有缺口却没说缺什么",
     dict(gaps=["公告原文没取到"], text="茅台上半年净利润 445.17 亿"),
     dict(gaps=["公告原文没取到"], text="有一项没能拿到：公告原文没取到")),
    ("C4 有跨源分歧却没提",
     dict(conflicts=["腾讯 1288 vs 通达信 1289"], text="茅台 1288 元（两个源一致）"),
     dict(conflicts=["腾讯 1288 vs 通达信 1289"], text="腾讯 1288 vs 通达信 1289，不一致，只报分歧不调和")),
    ("C5 反方有 UNKNOWN 却说成了没风险",
     dict(bear_unknown=2, text="已扫描 8 条法定触发器，没有风险"),
     dict(bear_unknown=2, text="其中 2 条无法判定（缺数据），不等于没有风险")),
    ("C6 给了方向却没有证据号",
     dict(direction="看空", invalidators=["x"], caliber="合并", text="建议卖出"),
     dict(direction="看空", evidence_ids=["abc123"], invalidators=["x"],
          caliber="合并", text="证据 abc123")),
    ("C7 给了方向却没写失效条件",
     dict(direction="看空", evidence_ids=["abc"], caliber="合并", text="看空"),
     dict(direction="看空", evidence_ids=["abc"], invalidators=["同比转正"],
          caliber="合并", text="失效条件：同比转正")),
]
# ⚠️ 记一笔：「合规」用例必须**同时满足其他所有规则**，因为 bl() 数的是总 BLOCKER 数。
#    第一版 C6 的合规用例只写了 direction/evidence_ids，被 C7（缺失效条件）**正确地**拦了，
#    而我把它当成了"误杀" —— **是用例不完整，不是代码错**（血泪清单第 9 条）。
missed: list[str] = []
wronged: list[str] = []
for name, bad_kw, good_kw in CASES:
    if bl(OutputContext(**bad_kw)) == 0:
        missed.append(f"{name}：违规输出**没被拦**")
    if bl(OutputContext(**good_kw)) != 0:
        wronged.append(f"{name}：合规输出**被冤枉**了")
print(f"    违规用例 {len(CASES)} 条 → 漏检 {len(missed)} 条")
print(f"    合规用例 {len(CASES)} 条 → 误杀 {len(wronged)} 条")
print(f"    ★ 漏检率 = {pct(len(missed), len(CASES))}")
print(f"    ★ 误杀率 = {pct(len(wronged), len(CASES))}")
check_true("★★ 漏检率 = 0（违规的一条都不许放过去）", not missed, str(missed[:2]))
check_true("★★ 误杀率 = 0（合规的一条都不许被拦）", not wronged, str(wronged[:2]))

print()
print("═" * 74)
print("B · ★★ 关键回归：**内部诚实、输出骗人**的三种情形必须被拦")
print("═" * 74)
# 这三种是设计文档点名的、最危险的失败：系统内部全知道，输出里什么都不说
for name, kw in (
    ("过期数据没标时效（内部知道 age_seconds）", dict(stale_notes=["20 小时前"], text="现价 1288")),
    ("依据不足没标（内部 flags 里有）",           dict(claim_flags={"依据不足": "x"}, text="结论很确定")),
    ("UNKNOWN 被说成 CLEAR（内部有 n_unknown）",  dict(bear_unknown=3, text="已扫描 8 条，没有风险")),
):
    check_true(f"★ 拦住：{name}", bl(OutputContext(**kw)) > 0, "")
# ★ 负对照：真实跑通产出的那份简报，必须整体通过
real = OutputContext(
    text=GOOD,
    claim_flags={"依据不足": "x", "单一依据": "y"},
    stale_notes=["20 小时前"], gaps=["公告原文没取到"],
    conflicts=["分歧"], bear_unknown=1, bear_hits=1,
    evidence_ids=["950c2c2a89ce"], direction="看空",
    invalidators=["同比转正"], caliber="归属净利润",
)
issues = c_check(real)
check_true("★★ 负对照：一份写全了的真实简报 → 0 条 BLOCKER", not blockers(issues),
           report(issues)[:90])

print()
print("═" * 74)
print("C · 严重度分级对不对（BLOCKER 与 WARN 不许混）")
print("═" * 74)
# 口径缺失只是 WARN，不该拦下发出去
only_caliber = c_check(OutputContext(
    direction="看空", evidence_ids=["abc"], invalidators=["x"], text="证据 abc"))
check("★ 只缺口径 → 1 条 WARN", len(only_caliber), 1)
check("★ 且它的严重度是 WARN 不是 BLOCKER", only_caliber[0].severity, WARN)
check("★ 且它不是 BLOCKER（口径缺失不足以拦下整份回答）",
      len(blockers(only_caliber)), 0)
# 正文里看不到证据号 → WARN
see_eid = c_check(OutputContext(direction="看空", evidence_ids=["zzz9"], invalidators=["x"],
                              caliber="合并", text="没有任何证据号"))
check("★ 正文看不到证据号 → WARN", see_eid[0].severity, WARN)

print()
print("═" * 74)
print("D · 空回答 / 边界输入不许崩")
print("═" * 74)
for name, kw in (("空文本", dict(text="")), ("None 文本", dict(text=None)),
                 ("全空上下文", dict(text=""))):
    try:
        n = bl(OutputContext(**kw))
        check_true(f"★ {name} → 不崩（拦 {n} 条）", True, f"{n} 条")
    except Exception as e:                                  # noqa: BLE001
        check_true(f"★ {name} → 不崩", False, f"{type(e).__name__}: {e}")
# 全空上下文 = 什么都没说，也没什么要说的 → 必须放行（不许无中生有报错）
check("★★ 全空上下文 → **0 条问题**（不无中生有）", bl(OutputContext(text="随便一句话")), 0)

print()
print("═" * 74)
print("E · ★ 时效词表复用 evidence/freshness（不许各写一份）")
print("═" * 74)
import judge.contract as CT                                # noqa: E402
from evidence import freshness as F                        # noqa: E402
check_true("★ 契约用的是 freshness 的词表对象本身，不是抄一份",
           CT.FRESHNESS_WORDS is F.FRESHNESS_WORDS, "同一对象")
check_true("★ 词表确实能认出时效表述", bool(F.FRESHNESS_WORDS.search("这是 2.5 小时前的数据")), "")
check_true("★ 且不会把无关文本误判成时效表述",
           not F.FRESHNESS_WORDS.search("茅台是白酒龙头"), "")


print()
print("═" * 74)
print("F · ★★★ 空回答不许通过（独立验证点名的那条）")
print("═" * 74)
# 实测所得：之前 "一个事实都没有、也没说缺什么" 的答案会拿到
# "✅ 输出契约：全部通过（该说的话都在）" —— 它自己定义的最坏情况。
empty = c_check(OutputContext(text="【输出契约自检】", n_facts=0))
check_true("★★★ 零事实 + 无方向 + 无缺口 → **BLOCKER**", len(blockers(empty)) > 0,
           report(empty)[:80])
check_true("  且说清了它等于没答", "等于没答" in report(empty), "")
# 负对照：有事实的正常回答不该被这条误杀
okc = c_check(OutputContext(text=GOOD, claim_flags={"依据不足": "x", "单一依据": "y"},
                            stale_notes=["20 小时前"], gaps=["公告原文没取到"],
                            conflicts=["腾讯 1288 vs 通达信 1289"], bear_unknown=1, evidence_ids=["950c2c2a89ce"],
                            direction="看空", invalidators=["x"], caliber="合并",
                            n_facts=21))
check_true("★ 负对照：有 21 条事实的回答 → 不被这条误杀",
           not any("一个事实都没有" in i.problem for i in okc), "")


print()
print("═" * 74)
print("G · ★★★ 模板句不许满足契约（独立验证点名的漏洞）")
print("═" * 74)
# 渲染模板自带的标题里就含「没取到」三个字 ——
# 只要模板打一遍，关键词判据就会自动满足。
GAPS = ["kline 取数失败", "financial_sina 取数失败"]
t_title = c_check(OutputContext(text="【没取到的】（不填空，如实列出）", gaps=GAPS))
check_true("★★★ 只打标题、没列内容 → **必须拦**", len(blockers(t_title)) > 0,
           report(t_title)[:70])
t_items = c_check(OutputContext(text="【没取到的】kline 取数失败；financial_sina 取数失败", gaps=GAPS))
check_true("★ 负对照：逐条列出来了 → 不拦", not blockers(t_items), report(t_items)[:70])
t_echo = c_check(OutputContext(text="问题：依据不足 没取到 茅台多少钱",
                               claim_flags={"依据不足": "x"}, gaps=["某数据没取到"]))
check_true("★★ 用户问句回显不能满足契约（用户自己输入的词不算）",
           len(blockers(t_echo)) > 0, report(t_echo)[:70])

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
