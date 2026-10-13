# -*- coding: utf-8 -*-
"""输出契约 —— **数据时效必须出现在给客户的回答里。**

═══════════════════════════════════════════════════════════════════
为什么这一层必须有
═══════════════════════════════════════════════════════════════════
我们刚给取数层加了「过期兜底」：所有源都挂时，用旧数据顶上，
并且在 `Result.caliber` 里自动写上"这是 20 小时前的缓存数据"。

**但如果判断层不读这句话，整件事就白做了。**

最坏的情形：
    客户问「茅台现在多少钱」
    系统（所有源被限流）→ 拿出 20 小时前的缓存 → 回答「1279.99 元」
    **客户以为这是现在的价，其实是昨天的。**

这比"取不到"更危险 —— 因为**它看起来是成功的**。

→ 所以这一层的职责：**把时效变成输出的一部分，并且可校验。**

═══════════════════════════════════════════════════════════════════
设计：给数字套一层「时效标签」
═══════════════════════════════════════════════════════════════════
不给判断层"自由发挥"的空间 —— 它拿到的每个数字都自带标签，
**报出去的时候标签必须一起报**。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ══════════════════════════════════════════════════════════════════
# 时效等级（口语化，客户能懂）
# ══════════════════════════════════════════════════════════════════
FRESH = "实时"
RECENT = "近期"
OLD = "陈旧"
VERY_OLD = "很旧"


def grade(age_hours: float, kind: str = "") -> tuple[str, str]:
    """时效分级 → (等级, 给客户看的话)。

    ★ 阈值按**数据的真实更新频率**定，不是一刀切：
       财报一个季度才出一次，"3 天前"算很新；
       实时报价 3 小时前就已经不能叫"现在"了。
    """
    # 低频数据（季度/年度更新）标准放宽
    slow = kind in ("financial", "financial_sina", "financial_income",
                    "financial_balance", "financial_cashflow", "dividend",
                    "holder_num", "top_free_holders", "org_survey",
                    "institution_survey", "restricted_release")
    if slow:
        if age_hours <= 24 * 30:
            return FRESH, "这是最新一期报告的数据"
        if age_hours <= 24 * 120:
            return RECENT, f"这是 {age_hours/24:.0f} 天前那期报告的数据"
        return OLD, f"这是 {age_hours/24/30:.1f} 个月前的数据，可能已过时"
    # 高频数据（行情/资金/舆情）
    if age_hours <= 0.25:
        return FRESH, "这是刚刚的数据"
    if age_hours <= 2:
        return RECENT, f"这是 {age_hours*60:.0f} 分钟前的数据"
    if age_hours <= 24:
        return OLD, f"这是 {age_hours:.1f} 小时前的数据，**不是现在的**"
    return VERY_OLD, f"这是 {age_hours/24:.1f} 天前的数据，**很可能已经变了**"


@dataclass
class Freshness:
    """一个数字的时效标签。"""
    as_of: str = ""              # 数据本身的时点（交易日/报告期）
    age_hours: float = 0.0
    stale: bool = False
    source: str = ""
    kind: str = ""

    @property
    def label(self) -> str:
        lv, _ = grade(self.age_hours, self.kind)
        return lv

    def sentence(self) -> str:
        """★ **必须出现在回答里的那句话**。"""
        if not self.stale:
            return ""
        _, text = grade(self.age_hours, self.kind)
        return text

    def prefix(self) -> str:
        """放在数字前面的短标签。"""
        if not self.stale:
            return ""
        return f"［{self.as_of or '旧'}·{self.age_hours:.1f}h前］"


def from_result(r: Any) -> Freshness:
    """从取数层的 `Result` 提取时效标签。"""
    return Freshness(
        as_of=getattr(r, "as_of", "") or "",
        age_hours=float(getattr(r, "age_seconds", 0.0)) / 3600.0,
        stale=bool(getattr(r, "stale", False)),
        source=getattr(r, "source", "") or "",
        kind=getattr(r, "kind", "") or "",
    )


# ══════════════════════════════════════════════════════════════════
# ★★ 输出校验 —— 这是本层的牙齿
# ══════════════════════════════════════════════════════════════════
# 时效的常见表达（回答里出现任意一个就算写了）
FRESHNESS_WORDS = re.compile(
    r"(小时前|分钟前|天前|周前|月前|截至|数据时点|数据时间|"
    r"实时|刚刚|最新一期|报告期|上个交易日|前一交易日|"
    r"滞后|不是现在|已经变了|可能已过时|旧数据|缓存数据)"
)


@dataclass
class OutputIssue:
    severity: str          # blocker | warn
    problem: str
    required_fix: str


def validate_output(text: str, freshness: list[Freshness],
                    guard_note: str = "") -> list[OutputIssue]:
    """检查回答是否如实交代了数据时效。

    ★ 只查**能机械判定**的两件事，不做语义判断：
        ① 有过期数据 → 回答里必须出现时效表述
        ② 守卫说「必需数据缺失」→ 回答里必须提到缺了什么
    """
    issues: list[OutputIssue] = []
    stale = [f for f in freshness if f.stale]

    if stale and not FRESHNESS_WORDS.search(text or ""):
        worst = max(stale, key=lambda f: f.age_hours)
        issues.append(OutputIssue(
            "blocker",
            f"用了 {len(stale)} 项过期数据（最旧 {worst.age_hours:.1f} 小时），"
            "但回答里**没有任何时效说明**",
            f"必须写明，例如：「{worst.sentence()}」",
        ))

    if guard_note and "必需数据缺失" in guard_note and \
            not re.search(r"(取不到|暂无|缺失|没有取到|无法获取|数据不可用)", text or ""):
        issues.append(OutputIssue(
            "blocker",
            "守卫报告「必需数据缺失」，但回答里**没有说明缺了什么**",
            "开篇必须说清缺失项，不许用旧数据或推测填空",
        ))

    return issues


def must_say(freshness: list[Freshness], guard_note: str = "") -> list[str]:
    """★ **判断层必须照说的一句话清单**（它不能省）。"""
    out: list[str] = []
    for f in freshness:
        if f.stale:
            s = f.sentence()
            if s:
                out.append(s)
    if guard_note and "必需数据缺失" in guard_note:
        out.append("本次有必需数据没取到，结论不完整 —— 具体缺什么会在文中说明")
    return out


def annotate(value: Any, f: Freshness, unit: str = "") -> str:
    """把一个数字 + 时效标签渲染成**客户能看懂**的字符串。

    ★ 过期数据的标签**放在数字前面**，不能放后面 ——
      放后面客户会先看到数字就信了。
    """
    s = f"{value}{unit}"
    if f.stale:
        return f"{f.prefix()}{s}"
    return s
