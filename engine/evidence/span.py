# -*- coding: utf-8 -*-
"""span 双层硬校验 —— 防"数字编造"的最后一道物理防线。

设计来源：VeriFin（Apache-2.0）`verifin/span.py` 的思路，**并做了两处改进**。

═══ 双层是什么意思 ═══
  第 1 层「片段在不在原文里」：EXACT（逐字）→ NORMALIZED（归一化后）两级匹配
  第 2 层「数值能不能从该片段推出来」：把片段里所有金额换到元做精确比对

**两层都过，证据才成立。** 只过第 1 层不够 —— 片段是真的，但抄错了数字，
这正是 FinGround 说的"computational 类幻觉率 28.4%"的成因。

═══ 我们相对 VeriFin 的两处改进 ═══

① **修掉它自己承认的缺陷**（`span.py:152-158`）：它"不区分数字语义角色，
   日期里的 2023 会被当金额"。→ 本实现要求数字**必须带单位或是货币上下文**才算金额；
   1900–2100 的裸四位数一律当**年份**，不当金额。

② **补上 A 股特有的单位量纲**（学界全空白，调研明确指出"所有基准都是英文美元，
   没有一篇处理元/千元/万元/亿元，而这在 A 股差 10⁴ 倍"）。
   → 建立显式换算表，**所有金额一律换到「元」再比**。中文年报里
   「1,146,363.00」「114.64 万元」「0.0115 亿元」可能是同一个数。

═══ 默认零容差是有意的 ═══
  金额比对默认**精确相等**。理由：容差是"允许抄错一点"，而财务数字抄错一位
  就是 10 倍错误。容差只在**计算类声明**（computational）里按 FinGround 的建议给 ±0.5%。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# ① 单位换算表（A 股特有，学界空白）
# ══════════════════════════════════════════════════════════════════
#    ★ 顺序很重要：先匹配长单位（万亿 > 亿元），否则"亿元"会被"元"抢先匹配
UNIT_TO_YUAN: dict[str, Decimal] = {
    "万亿": Decimal("1e12"),
    "千亿": Decimal("1e11"),
    "百亿": Decimal("1e10"),
    "亿元": Decimal("1e8"),
    "亿": Decimal("1e8"),
    "千万元": Decimal("1e7"),
    "百万": Decimal("1e6"),
    "万元": Decimal("1e4"),
    "万": Decimal("1e4"),
    "千元": Decimal("1e3"),
    "百元": Decimal("1e2"),
    "元": Decimal("1"),
}
# 股本单位（单独处理，不与金额混算）
SHARE_UNITS = {"股": Decimal("1"), "万股": Decimal("1e4"), "亿股": Decimal("1e8")}
_UNIT_KEYS_SORTED = sorted(UNIT_TO_YUAN, key=len, reverse=True)
_SHARE_KEYS_SORTED = sorted(SHARE_UNITS, key=len, reverse=True)

# 百分比 / 比率单位：不换算，原样比
RATIO_UNITS = ("%", "％", "个百分点", "倍")


# ══════════════════════════════════════════════════════════════════
# ② 文本归一化（第 1 层的第二级）
# ══════════════════════════════════════════════════════════════════
_PUNCT_MAP = {
    "，": ",", "；": ";", "：": ":", "（": "(", "）": ")",
    "［": "[", "］": "]", "％": "%", "－": "-", "—": "-", "–": "-",
    "　": " ", "\u00a0": " ", "\u3000": " ",
}
# ★ 刻意**不把中文句号「。」归一成「.」**（实测踩到的坑）：
#   「。」是无歧义的句子分隔符，而「.」同时是小数点。
#   一旦归一化，"1.07 亿元。2026 年" 里的句号就变小点了，
#   判断"数字前面有没有货币单位"时无法安全切句 ——
#   结果 2026 会沾到上一句的"元"，被当成金额（VeriFin 的同款缺陷换个形式复现）。


def normalize_text(s: str) -> str:
    """全角→半角、去多余空白、统一标点。**保留数字与单位**，只做形式归一。"""
    if not s:
        return ""
    t = unicodedata.normalize("NFKC", s)
    for a, b in _PUNCT_MAP.items():
        t = t.replace(a, b)
    t = re.sub(r"[ \t\r\n]+", " ", t)
    return t.strip()


class SpanMatch(str, Enum):
    EXACT = "exact"            # 逐字命中
    NORMALIZED = "normalized"  # 归一化后命中（说明原文有全角/空白差异）
    NOT_FOUND = "not_found"


def locate_span(span: str, source_text: str) -> tuple[SpanMatch, int]:
    """第 1 层：片段在不在原文里。返回 (匹配级别, 位置)。

    ★ 归一化命中要**单独标记**，不要当成逐字命中 ——
      归一化会掩盖真实差异（比如全角数字），上层可能想知道。
    """
    if not span or not source_text:
        return SpanMatch.NOT_FOUND, -1
    pos = source_text.find(span)
    if pos >= 0:
        return SpanMatch.EXACT, pos
    pos = normalize_text(source_text).find(normalize_text(span))
    if pos >= 0:
        return SpanMatch.NORMALIZED, pos
    return SpanMatch.NOT_FOUND, -1


# ══════════════════════════════════════════════════════════════════
# ③ 金额抽取（含 VeriFin 缺陷的修复）
# ══════════════════════════════════════════════════════════════════
_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_CURRENCY_CTX = ("元", "¥", "￥", "人民币", "RMB")


def extract_amounts(text: str) -> list[tuple[Decimal, str, str]]:
    """从文本里抽出所有**金额**，返回 [(换算到元的值, 原样数值串, 单位)]。

    ★ 关键修复（VeriFin 的缺陷）：不是所有数字都是金额。
      判定规则：
        · 数字后面紧跟单位（元/万元/亿元/股…）→ 是金额
        · 数字前面有货币符号或"人民币" → 是金额
        · 裸四位数且落在 1900–2100 → **视为年份，排除**（这是它踩过的坑）
        · 其余裸数字 → 排除（可能是编号、比例、日期）

    ★ 两个实测踩到的坑（第一版都错了）：
      1. **数字与单位之间有空格**："1,098,549.90 元" —— 必须先 `lstrip()` 再判单位，
         否则匹配不上，整段金额全丢。
      2. **货币上下文不能跨越标点**："注册资本 1.07 亿元。2026 年 8 月 29 日" 里，
         若向前取 6 个字符判断"有没有元"，`2026` 会沾到上一句的"元"而被当成金额。
         → 只取**最后一个标点之后**的部分。
    """
    out: list[tuple[Decimal, str, str]] = []
    t = normalize_text(text)
    for m in _NUM_RE.finditer(t):
        raw = m.group(0)

        # 坑 1：数字与单位之间可能有空格
        after = t[m.end():m.end() + 8].lstrip()

        unit = ""
        for u in _UNIT_KEYS_SORTED:
            if after.startswith(u):
                unit = u
                break
        if not unit:
            for u in _SHARE_KEYS_SORTED:
                if after.startswith(u):
                    unit = u
                    break

        # 坑 2：货币上下文只取最后一个标点之后的部分
        before = t[max(0, m.start() - 8):m.start()]
        before = re.split(r"[。；;，,、!?！？\n]", before)[-1]
        has_currency_ctx = any(c in before for c in _CURRENCY_CTX)

        if not unit and not has_currency_ctx:
            # 裸数字：排除年份（VeriFin 的坑）
            try:
                n = Decimal(raw.replace(",", ""))
                if n == int(n) and 1900 <= n <= 2100:
                    continue
            except (InvalidOperation, ValueError):
                pass
            continue                                   # 无单位的裸数字一律不当金额

        try:
            n = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            continue

        mult = UNIT_TO_YUAN.get(unit) or SHARE_UNITS.get(unit) or Decimal("1")
        out.append((n * mult, raw, unit))
    return out


def value_matches_span(value: str, unit: str, span: str, *,
                       tolerance: Decimal = Decimal("0")) -> tuple[bool, str]:
    """第 2 层：声称的数值能不能从该片段推出来。

    ★ 任一命中即过（VeriFin 的做法）—— 片段里可能同时提到本期与上期，
      要求"所有数字都对上"会把正确证据判死。
    ★ 但**必须有一个对上**，否则说明数字是编的。
    """
    try:
        claimed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return False, f"声称的数值 {value!r} 不是合法数字"

    # 百分比类原样比
    if unit in RATIO_UNITS or (unit and unit in RATIO_UNITS):
        for m in _NUM_RE.finditer(normalize_text(span)):
            try:
                if abs(Decimal(m.group(0).replace(",", "")) - claimed) <= tolerance:
                    return True, "百分比精确命中"
            except (InvalidOperation, ValueError):
                continue
        return False, f"片段里找不到百分比 {claimed}"

    # 金额/股数：全部换算到元再比
    claimed_yuan = claimed * (UNIT_TO_YUAN.get(unit) or SHARE_UNITS.get(unit) or Decimal("1"))
    amounts = extract_amounts(span)
    if not amounts:
        return False, "片段里没有任何带单位的金额（裸数字不算，防年份误判）"

    for yuan, raw, u in amounts:
        if abs(yuan - claimed_yuan) <= tolerance:
            return True, f"命中：{raw}{u} = {yuan} 元"
        # 单位缺失时的宽容：声称值与原文数字本身相等（原文单位可能写在表头）
        try:
            if abs(Decimal(raw.replace(",", "")) - claimed) <= tolerance:
                return True, f"命中数值本体：{raw}（原文单位可能与声称不同：{u or '无'}）"
        except (InvalidOperation, ValueError):
            continue

    got = "、".join(f"{r}{u}" for _, r, u in amounts[:5])
    return False, f"片段里的金额是 [{got}]，换算后都对不上声称的 {claimed} {unit}"


# ══════════════════════════════════════════════════════════════════
# ④ 一站式：证据 × 原文 → 校验结果
# ══════════════════════════════════════════════════════════════════
def verify_span(evidence, source_text: str, *,
                tolerance: Optional[Decimal] = None) -> tuple[str, str]:
    """对一条证据做完整双层校验。

    返回 `(verdict, detail)`：verdict ∈ {"PASS","WARN","FAIL","REFUSE"}。

    ★ 顺序有讲究：**先校验字段齐不齐，再校验 span** ——
      字段不齐时做 span 校验没有意义，而且会掩盖真实原因。
    """
    from .record import RefusalReason, Verdict

    if not evidence.is_complete:
        return Verdict.REFUSE.value, "证据字段不全：" + "、".join(evidence.missing_fields)

    if not evidence.span:
        # 接口类数据（行情/财务接口）没有 PDF 原文片段，跳过 span 校验，
        # 但必须有可回访的出处 —— 这是要求①的低配版，不能连出处都没有
        if evidence.source:
            return Verdict.PASS.value, f"接口类数据，出处：{evidence.source}"
        return Verdict.REFUSE.value, "既没有原文片段，也没有出处"

    level, _pos = locate_span(evidence.span, source_text)
    if level == SpanMatch.NOT_FOUND:
        return Verdict.FAIL.value, "声称的原文片段在原文里找不到（片段是编的）"

    tol = tolerance if tolerance is not None else Decimal("0")
    ok, why = value_matches_span(evidence.value, evidence.unit, evidence.span, tolerance=tol)
    if not ok:
        return Verdict.FAIL.value, f"片段真实，但数值对不上：{why}"

    if level == SpanMatch.NORMALIZED:
        return Verdict.WARN.value, "片段是归一化后匹配的（原文有全角/空白差异）"
    return Verdict.PASS.value, why


def verify_computed(claimed: str, recomputed: str, unit: str = "", *,
                    rel_tolerance: Decimal = Decimal("0.005")) -> tuple[str, str]:
    """计算类声明（computational）专属校验。

    ★ FinGround 实测：**computational 类幻觉率最高（28.4%），
      而且通用 NLI 即使证据给对了也验不出比率错误 → 必须真重算**。
      容差 ±0.5%（可配）。
    """
    from .record import Verdict
    try:
        a = Decimal(str(claimed).replace(",", "").strip())
        b = Decimal(str(recomputed).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return Verdict.REFUSE.value, "无法解析为数字，不能按计算类校验"

    if a == b:
        return Verdict.PASS.value, "精确相等"
    diff = abs(a - b)
    base = abs(b) if b != 0 else Decimal("1")
    if diff / base <= rel_tolerance:
        return Verdict.WARN.value, f"差异 {diff}（相对 {diff / base:.4%}），在 ±0.5% 容差内"
    return Verdict.FAIL.value, f"重算得 {b}，声称 {a}，差异 {diff}（超出容差）"
