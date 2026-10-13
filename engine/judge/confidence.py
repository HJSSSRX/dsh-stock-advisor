# -*- coding: utf-8 -*-
"""置信度 —— **数出来的，不是感觉出来的**。

═══════════════════════════════════════════════════════════════════════════
出处：`设计/判断层-确定性打分-调研.md` §五
═══════════════════════════════════════════════════════════════════════════
★ 核心原则：置信度必须来自**决策台账的结算样本**，不许"随口说 0.7"。

主渠道：Wilson（*JASA*, 1927）二项比例区间估计，**取它的下界**当置信度。

    p̂ = h/N
    center    = (p̂ + z²/(2N)) / (1 + z²/N)
    halfwidth = z/(1 + z²/N) · sqrt( p̂(1-p̂)/N + z²/(4N²) )
    conf      = center - halfwidth          # z = 1.96（95%）

★ 为什么用**下界**而不是点估计（§5.2 原文）：
    N=30、命中 18 次时点估计是 0.60，但下界只有约 **0.42**。
    用下界 = **小样本自动保守**，而且它随样本增加自动收敛 ——
    不需要我们手工调一个「惩罚系数」。

★★ 口径纪律（照竞品血教训）：
    **看多 → 必须跑赢基准才算对；看空 → 必须跑输基准才算对；观望不计入。**
    ⚠️ 竞品曾把「Sell 之后下跌」（判断正确）记成失败，因为它把
       "标怎么走"当成了"判断准不准"。**方向正确率才是唯一衡量准不准的指标。**

★ N < 30 → 返回 None + 标「样本不足」，**不给数字**。
   想给也只能给一个**显式标注为策略默认值**的数。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# 95% 置信（双侧），与 §5.1 原文一致
Z95 = 1.96

# ★ 样本不足的门槛（写死，不许调用方随手调低）
MIN_SAMPLES = 30

# 分数桶：按 |S| 分档（§5.1）。边界用死区 δ 起。
BUCKET_EDGES = (0.50, 0.80)      # → 三档：[δ,0.50) / [0.50,0.80) / [0.80,1.0]


def bucket_of(score: float, delta: float) -> str:
    """把 |S| 分档。**同桶同方向**的历史样本才拿来数命中率。

    为什么必须分桶：`|S|`=0.9 的样本和 `|S|`=0.21 的样本混在一起算命中率，
    等于把"很有把握"和"刚过线"当同一件事 —— 那算出来的数字没有意义。
    """
    a = abs(score)
    if a < delta:
        return "观望"
    if a < BUCKET_EDGES[0]:
        return "低"
    if a < BUCKET_EDGES[1]:
        return "中"
    return "高"


def wilson_lower(h: int, n: int, z: float = Z95) -> float:
    """★ Wilson 区间的**下界**（§5.1 的公式，逐字实现）。

    h = 命中数，n = 已结算样本数。
    """
    if n <= 0:
        return 0.0
    if h < 0 or h > n:
        raise ValueError(f"命中数 {h} 不在 [0, {n}] 内")
    p = h / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, center - half)


def wilson_upper(h: int, n: int, z: float = Z95) -> float:
    """上界（报告区间时要用；也是 `judge/ablation.py` 里 CP 上界的姊妹口径）。"""
    if n <= 0:
        return 0.0
    p = h / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return min(1.0, center + half)


# ══════════════════════════════════════════════════════════════════
@dataclass
class BucketStat:
    """某个（分数桶 × 方向）的已结算样本。"""
    n: int = 0
    hits: int = 0

    def add(self, correct: bool) -> None:
        self.n += 1
        if correct:
            self.hits += 1

    @property
    def point(self) -> Optional[float]:
        return (self.hits / self.n) if self.n else None

    @property
    def lower(self) -> Optional[float]:
        return wilson_lower(self.hits, self.n) if self.n else None

    @property
    def upper(self) -> Optional[float]:
        return wilson_upper(self.hits, self.n) if self.n else None


@dataclass
class ConfidenceResult:
    value: Optional[float]                  # None = 样本不足
    source: str                             # ledger_wilson | policy_default | insufficient_samples
    bucket: str = ""
    n: int = 0
    hits: int = 0
    note: str = ""
    capped_by: str = ""                     # 被 claim.py 的 CONF_CAP 压过时说明

    def render(self) -> str:
        if self.value is None:
            return (f"置信度：**样本不足，不给数字**（{self.source}）"
                    f"｜分数桶「{self.bucket}」已结算 {self.n} 条"
                    f"（需 ≥ {MIN_SAMPLES} 条）")
        L = [f"置信度：{self.value:.1%}（来源 {self.source}）"]
        if self.n:
            L.append(f"  · 分数桶「{self.bucket}」：已结算 {self.n} 条、命中 {self.hits} 条"
                     f"（点估计 {self.hits/self.n:.1%}，**取的是 Wilson 下界**）")
        if self.capped_by:
            L.append(f"  · {self.capped_by}")
        if self.note:
            L.append(f"  · {self.note}")
        return "\n".join(L)


# claim.py 的策略上限（来源：judge/claim.py，已如实注明"是我们定的策略，不是实证结论"）
CONF_CAP = {1: 0.50, 2: 0.65, 3: 0.75}
CONF_CAP_MAX = 0.85


def cap_for(n_evidence: int) -> float:
    """按**可用依据条数**给置信度上限。"""
    if n_evidence <= 0:
        return 0.0
    if n_evidence in CONF_CAP:
        return CONF_CAP[n_evidence]
    return CONF_CAP_MAX


def confidence_for(direction: str, score: float, delta: float,
                   stats: Optional[dict] = None) -> ConfidenceResult:
    """★ 置信度 = **台账 Wilson 下界**，再与 `claim.py` 的策略上限取 min。

    stats: {(分数桶, 方向): BucketStat}。缺省 = 没有台账 = 样本不足。
    """
    if direction == "观望":
        return ConfidenceResult(None, "not_applicable", "观望", 0, 0,
                                note="观望不给置信度（观望不计入方向正确率）")
    b = bucket_of(score, delta)
    st = (stats or {}).get((b, direction))
    if st is None or st.n < MIN_SAMPLES:
        n = st.n if st else 0
        return ConfidenceResult(
            None, "insufficient_samples", b, n, st.hits if st else 0,
            note=f"该桶已结算 {n} 条，不足 {MIN_SAMPLES} 条 —— "
                 "**不给数字**（编一个 0.7 是本项目明令禁止的）")
    return ConfidenceResult(st.lower, "ledger_wilson", b, st.n, st.hits)


def combine(score_conf: ConfidenceResult, n_evidence: int) -> ConfidenceResult:
    """与 `claim.py` 的 CONF_CAP 叠加：`min(打分器置信度, 依据数上限)`。

    ★ 两个来源都要打出来（`claim.py` 已有 `requested_confidence` 做这件事）。
    """
    cap = cap_for(n_evidence)
    if score_conf.value is None:
        score_conf.capped_by = (f"依据 {n_evidence} 条 → 口径上限 {cap:.0%}"
                                f"（**这是我们定的策略，不是实证结论**）")
        return score_conf
    if score_conf.value > cap:
        score_conf.capped_by = (f"被依据数上限压过：打分器给 {score_conf.value:.1%}，"
                                f"依据 {n_evidence} 条 → 上限 {cap:.0%}")
        score_conf.value = cap
    return score_conf


# ══════════════════════════════════════════════════════════════════
# 结算：**方向正确率的唯一口径**（照竞品血教训）
# ══════════════════════════════════════════════════════════════════
def settle(direction: str, stock_return: float, benchmark_return: float) -> Optional[bool]:
    """把一次决策结算成"对/错"。**观望返回 None（不计入）。**

    ★★ 口径：
        看多 → 个股收益 **跑赢**基准才算对
        看空 → 个股收益 **跑输**基准才算对
        观望 → 不计入（None）

    ⚠️ 竞品曾把「Sell 之后下跌」（判断正确）记成失败 ——
       因为它把"标怎么走"当成了"判断准不准"。这个函数就是为了避免那个错。
    """
    if direction == "观望":
        return None
    if direction == "看多":
        return stock_return > benchmark_return
    if direction == "看空":
        return stock_return < benchmark_return
    raise ValueError(f"未知方向：{direction!r}")


def build_stats(records) -> dict:
    """从已结算记录建统计表。

    records: 可迭代的 (direction, score, delta, stock_return, benchmark_return)
             或含这些字段的对象。
    """
    out: dict = {}
    for r in records:
        if isinstance(r, dict):
            direction, score, delta = r["direction"], r["score"], r["delta"]
            sr, br = r["stock_return"], r["benchmark_return"]
        else:
            direction, score, delta, sr, br = r
        ok = settle(direction, sr, br)
        if ok is None:
            continue
        key = (bucket_of(score, delta), direction)
        out.setdefault(key, BucketStat()).add(ok)
    return out
