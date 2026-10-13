# -*- coding: utf-8 -*-
"""结论构造器 —— **没有依据的结论，构造不出来。**

═══════════════════════════════════════════════════════════════════
为什么这一步要"抛错"，而不是"提醒要写"
═══════════════════════════════════════════════════════════════════
判断层最容易出的问题不是算错，是**说了一句没有出处的话，而没人拦**。

    「茅台基本面稳健，可以关注。」   ← 看起来正常，其实**一个依据都没有**

如果这只是一条"提醒"，模型完全可以不写、也可以随手补一句
看起来像出处的废话（"根据公开信息"）。所以本层把它做成**构造函数**：

    build(...) 拿不到至少一条**能进结论**的依据 → 直接抛 NoEvidenceError

**结论在物理上讲不出来。** 这是 `dsh-research-report` 的做法
（未核实的 claim 带可见标记，绝不静默通过），也是 `financial-risk-agent`
的 "No valid AssuranceResult → no authorized final_decision"。

═══════════════════════════════════════════════════════════════════
每个结论长什么样（调研报告 §五②）
═══════════════════════════════════════════════════════════════════
    {结论文本, 依据列表, 口径, 置信度, 反方观点, 失效条件}

其中：
  · 依据列表 —— 每条是 `Citation`：证据 + 数据层 kind + span 核验结论
  · 反方观点 / 失效条件 —— **本模块只留插槽**，由 `judge/bear.py`（第 4 块）填
  · 不算数的引用**不会被丢掉**，进 `Claim.unusable` 并写明原因（静默丢弃 = 隐瞒）

═══════════════════════════════════════════════════════════════════
六种标记（有任何一个，结论就是"站不住"）
═══════════════════════════════════════════════════════════════════
    依据不足      有引用不可用（字段不全 / 引用号对不上 / span 核验失败 / 来源不能进结论）
    单一依据      可用依据 < 2 —— 一条依据撑不起一个结论
    证据冲突      同一 (主体,期间,指标) 出现多个不一致的数值
    口径不一致    同一指标挂了两个不同口径（如 归属净利润 vs 扣非净利润）
    口径缺失      结论没写口径（合并/母公司、复权方式…）
    置信度超限    自报把握超出依据条数能支持的上限，已压到上限

★ **冲突只报冲突，不调和**（TradingAgents 的原话：
  "flag the discrepancy rather than inventing a reconciled number"）——
  本模块**不会**去平均、也不会挑一个"看起来更对"的值。

★ 置信度上限是**我们定的策略，不是实证结论**（没有数据支持"2 条依据就只能 65%"）。
  定它的理由：让"把握"必须跟着依据条数走，不能空口说 90%。数值可调。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from evidence.record import Evidence, Verdict
from evidence.trust import can_enter_conclusion

from .ledger import DIRECTIONS

# ══════════════════════════════════════════════════════════════════
# 策略常量
# ══════════════════════════════════════════════════════════════════
MIN_EVIDENCE = 2          # 少于这个数 → 标「单一依据」
CONF_CAP = {1: 0.50, 2: 0.65, 3: 0.75}   # 按可用依据条数封顶；≥4 条用 CONF_CAP_MAX
CONF_CAP_MAX = 0.85

# 标记名（写死一处，免得调用方各自拼字符串）
F_INSUFFICIENT = "依据不足"
F_SINGLE = "单一依据"
F_CONFLICT = "证据冲突"
F_CALIBER_MISMATCH = "口径不一致"
F_CALIBER_MISSING = "口径缺失"
F_CONF_CAP = "置信度超限"
# ★ 第七种标记：消融判定结论**脆弱** —— 它必须进 flags，
#   否则"降级为脆弱"只是一句印在纸上的话，契约还是会报"全部通过"。
F_FRAGILE = "结论脆弱"


class NoEvidenceError(ValueError):
    """没有可用依据 → **结论构造不出来**。这是抛错，不是提醒。"""


class ClaimInvalid(ValueError):
    """结论本身不合法（方向不认识等）。"""


# ══════════════════════════════════════════════════════════════════
# 引用：证据 + 它是从哪个 kind 来的 + 核验结论
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Citation:
    """一条依据。

    ★ 为什么还要 `kind`：证据层的六元组里**没有"这是哪类数据"**，
      而"能不能进结论"恰恰是按 kind 分档的（`evidence/trust.py`）。
      不把 kind 带过来，分档就落不了地 —— 那样任何一条引用都能进结论。
    """
    evidence: Evidence
    kind: str = ""            # 数据层 kind（quote / fundflow / financial_sina …）
    verify: str = ""          # span.verify_span 的结论：PASS/WARN/FAIL/REFUSE；接口类数据可留空
    note: str = ""

    @property
    def eid(self) -> str:
        return self.evidence.eid

    def to_dict(self) -> dict:
        return {"eid": self.eid, "kind": self.kind, "verify": self.verify,
                "note": self.note, "evidence": self.evidence.to_dict()}


def _eid_matches(ev: Evidence) -> bool:
    """引用号能不能重算出来。

    `Evidence.eid` 是内容寻址的（字段 → sha256），所以**改过任何字段就对不上**。
    这里不重写哈希逻辑，而是照着字段重新构造一个 —— 保证与证据层同一处实现。
    """
    fresh = Evidence(company=ev.company, period=ev.period, metric=ev.metric,
                     value=ev.value, unit=ev.unit, source=ev.source, caliber=ev.caliber)
    return fresh.eid == ev.eid


def _problems(c: Citation, used: dict[str, int]) -> list[str]:
    """这条引用有哪些毛病。**返回空列表才算可用。**"""
    ev = c.evidence
    out: list[str] = []

    if not ev.is_complete:
        out.append("证据不完整（缺 " + "、".join(ev.missing_fields) + "）")
    elif not _eid_matches(ev):
        out.append("引用号与内容对不上（这条被改过）")

    if not c.kind:
        out.append("没标数据来源 kind —— 保守默认不能进结论")
    elif not can_enter_conclusion(c.kind):
        out.append(f"来源「{c.kind}」按可信度分档**不能进结论**")

    if c.verify in (Verdict.FAIL.value, Verdict.REFUSE.value):
        out.append(f"span 核验 {c.verify}" + (f"：{c.note}" if c.note else ""))

    # ★ 同一条证据被引用两次，**不能当两条依据**（只有在前面那次可用时才判）
    if not out and ev.eid in used:
        out.append(f"与第 {used[ev.eid]} 条是同一条证据（引用号相同）—— 不能算两条依据")
    return out


# ══════════════════════════════════════════════════════════════════
# 结论
# ══════════════════════════════════════════════════════════════════
@dataclass
class Claim:
    code: str
    as_of: str
    direction: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    unusable: list[tuple[Citation, list[str]]] = field(default_factory=list)
    caliber: str = ""
    confidence: float = 0.0
    requested_confidence: float = 0.0
    name: str = ""
    flags: dict[str, str] = field(default_factory=dict)
    # ── 以下由 judge/bear.py（第 4 块）填 ──
    bear: str = ""                                  # 反方论证
    bear_evidence_ids: list[str] = field(default_factory=list)
    invalidators: list[str] = field(default_factory=list)   # 失效条件

    # ------------------------------------------------------------------
    @property
    def usable(self) -> list[Citation]:
        return self.citations

    @property
    def evidence_ids(self) -> list[str]:
        """★ 进决策台账的就是这一串 —— 记下的依据必须与说出去的一致。"""
        return [c.eid for c in self.citations]

    @property
    def sufficient(self) -> bool:
        """★ 依据站不站得住。**有任何一个标记就是站不住**，必须如实报给用户。"""
        return not self.flags

    def marker(self) -> str:
        return ("[" + "·".join(self.flags) + "]") if self.flags else ""

    # ------------------------------------------------------------------
    def render(self, with_evidence: bool = True) -> str:
        """给用户/模型看的形态。**标记必须出现在正文里**，不许藏在字段里。"""
        L = [f"{self.text}{self.marker()}"]
        if self.requested_confidence != self.confidence:
            L.append(f"　方向：{self.direction}｜置信度：{self.confidence:.0%}"
                     f"（自报 {self.requested_confidence:.0%}）")
        else:
            L.append(f"　方向：{self.direction}｜置信度：{self.confidence:.0%}")
        L.append(f"　口径：{self.caliber}" if self.caliber else "　口径：**没写**")
        if self.bear:
            L.append(f"　反方：{self.bear}")
        if self.invalidators:
            L.append("　失效条件：" + "；".join(self.invalidators))
        if self.flags:
            L.append("　⚠️ " + "；".join(f"{k} —— {v}" for k, v in self.flags.items()))
        if with_evidence:
            L.append(f"　依据（{len(self.citations)} 条）：")
            for i, c in enumerate(self.citations, 1):
                kind = f"｜来源 {c.kind}" if c.kind else ""
                L.append(f"　　{i}. {c.evidence.tool_message()}{kind}")
            if self.unusable:
                L.append(f"　⚠️ 不可用引用（{len(self.unusable)} 条，**没被丢掉，只是不算数**）：")
                for c, probs in self.unusable:
                    L.append(f"　　- {c.evidence.metric or '（无指标名）'}：" + "；".join(probs))
        return "\n".join(L)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "as_of": self.as_of,
            "direction": self.direction, "text": self.text,
            "confidence": self.confidence,
            "requested_confidence": self.requested_confidence,
            "caliber": self.caliber, "bear": self.bear,
            "bear_evidence_ids": self.bear_evidence_ids,
            "invalidators": self.invalidators,
            "flags": dict(self.flags), "sufficient": self.sufficient,
            "evidence_ids": self.evidence_ids,
            "citations": [c.to_dict() for c in self.citations],
            "unusable": [{"eid": c.eid, "metric": c.evidence.metric, "problems": p}
                         for c, p in self.unusable],
        }


# ══════════════════════════════════════════════════════════════════
# ★★ 构造：本模块的牙齿
# ══════════════════════════════════════════════════════════════════
def build(*, code: str, as_of: str, direction: str, text: str,
          citations: Optional[list[Citation]] = None,
          caliber: str = "", confidence: float = 0.0, name: str = "",
          invalidators: Optional[list[str]] = None, bear: str = "",
          bear_evidence_ids: Optional[list[str]] = None) -> Claim:
    """构造一条结论。**依据不够就抛 NoEvidenceError。**"""
    if direction not in DIRECTIONS:
        raise ClaimInvalid(f"方向只能是 {DIRECTIONS}，收到 {direction!r}")

    cites = list(citations or [])
    if not cites:
        raise NoEvidenceError(
            "这条结论**一条依据都没有** —— 构造不出来。"
            "不许先写结论再补出处；有结论就必须先有能进结论的证据。"
        )

    usable: list[Citation] = []
    unusable: list[tuple[Citation, list[str]]] = []
    used: dict[str, int] = {}                 # eid → 第几条（只登记可用的）
    for i, c in enumerate(cites, 1):
        probs = _problems(c, used)
        if probs:
            unusable.append((c, probs))
            continue
        used[c.evidence.eid] = i
        usable.append(c)

    if not usable:
        detail = "；".join(f"[{c.evidence.metric or '?'}] " + "，".join(p)
                          for c, p in unusable)
        raise NoEvidenceError(
            f"这条结论的**每一条依据都不可用**（{len(unusable)} 条），等于没有依据 —— 结论作废。"
            + detail
        )

    flags: dict[str, str] = {}
    if unusable:
        flags[F_INSUFFICIENT] = f"{len(unusable)} 条引用不可用"
    if len(usable) < MIN_EVIDENCE:
        flags[F_SINGLE] = f"可用依据只有 {len(usable)} 条（< {MIN_EVIDENCE}）"
    if not caliber.strip():
        flags[F_CALIBER_MISSING] = "没写口径（合并/母公司、复权方式…）"

    # ── 冲突：同一(主体,期间,指标)出现多个不一致的数值 ──
    by_key: dict[tuple[str, str, str], list[Citation]] = {}
    for c in usable:
        ev = c.evidence
        by_key.setdefault((ev.company, ev.period, ev.metric), []).append(c)
    clashes = []
    for (company, period, metric), group in by_key.items():
        vals = {g.evidence.value for g in group}
        if len(vals) > 1:
            got = "、".join(f"{g.evidence.value} {g.evidence.unit}@{g.eid}" for g in group)
            clashes.append(f"{company}｜{period}｜{metric}：{got}")
    if clashes:
        # ★ 不调和、不平均、不挑一个 —— 原样报出来让用户知道这里有分歧
        flags[F_CONFLICT] = ("**只报冲突，不调和** —— " + "；".join(clashes))

    # ── 口径：同一指标挂了两个口径 ──
    cals: dict[str, set[str]] = {}
    for c in usable:
        if c.evidence.caliber:
            cals.setdefault(c.evidence.metric, set()).add(c.evidence.caliber)
    mixed = {m: sorted(s) for m, s in cals.items() if len(s) > 1}
    if mixed:
        flags[F_CALIBER_MISMATCH] = ("同一指标有多个口径，**不许直接比较**：" +
                                     "；".join(f"{m}={'/'.join(s)}" for m, s in mixed.items()))

    # ── 置信度：按依据条数封顶 ──
    n = len(usable)
    # ★ 注意别写成 min(n, len(CONF_CAP))：那样 4 条依据也会落到 0.75 那一档，
    #   永远到不了 0.85（第一次就是这么写的，被负对照逮住）。
    cap = CONF_CAP.get(n, CONF_CAP_MAX)
    asked = max(0.0, min(1.0, float(confidence or 0.0)))
    conf = asked
    if asked > cap:
        flags[F_CONF_CAP] = (f"自报 {asked:.0%} 超出 {n} 条依据支持的上限 {cap:.0%}"
                             "（上限是策略，可按需调整）")
        conf = cap

    return Claim(
        code=code, name=name, as_of=as_of, direction=direction, text=text,
        citations=usable, unusable=unusable, caliber=caliber.strip(),
        confidence=conf, requested_confidence=asked, flags=flags,
        bear=bear, bear_evidence_ids=list(bear_evidence_ids or ()),
        invalidators=list(invalidators or ()),
    )
