# -*- coding: utf-8 -*-
"""权重敏感性 —— **闭式解，不是枚举、不是模拟**。

═══════════════════════════════════════════════════════════════════════════
出处（`设计/判断层-确定性打分-调研.md` §3.4）
═══════════════════════════════════════════════════════════════════════════
O'Shea, Deeney, Triantaphyllou, Diaz-Balteiro & Tarim,
*Weight stability intervals for multi-criteria decision analysis using the
weighted sum model*, Expert Systems with Applications 296:128460 (2025).

该文指出：**以往**求权重稳定性区间必须先手工改权重、或枚举、或模拟；
它给出不依赖枚举/模拟、**直接求出精确区间**的方法。

我们的打分器是**线性的**，所以有闭式解 —— 这不是近似，是精确值。

═══════════════════════════════════════════════════════════════════════════
推导（一次一个权重 OAT，其余按比例重归一化）
═══════════════════════════════════════════════════════════════════════════
设基准权重 w（Σw=1），信号归一分 a_k = s_k·z_k，z_k ∈ [-1,1]，S = Σ w_k·a_k。

把第 k 项的权重掰到 t，其余按比例重归一化：

    w_k(t) = t ,  w_j(t) = w_j·(1-t)/(1-w_k)     (j ≠ k)

记 B_k = Σ_{j≠k} w_j·a_j，则

    S(t) = t·a_k + (1-t)/(1-w_k)·B_k
         = α_k·t + β_k            ← **关于 t 严格线性**

    其中  β_k = B_k/(1-w_k) ,  α_k = a_k − B_k/(1-w_k)

于是「标签在哪个 t 区间内不变」= 线性不等式的解，闭式。

═══════════════════════════════════════════════════════════════════════════
★ 硬性降级规则（§3.4 原文）
═══════════════════════════════════════════════════════════════════════════
    flip_rate > 5%  或  稳定区间宽度 < 0.10  或  δ 扫描内标签变化
        → **方向必须降为「观望」，不许给强结论**
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

# 硬性降级阈值（写死在这里，不许调用方随手改）
FLIP_RATE_MAX = 0.05           # 5%
STABILITY_WIDTH_MIN = 0.10     # 0.10
DELTA_GRID = (0.10, 0.15, 0.20, 0.25, 0.30)


def label_of(score: float, delta: float) -> str:
    """把总分 S 映射成标签。**这是判方向的唯一出口。**"""
    if score >= delta:
        return "看多"
    if score <= -delta:
        return "看空"
    return "观望"


@dataclass(frozen=True)
class Stability:
    """第 k 项权重的稳定性区间。"""
    key: str
    lo: float                     # t 的下界
    hi: float                     # t 的上界
    alpha: float                  # S(t) = alpha*t + beta
    beta: float
    base_label: str
    degenerate: bool = False      # 区间为空（无论 t 怎么动，标签都变）→ 天然不稳

    @property
    def width(self) -> float:
        return max(0.0, self.hi - self.lo)

    def at(self, t: float) -> float:
        return self.alpha * t + self.beta

    def __str__(self) -> str:
        return (f"{self.key}: t∈[{self.lo:.4f}, {self.hi:.4f}] 宽 {self.width:.4f}"
                f"（S(t)={self.alpha:+.4f}·t{self.beta:+.4f}）")


def resolve_weights(weights: dict, scores: dict) -> tuple[dict, list[tuple[str, str]]]:
    """把 (权重, 分数) 解析成生效权重 + **被剔除的项及原因**。

    ★★ 两条规矩，分开管：

      (1) **manifest 的权重和必须为 1** —— 不为 1 就抛错。
          这是 §9.5 点名的「权重和不为 1（必须拒绝，**不许静默归一化**）」。
          它管的是**规格本身**有没有写错。

      (2) **指标缺数据**时，在**剩下的项上重归一化** ——
          WSM 的标准做法，本身没问题；**有问题的是"静默"**。
          所以这里把每一个被剔除的项连同原因返回出去，
          调用方必须把它放进 `DirectionResult.excluded`。

    ★ 换句话说：**"不许静默归一化" ≠ "不许归一化"，而是"不许不吭声"。**
    """
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"manifest 权重和 = {total!r}，不为 1 —— 必须拒绝，不许静默归一化")
    if not weights:
        raise ValueError("权重表是空的")

    excluded: list[tuple[str, str]] = []
    avail: dict = {}
    for k, w in weights.items():
        if k not in scores or scores[k] is None:
            excluded.append((k, "该指标本次没有分数（数据缺失）"))
            continue
        avail[k] = w
    if not avail:
        raise ValueError("没有任何一项同时有权重和分数")

    tw = sum(avail.values())
    if abs(tw - 1.0) > 1e-12:
        # ★ 重归一化 —— 会被声明出去，不是静默的
        avail = {k: v / tw for k, v in avail.items()}
        excluded.append(("__renormalized__",
                         f"因缺少 {len(excluded)} 项，剩余 {len(avail)} 项权重已重归一化到 1"))
    return avail, excluded


def _weights_and_scores(weights: dict, scores: dict) -> tuple[float, dict]:
    """内部用：只吃**已经解析好**的权重（调用方负责声明剔除项）。"""
    keys = [k for k in weights if k in scores]
    if not keys:
        raise ValueError("没有任何一项同时有权重和分数")
    total_w = sum(weights[k] for k in keys)
    if abs(total_w - 1.0) > 1e-9:
        raise ValueError(f"权重和 = {total_w!r}，不为 1 —— 必须拒绝，不许静默归一化")
    contrib = {k: weights[k] * scores[k] for k in keys}
    return sum(contrib.values()), contrib


def base_score(weights: dict, scores: dict) -> float:
    """基准总分 S = Σ w_k·a_k。

    ★ 缺数据的项会被剔除并**重归一化**；要拿到剔除清单请用 `resolve_weights`。
    """
    avail, _ = resolve_weights(weights, scores)
    s, _ = _weights_and_scores(avail, scores)
    return s


def stability_interval(weights: dict, scores: dict, key: str,
                       delta: float) -> Stability:
    """★ **闭式解**：把 `key` 的权重掰到 t，其余按比例重归一化，
    求「标签与基准一致」的 t 区间。

    不需要枚举，不需要模拟 —— S(t) 是 t 的线性函数，解不等式即可。
    """
    wk = weights.get(key)
    if wk is None:
        raise KeyError(f"{key} 不在权重里")
    # ★ 先解析出"生效权重"（缺数据的项已被剔除并重归一化）
    w_eff, _excluded = resolve_weights(weights, scores)
    if key not in w_eff:
        raise ValueError(f"{key} 本次缺数据，它没有生效权重 —— 谈不上稳定性区间")
    wk_eff = w_eff[key]
    others = {k: v for k, v in w_eff.items() if k != key}
    denom = 1.0 - wk_eff
    if denom <= 1e-12:
        # 该项权重已经是 1，掰它就是把别人全清零 —— 退化，S(t) 就是 a_k
        a_k = scores.get(key)
        return Stability(key, 0.0, 1.0, 0.0, float(a_k if a_k is not None else 0.0),
                         label_of(float(a_k or 0.0), delta), degenerate=True)

    b_k = sum(w_eff[k] * scores[k] for k in others)
    beta = b_k / denom
    a_k = scores.get(key, 0.0)
    alpha = a_k - beta

    s0, _ = _weights_and_scores(w_eff, scores)
    base = label_of(s0, delta)

    lo, hi = _solve_interval(alpha, beta, delta, base)
    return Stability(key, lo, hi, alpha, beta, base,
                     degenerate=(hi <= lo + 1e-15))


def _solve_interval(alpha: float, beta: float, delta: float,
                    label: str) -> tuple[float, float]:
    """解 { t ∈ [0,1] : label(αt+β) == label }，返回 (lo, hi)。

    三种标签各自对应一段 t 集合；取与 [0,1] 的交集。
    """
    lo, hi = 0.0, 1.0

    def clip(a: float, b: float) -> tuple[float, float]:
        return max(0.0, a), min(1.0, b)

    if abs(alpha) < 1e-15:
        # S(t) 恒等于 beta，与 t 无关
        return (0.0, 1.0) if label_of(beta, delta) == label else (0.0, 0.0)

    if label == "看多":
        # αt + β ≥ δ
        t = (delta - beta) / alpha
        lo, hi = clip(t, 1.0) if alpha > 0 else clip(0.0, t)
    elif label == "看空":
        # αt + β ≤ −δ
        t = (-delta - beta) / alpha
        lo, hi = clip(0.0, t) if alpha > 0 else clip(t, 1.0)
    else:
        # 观望：−δ < αt + β < δ  →  两条不等式取交集
        t1 = (-delta - beta) / alpha
        t2 = (delta - beta) / alpha
        a, b = (t1, t2) if t1 <= t2 else (t2, t1)
        lo, hi = clip(a, b)
    return lo, hi


def brute_force_interval(weights: dict, scores: dict, key: str, delta: float,
                         step: float = 1e-4) -> tuple[float, float]:
    """**暴力扫描**（只用来交叉验证闭式解；产品路径不用它）。

    按 §9.1 的要求：闭式解 vs 暴力扫描，区间端点误差应 < 1e-6。
    实际用 step 采样，端点精度就是 step，所以断言时按 step 放容差。
    """
    w_eff, _ex = resolve_weights(weights, scores)
    base = label_of(base_score(weights, scores), delta)
    wk = w_eff[key]
    hits = []
    n = int(round(1.0 / step))
    for i in range(n + 1):
        t = i * step
        w2 = dict(w_eff)
        w2[key] = t
        for k in w_eff:
            if k != key:
                w2[k] = w_eff[k] * (1 - t) / (1 - wk) if (1 - wk) > 1e-12 else 0.0
        s = sum(w2[k] * scores[k] for k in w2 if k in scores)
        if label_of(s, delta) == base:
            hits.append(t)
    if not hits:
        return 0.0, 0.0
    return min(hits), max(hits)


def flip_rate(weights: dict, scores: dict, delta: float, M: int = 2000,
              kappa: float = 20.0, seed: int = 20261013) -> float:
    """**同时扰动**多个权重，统计标签翻转比例（蒙特卡洛）。

    从 Dirichlet(κ·w) 抽 M 组权重（κ 越大扰动越小），重算标签，
    统计与基准标签不一致的比例。用来量「同时动很多个权重」的脆弱度。

    ★ 用固定 seed —— 同一个输入必须给同一个结果（可复现）。
    """
    w_eff, _ex = resolve_weights(weights, scores)
    keys = list(w_eff)
    base = label_of(base_score(weights, scores), delta)
    rng = random.Random(seed)
    alpha = [max(1e-9, kappa * w_eff[k]) for k in keys]
    flips = 0
    for _ in range(M):
        g = [rng.gammavariate(a, 1.0) for a in alpha]
        tot = sum(g)
        if tot <= 0:
            continue
        w2 = {k: gi / tot for k, gi in zip(keys, g)}
        s = sum(w2[k] * scores[k] for k in keys)
        if label_of(s, delta) != base:
            flips += 1
    return flips / M if M else 0.0


def delta_scan(weights: dict, scores: dict,
               grid: tuple = DELTA_GRID) -> dict:
    """δ 扫描：δ 是**我们定的策略常数，不是实证结论**，所以必须扫。

    返回 {δ: 标签}。若标签在网格内变过 → 调用方必须打「阈值敏感」并降级。
    """
    s = base_score(weights, scores)
    return {d: label_of(s, d) for d in grid}
