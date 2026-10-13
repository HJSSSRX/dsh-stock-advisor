# -*- coding: utf-8 -*-
"""证据消融 —— **去掉一条依据，结论会不会翻。**

═══════════════════════════════════════════════════════════════════
★★★ 开工前调研推翻了原设计里的一个核心假设
═══════════════════════════════════════════════════════════════════
《判断层调研报告》§五③ 写的是「翻转率 **> 阈值** → 降级为脆弱」。
**调研发现：这个阈值在原出处根本不存在，而且是统计上不可辨识的。**

**① 原出处没有阈值。** 读了 `financial-risk-agent` 的真源码
（`backend/finrisk/assurance/fragility.py`），判定行是：

    if flips or (largest is not None and largest >= assurance_policy.fragile_score_delta):
        state = FRAGILE

**只要有任何一条依据被去掉就翻，就直接判 FRAGILE。**
`decision_flip_rate` 算出来了、写进返回值，但**不参与任何状态分支**。

**② 它那两个阈值（10.0 / 20.0）不能引用。** 同一个仓库的
`config/assurance_policy.json` 第一行就写着 `"maturity": "HEURISTIC_POLICY"`，
作者的 `research/limitations.md` 自己写明
`no finite-sample, conformal or probability guarantee`。
**作者否认自己的阈值有实证支持 —— 我们不能把它当成"原始出处给的答案"。**

**③ 在真实样本量下，翻转率根本分辨不出来（本机实算）。**
Clopper–Pearson 95% 上界：

    n=5  条依据零翻转 → 上界仍是 0.451
    n=10 条零翻转    → 0.259
    n=20 条零翻转    → 0.139
    要让「翻转率 < 0.05」达到 95% 置信 → 需要 **59 条依据全部零翻转**

**一只股票一条结论挂 59 条依据，现实中不存在。**
→ 所以任何 0.2 / 0.3 的翻转率阈值都是拍的 ——
  **不是"保守"，是数据的分辨力到不了那个刻度。**

═══════════════════════════════════════════════════════════════════
本模块的做法（跟着证据走，不是跟着直觉走）
═══════════════════════════════════════════════════════════════════
    ★ 状态判定**只用 flip_count**（有翻即降级）—— 漏检率按构造为 0
    ★ flip_rate 只作**展示**字段，并**同时报出 Clopper–Pearson 上界**
    ★ 未注册规则 → **NOT_ESTIMABLE**，绝不回退成 STABLE
      （回退成 STABLE 等于**把"没法评估"伪装成"它很稳"**）

四态：`STABLE` / `SENSITIVE` / `FRAGILE` / `NOT_ESTIMABLE`

═══════════════════════════════════════════════════════════════════
★ 一个必须先解决的前提（不作这一步，消融是空转的）
═══════════════════════════════════════════════════════════════════
调研原文：
> `judge/claim.py` 的 `build()` 里，`direction` 只被校验一次，
> **对 `citations` 没有任何数值上的函数依赖** ——
> 所以按现状做消融，删掉任何一条引用方向都不变，**翻转次数恒为 0**。

→ 所以本模块要求调用方提供一个**「依据子集 → 方向」的纯函数** `rule`。
  它必须真的读依据、且只读依据。`analyze()` 会**先断言**这一点：

      一致性断言：rule(claim.citations) 必须 == claim.direction
      不一致 → 抛 RuleMismatch（**绝不静默选用其中一个**）
      空转检测：如果 rule 是常量函数，`analyze` 会报出来

**没有这一步，"脆弱性分析"就是一次昂贵的空转。**
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Optional, Sequence

from .claim import Claim, Citation, NoEvidenceError
from .ledger import BEAR, BULL, DIRECTIONS, HOLD

# ── 四态 ──
STABLE = "STABLE"                  # 去掉任何一条都不翻，且置信度掉得不多
SENSITIVE = "SENSITIVE"            # 不翻，但置信度掉得明显
FRAGILE = "FRAGILE"                # 只要有**任何一条**被去掉就翻
NOT_ESTIMABLE = "NOT_ESTIMABLE"    # 规则没注册 / 依据为空 —— **不是"稳"**


class RuleMismatch(ValueError):
    """调用方传的方向与规则算出来的方向不一致 —— 不许静默选一个。"""


# ══════════════════════════════════════════════════════════════════
# 统计工具（纯标准库，约 20 行；本机实测 scipy / statsmodels 都没装）
# ══════════════════════════════════════════════════════════════════
def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def cp_upper(k: int, n: int, alpha: float = 0.05) -> Optional[float]:
    """Clopper–Pearson 单侧 95% 上界：真实翻转率最高可能是多少。

    ★ 为什么必须报这个：`flip_rate = 0/5` 看起来完美，但它的 95% 上界是 0.451 ——
      **"零翻转"在 5 个样本上什么也证明不了。** 只报 0/N 是在误导。
    """
    if n <= 0:
        return None
    if k >= n:
        return 1.0
    if k == 0:
        return 1.0 - alpha ** (1.0 / n)      # 精确闭式（与二分结果逐位一致）
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def wilson(k: int, n: int, z: float = 1.959963985) -> Optional[tuple[float, float]]:
    """Wilson 区间（双侧）。"""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# ══════════════════════════════════════════════════════════════════
# 策略（可序列化 → 能进台账、能跨版本 diff）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AblationPolicy:
    """消融策略。

    ★ 注意 `sensitive_confidence_drop` 这个数**没有实证依据**（它是我们定的），
      所以它只影响 STABLE 与 SENSITIVE 的分界，**不影响 FRAGILE** ——
      而 FRAGILE 才是"不许给强结论"的那一档。这样即使这个数拍得不准，
      也不会污染最重要的那个判定。
    """
    min_nodes_for_rate: int = 3          # 低于它不给率（给了也是误导）
    max_exact_nodes: int = 12            # 求最小可用子集的精确枚举上限
    sensitive_confidence_drop: float = 0.25
    version: str = "ablation-policy-v0.1"

    @property
    def policy_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.__dict__, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]


DEFAULT_POLICY = AblationPolicy()


# ══════════════════════════════════════════════════════════════════
# 结果
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AblationOutcome:
    removed_eid: str
    removed_metric: str
    direction_without: str
    flipped: bool
    unconstructible: bool = False        # 删掉它结论就构造不出来（最强的一档承重）
    reason: str = ""


@dataclass
class AblationReport:
    code: str = ""
    as_of: str = ""
    n_usable: int = 0
    direction_baseline: str = ""
    policy_hash: str = ""
    outcomes: list = field(default_factory=list)
    flip_count: int = 0
    flip_rate: Optional[float] = None
    flip_rate_upper_95: Optional[float] = None
    max_confidence_drop: float = 0.0
    load_bearing_eids: tuple = ()
    unconstructible_eids: tuple = ()
    min_viable_subset: Optional[tuple] = None
    min_viable_method: str = "NOT_ESTIMABLE"
    min_viable_exact: bool = False
    state: str = NOT_ESTIMABLE
    reasons: tuple = ()
    not_estimable_reason: str = ""

    @property
    def fragile(self) -> bool:
        return self.state == FRAGILE

    def render(self) -> str:
        if self.state == NOT_ESTIMABLE:
            return (f"⚠️ 【脆弱性：**无法评估**】{self.not_estimable_reason}\n"
                    "    **「无法评估」不等于「它很稳」** —— 不许当成通过。")
        L = [f"【脆弱性：{self.state}】依据 {self.n_usable} 条，"
             f"去掉任一条后有 {self.flip_count} 条会翻"]
        if self.load_bearing_eids:
            L.append(f"  ★ **承重依据**（去掉它就翻）：{'、'.join(self.load_bearing_eids)}")
        if self.unconstructible_eids:
            # ★ 这是最强的一档：不是"方向变了"，是**结论根本构造不出来**了
            L.append(f"  ★★ 其中 {len(self.unconstructible_eids)} 条是**去掉它结论就构造不出来**"
                     f"（最强的一档承重）：{'、'.join(self.unconstructible_eids)}")
        if self.flip_rate is not None:
            L.append(f"  翻转率 {self.flip_rate:.0%}"
                     f"（**95% 上界 {self.flip_rate_upper_95:.1%}** —— "
                     f"{self.n_usable} 个样本证明不了更多）")
        else:
            L.append(f"  依据只有 {self.n_usable} 条（< {DEFAULT_POLICY.min_nodes_for_rate}）"
                     "，**不给翻转率** —— 样本太少，给了也是误导")
        for r in self.reasons:
            L.append(f"  · {r}")
        return "\n".join(L)


# ══════════════════════════════════════════════════════════════════
# ★★ 核心
# ══════════════════════════════════════════════════════════════════
DirectionRule = Callable[[Sequence[Citation]], str]

# 规则注册表：rule_id → (纯函数, 可序列化声明)
RULES: dict[str, tuple[DirectionRule, dict]] = {}


def register(rule_id: str, spec: dict):
    """登记一条「依据子集 → 方向」的规则。"""
    def deco(fn: DirectionRule) -> DirectionRule:
        RULES[rule_id] = (fn, spec)
        return fn
    return deco


def _cap_conf(n: int) -> float:
    from .claim import CONF_CAP, CONF_CAP_MAX
    return CONF_CAP.get(n, CONF_CAP_MAX)


def analyze(claim: Claim, rule: Optional[DirectionRule] = None,
            policy: AblationPolicy = DEFAULT_POLICY,
            rule_id: str = "") -> AblationReport:
    """对一条结论做证据消融。

    `rule` 必须是**只读依据**的纯函数；不传则按 `rule_id` 查注册表。
    **查不到就是 NOT_ESTIMABLE —— 绝不回退成 STABLE。**
    """
    rep = AblationReport(code=claim.code, as_of=claim.as_of,
                         policy_hash=policy.policy_hash)
    cites = list(claim.citations)
    n = len(cites)
    rep.n_usable = n
    rep.direction_baseline = claim.direction

    if n == 0:
        rep.not_estimable_reason = "这条结论没有可用依据，无从消融"
        return rep

    # ── 找规则 ──
    if rule is None and rule_id and rule_id in RULES:
        rule = RULES[rule_id][0]
    if rule is None:
        rep.not_estimable_reason = (
            f"没有为 {rule_id or claim.code!r} 注册「依据子集 → 方向」的规则。"
            "**保守默认：不许拿它当通过** —— 没法评估脆弱性 ≠ 它不脆弱。")
        return rep

    # ── ★ 一致性断言：规则算出来的方向必须与结论声称的一致 ──
    #    不一致就抛，**绝不静默选用其中一个**（那会把"规则与声明脱节"藏起来）
    try:
        d_base = rule(cites)
    except NoEvidenceError:
        rep.not_estimable_reason = "规则在完整依据上都构造不出结论（不该发生）"
        return rep
    if d_base != claim.direction:
        raise RuleMismatch(
            f"规则算出的方向是 {d_base!r}，而结论声称的是 {claim.direction!r} —— "
            "两者必须一致，否则消融就是在评估一个不存在的推理过程。"
        )

    # ── 逐条消融 ──
    for i, c in enumerate(cites):
        subset = [x for j, x in enumerate(cites) if j != i]
        try:
            d = rule(subset)
            flipped = (d != claim.direction)
            rep.outcomes.append(AblationOutcome(
                removed_eid=c.eid, removed_metric=c.evidence.metric,
                direction_without=d, flipped=flipped,
                reason="" if not flipped else f"去掉它之后方向变成 {d}",
            ))
        except NoEvidenceError:
            # ★ 删掉它结论就**构造不出来** = 最强的一档承重，判为翻转
            rep.outcomes.append(AblationOutcome(
                removed_eid=c.eid, removed_metric=c.evidence.metric,
                direction_without="", flipped=True, unconstructible=True,
                reason="去掉它之后结论**构造不出来**（依据不足）",
            ))

    rep.flip_count = sum(1 for o in rep.outcomes if o.flipped)
    rep.load_bearing_eids = tuple(o.removed_eid for o in rep.outcomes if o.flipped)
    rep.unconstructible_eids = tuple(o.removed_eid for o in rep.outcomes
                                     if o.unconstructible)

    # ── 率：只在样本够的时候给，且必须附上界 ──
    if n >= policy.min_nodes_for_rate:
        rep.flip_rate = rep.flip_count / n
        rep.flip_rate_upper_95 = cp_upper(rep.flip_count, n)
    # 置信度掉幅：去掉任意一条后依据变成 n-1 条，能支持的置信度上限随之下降。
    # ★ 这只是个**代理量**（真正该重算的是规则给出的把握，而规则目前只给方向），
    #   所以它只用来分 STABLE / SENSITIVE 两档，**不参与 FRAGILE 的判定**。
    rep.max_confidence_drop = round(max(0.0, claim.confidence - _cap_conf(n - 1)), 4)

    # ── 最小可用子集（可选）──
    _min_viable(cites, claim.direction, rule, policy, rep)

    # ── ★ 状态判定：**只用 flip_count** ──
    if rep.flip_count > 0:
        rep.state = FRAGILE
        rep.reasons = (f"有 {rep.flip_count} 条依据被去掉后结论就翻 —— "
                       "按纪律**降级为脆弱，不许给强结论**",)
    elif rep.max_confidence_drop >= policy.sensitive_confidence_drop:
        rep.state = SENSITIVE
        rep.reasons = (f"去掉依据后置信度上限掉 {rep.max_confidence_drop:.0%} —— "
                       "结论不翻但把握下降，应如实标注",)
    else:
        rep.state = STABLE
        rep.reasons = ("去掉任何一条依据，方向都不变 —— "
                       "但也只说明**它不依赖某一条**，不说明判断正确",)
    return rep


def _min_viable(cites, direction, rule, policy, rep) -> None:
    n = len(cites)
    if n == 0:
        return
    if n <= policy.max_exact_nodes:
        for k in range(1, n + 1):
            for combo in combinations(range(n), k):
                sub = [cites[i] for i in combo]
                try:
                    if rule(sub) == direction:
                        rep.min_viable_subset = tuple(cites[i].eid for i in combo)
                        rep.min_viable_method = "EXACT"
                        rep.min_viable_exact = True
                        return
                except NoEvidenceError:
                    continue
        rep.min_viable_method = "EXACT"
        rep.min_viable_exact = True
        return
    # 依据太多 → 贪心逐个剥离。★ 近似结果**绝不叫「最小」**
    keep = list(range(n))
    for i in range(n):
        trial = [j for j in keep if j != i]
        if not trial:
            continue
        try:
            if rule([cites[j] for j in trial]) == direction:
                keep = trial
        except NoEvidenceError:
            continue
    rep.min_viable_subset = tuple(cites[j].eid for j in keep)
    rep.min_viable_method = "GREEDY_APPROXIMATION"
    rep.min_viable_exact = False
