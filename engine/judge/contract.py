# -*- coding: utf-8 -*-
"""输出契约 —— **该说的话没说出口，等同于没说。**

═══════════════════════════════════════════════════════════════════
它在防什么
═══════════════════════════════════════════════════════════════════
前面几层把"数字对不对"管住了，但**最后一公里**还漏得掉：

    数据是 20 小时前的 → 系统内部知道 → **回答里一个字没提** → 客户以为是实时
    有 2 条依据不可用   → 系统内部标了   → **回答里没写**         → 客户以为是 3 条
    有 1 条触发器没数据 → 系统内部知道 → **回答里没写**         → 客户以为查过了

★ 这三件事的共同点：**内部状态是诚实的，输出是骗人的。**
  最危险的那种失败 —— 它看起来完全成功。

所以本层不判断"内容对不对"，只做一件机械的事：
**凡是内部已经知道该说的话，回答里必须真的出现；没出现就 BLOCKER 拦下。**

判据全是**能被正则/子串机械判定**的，不做语义审查（语义判断不可靠，
而且会把"措辞不同但意思到位"误杀）。

═══════════════════════════════════════════════════════════════════
与 evidence/freshness.py 的分工
═══════════════════════════════════════════════════════════════════
`freshness.validate_output` 只管**时效**这一条，而且是给取数层用的。
本层是**面向最终回答的总闸**，复用它的 `FRESHNESS_WORDS`，不重写一份
（两份时效词表迟早会漂移）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from evidence.freshness import FRESHNESS_WORDS

BLOCKER = "blocker"
WARN = "warn"


@dataclass
class OutputIssue:
    """一条违规。字段形状与 `evidence.freshness.OutputIssue` 一致，便于合并上报。"""
    severity: str
    problem: str
    required_fix: str


@dataclass
class OutputContext:
    """要检查的一份回答 + 它**内部已知**的状态。

    ★ 关键：这里的每个字段都是系统**已经知道**的事。契约只做一件事 ——
      核对它们有没有出现在 `text` 里。
    """
    text: str
    claim_flags: dict = field(default_factory=dict)   # 结论上的标记（依据不足…）
    gaps: list = field(default_factory=list)          # 没取到的数据
    stale_notes: list = field(default_factory=list)   # 过期数据说明
    conflicts: list = field(default_factory=list)     # 跨源分歧
    bear_unknown: int = 0                             # 反方里"没数据判不了"的条数
    bear_hits: int = 0                                # 反方命中的条数
    evidence_ids: list = field(default_factory=list)  # 结论引用的证据号
    direction: str = ""                               # 结论方向（非空表示给了方向）
    invalidators: list = field(default_factory=list)  # 失效条件
    caliber: str = ""                                 # 结论口径
    # ★ 这条回答里到底有几条事实（-1 = 未知）。
    #   独立验证实测："不存在的代码"会产出一份**零事实、零缺口、无结论**的空简报，
    #   而本契约照样打 "✅ 全部通过（该说的话都在）" —— 它自己定义的最坏情况。
    n_facts: int = -1


# ── 各类"已经说过了"的措辞特征（用于机械判定）──
_GAP_WORDS = re.compile(r"(没取到|未取到|缺失|没有取到|无法获取|数据不可用|没能拿到|没找到|缺口)")
_CONFLICT_WORDS = re.compile(r"(冲突|不一致|对不上|分歧|只报.{0,4}不调和)")
_UNKNOWN_WORDS = re.compile(r"(无法判定|判不了|没查到|未判定|不等于|覆盖\s*\d+\s*/\s*\d+)")


def check(ctx: OutputContext) -> list[OutputIssue]:
    """检查一份回答。返回违规清单（空 = 通过）。"""
    t = ctx.text or ""
    out: list[OutputIssue] = []

    # ── C1 · 结论带了标记，回答里必须出现那个标记 ──
    #   （「依据不足」这四个字要真出现在给用户看的文本里，不许只藏在字段里）
    for flag in (ctx.claim_flags or {}):
        if flag and flag not in t:
            out.append(OutputIssue(
                BLOCKER,
                f"结论带标记「{flag}」，但回答正文里**没有出现**这四个字",
                f"必须把「[{flag}]」写进正文 —— 标记只存在字段里＝用户看不到",
            ))

    # ── C2 · 用了过期数据，必须有时效表述（复用 freshness 的词表）──
    if ctx.stale_notes and not FRESHNESS_WORDS.search(t):
        out.append(OutputIssue(
            BLOCKER,
            f"有 {len(ctx.stale_notes)} 项数据是过期的，但回答里**没有任何时效说明**",
            f"必须写明，例如：「{ctx.stale_notes[0]}」",
        ))

    # ── C3 · 有缺口，必须**逐条**说缺了什么 ──
    # ★★ 判据不能只是"出现『没取到』这三个字"——渲染模板自带的标题
    #    「【没取到的】」就含这三个字，**照模板打印一遍就自动通过了**。
    #    → 改成：**逐条要求缺口内容本身出现在正文里**（前 12 字）。
    #    关键词只能证明"写了那句话"，结构化比对才能证明"说的内容真的在"。
    if ctx.gaps:
        miss_g = [g for g in ctx.gaps
                  if str(g).strip() and str(g).strip()[:12] not in t]
        if miss_g or not _GAP_WORDS.search(t):
            out.append(OutputIssue(
                BLOCKER,
                f"有 {len(ctx.gaps)} 项数据没取到，但回答里**没有逐条列出缺了什么**"
                + (f"（{len(miss_g)} 条内容没出现在正文里）" if miss_g else ""),
                "必须把缺口内容本身写进正文；只打印一个标题不算 —— "
                "标题里含『没取到』三个字，会被模板句满足",
            ))

    # ── C4 · 有跨源分歧，必须写出来（不许调和掉）──
    if ctx.conflicts:
        miss_c = [c for c in ctx.conflicts
                  if str(c).strip() and str(c).strip()[:12] not in t]
        if miss_c or not _CONFLICT_WORDS.search(t):
            out.append(OutputIssue(
                BLOCKER,
                f"存在 {len(ctx.conflicts)} 处跨源分歧，但回答里**没提**",
                "按纪律**只报分歧，不调和**；必须把分歧内容本身写出来，不只是关键词",
            ))

    # ── C5 · 反方有"没数据判不了"的，必须说，且不许说成"没有风险" ──
    if ctx.bear_unknown and not _UNKNOWN_WORDS.search(t):
        out.append(OutputIssue(
            BLOCKER,
            f"反方扫描里有 {ctx.bear_unknown} 条因缺数据无法判定，但回答里**没提**",
            "必须写明「N 条无法判定」并列出缺什么 —— "
            "把 UNKNOWN 说成 CLEAR 就是把「没查到」谎报成「没有风险」",
        ))

    # ── C6 · 给了方向，就必须有依据号 ──
    if ctx.direction and not ctx.evidence_ids:
        out.append(OutputIssue(
            BLOCKER,
            "给出了方向，但**一条证据号都没有**",
            "结论必须引用可核验的证据号（本项目的铁律：没有依据的结论构造不出来）",
        ))

    # ── C7 · 给了方向，就必须有失效条件 ──
    if ctx.direction and not ctx.invalidators:
        out.append(OutputIssue(
            BLOCKER,
            "给出了方向，但**没写失效条件**",
            "必须写明「什么情况下这个判断就不成立了」",
        ))

    # ── C8 · 结论必须写口径（WARN：口径缺失影响可读性但未必致命）──
    if ctx.direction and not (ctx.caliber or "").strip():
        out.append(OutputIssue(
            WARN,
            "结论没写口径",
            "写明数据口径（合并/归属、复权方式、统计窗口）",
        ))

    # ── C0 · ★★ 空回答不许通过（独立验证点名）──
    #   "一个事实都没有、也没说缺什么" = 压根没答，
    #   绝不能因为"没有违规"就盖一个"全部通过"的章。
    if ctx.n_facts == 0 and not ctx.direction and not ctx.gaps:
        out.append(OutputIssue(
            BLOCKER,
            "这条回答**一个事实都没有**，也没说缺什么 —— 它等于没答",
            "要么给出事实，要么明确拒答并说清为什么；不许输出一个看着像答了的空壳",
        ))

    # ── C9 · 正文里至少要能看到引用（证据号可回查）──
    if ctx.evidence_ids and not any(e in t for e in ctx.evidence_ids):
        out.append(OutputIssue(
            WARN,
            "结论引用了证据号，但正文里一个都看不到",
            "把证据号写进正文，用户才能自己回查",
        ))

    return out


def blockers(issues: list[OutputIssue]) -> list[OutputIssue]:
    return [i for i in issues if i.severity == BLOCKER]


def report(issues: list[OutputIssue]) -> str:
    if not issues:
        return "✅ 输出契约：全部通过（该说的话都在）"
    bl = blockers(issues)
    L = [f"{'🚫' if bl else '⚠️'} 输出契约：{len(bl)} 条 BLOCKER / "
         f"{len(issues) - len(bl)} 条 WARN —— **这份回答不许直接发给用户**"]
    for i in issues:
        L.append(f"  [{'BLOCKER' if i.severity == BLOCKER else 'WARN'}] {i.problem}")
        L.append(f"      → {i.required_fix}")
    return "\n".join(L)
