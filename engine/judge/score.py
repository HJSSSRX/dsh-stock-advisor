# -*- coding: utf-8 -*-
"""确定性方向打分器 —— **方向绝不由模型拍脑袋**。

═══════════════════════════════════════════════════════════════════════════
出处：`设计/判断层-确定性打分-调研.md` §六（照着就能写 Python 的部分）
═══════════════════════════════════════════════════════════════════════════
选型：**加权和打分（WSM）+ 前置硬过滤 + 后置敏感性自检**。

❌ 明确不采用：规则引擎（数值比较不是符号推理）、专家系统（置信因子没有频率意义）、
   AHP（不要把人的判断包装成客观方法）、机器学习（与确定性定位冲突，且无可信标签）。

═══════════════════════════════════════════════════════════════════════════
★★★ 这套设计的命门（§6.2 原文）
═══════════════════════════════════════════════════════════════════════════
    方向符号 s_k **来自文献，不来自我们的数据**。

这样「哪些方向是对的」就不是我们拟合出来的，而是**引用来的** ——
这是把数据窥探压到最小的最强一招。

配套的强制机制：`WeightSpec` **构造时**校验 provenance 是否诚实，
标了 `published` 却没有 DOI、标了 `estimated` 却没有试验次数 → 直接抛错。
**不是"文档里写一句"，是代码构造不出来。**

═══════════════════════════════════════════════════════════════════════════
⛔ 本模块与现有 ask.py 的关系（重要）
═══════════════════════════════════════════════════════════════════════════
ask.py 里现在跑的那 5 条规则（净利同比 / 价格vsMA60 / 回撤 / PE分位 / 两融）
**违反本文件所依据的设计**：
  · 设计 §6.1 F 明写「技术指标（MACD/KDJ/RSI/BOLL）不进打分」
    —— 而 MA60 与回撤就是技术指标，它们现在直接产生方向分；
  · 设计的权重必须登记 provenance —— 现在那 5 条是我手定的，零登记；
  · 设计的置信度来自台账 Wilson 下界 —— 现在是"规则票数换算"。

**本模块是替代方案**，不是补充。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import sensitivity as SN
from .sensitivity import DELTA_GRID, FLIP_RATE_MAX, STABILITY_WIDTH_MIN


# ══════════════════════════════════════════════════════════════════
# 一、权重来源：四级 + 构造即校验
# ══════════════════════════════════════════════════════════════════
class Provenance(str, Enum):
    PUBLISHED = "published_paper"        # L1 权重/构造规则来自已发表文献
    ESTIMATED = "estimated_from_data"    # L2 在自己数据上估出来的
    EXPERT = "expert_judgment"           # L3 人手定的（必须承认）
    EQUAL = "equal_weight"               # L4 等权（有实证支持的默认）


class ProvenanceError(ValueError):
    """权重来源说不清楚 → 打分器**构造不出来**。"""


@dataclass(frozen=True)
class WeightSpec:
    """一项指标的权重与其**来源声明**。来源说不清就不给构造。"""
    key: str
    weight: float
    sign: int                    # +1 / −1，**来自文献，不许从数据里挑**
    provenance: Provenance
    description: str = ""
    citation: str = ""           # DOI / URL —— PUBLISHED 必填
    validated_on: str = ""       # "中国A股 1995-2016" / "美股 1976-1996" —— PUBLISHED 必填
    trials: Optional[int] = None # ESTIMATED 必填
    oos_result: Optional[float] = None   # ESTIMATED 必填
    stability_interval: Optional[tuple[float, float]] = None  # EXPERT 必填
    caveat: str = ""             # ⚠️ 该来源的已知局限，会打进输出


def assert_provenance_honest(specs: list) -> None:
    """★ 构造时校验：**标了就要拿得出证据**。

    这是把"人手定的权重包装成实证权重"这件事，从"纪律问题"变成"构造不出来"。
    """
    if not specs:
        raise ProvenanceError("权重表是空的")
    for s in specs:
        if s.provenance is Provenance.PUBLISHED:
            if not s.citation:
                raise ProvenanceError(f"{s.key}: 标了 published 却没有引用（DOI/URL）")
            if not s.validated_on:
                raise ProvenanceError(f"{s.key}: 标了 published 却没写验证范围（哪个市场、哪段区间）")
        elif s.provenance is Provenance.ESTIMATED:
            if s.trials is None or s.oos_result is None:
                raise ProvenanceError(f"{s.key}: 标了 estimated 却无试验次数 / 样本外结果")
        elif s.provenance is Provenance.EXPERT:
            if s.stability_interval is None:
                raise ProvenanceError(f"{s.key}: 人手定的权重必须附稳定性区间")
        if s.sign not in (+1, -1):
            raise ProvenanceError(f"{s.key}: 符号必须是 +1 或 −1")
    total = sum(s.weight for s in specs)
    if abs(total - 1.0) > 1e-9:
        raise ProvenanceError(f"权重和 = {total!r}，不为 1")


# ══════════════════════════════════════════════════════════════════
# 二、默认权重表
#     选等权不是偷懒 —— DeMiguel, Garlappi & Uppal (2009) 的结论是
#     等权在样本外**打败**估出来的权重，除非后者有远超所需的样本量。
#     我们没有那个样本量，所以等权是**唯一有强实证支持的选择**，
#     而且它让"权重是拟合出来的"这个问题**整体不存在**。
# ══════════════════════════════════════════════════════════════════
_C_DE = "DOI 10.1093/rfs/hhm075"          # 等权 vs 估权重
_C_LSY = "DOI 10.1016/j.jfineco.2019.03.008"   # 中国因子（价值=EP，剔除最小 30%）
_C_JAN = "DOI 10.1016/j.pacfin.2021.101607"    # A 股 32 个异象
_C_PIO = "DOI 10.2307/2672906"            # Piotroski F-score

_MARK = "⚠️ 用途见 caveat"
N_INDICATORS = 8


def default_manifest() -> list:
    """默认权重表 —— **8 项等权**（每项 1/8）。

    ★ 等权的出处是 DeMiguel et al. (2009)；每一项各自的**构造与符号**
      另有出处（见每条 citation）。两者分开声明，不混为一谈。
    """
    w = 1.0 / N_INDICATORS
    E = Provenance.EQUAL
    P = Provenance.PUBLISHED
    return [
        WeightSpec("EP", w, +1, E, "归母净利润 TTM / 总市值",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009（多市场）",
                   caveat=f"价值因子构造与符号见 Liu-Stambaugh-Yuan 2019 {_C_LSY}（中国 A 股 1995-2016）"),
        WeightSpec("BP", w, +1, E, "归母净资产 / 总市值",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"EP 已涵盖 BM；单独再用一次有重复计价风险。{_C_LSY}"),
        WeightSpec("IVOL", w, -1, E, "过去 60 日对基准回归的残差波动率",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"低波异象；风险类在 A 股存在，见 Jansen 2021 {_C_JAN}"),
        WeightSpec("TURN20", w, -1, E, "过去 20 日平均换手率",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"trading 类；见 Jansen 2021 {_C_JAN}"),
        WeightSpec("ILLIQ", w, +1, E, "Amihud 非流动性（|收益|/成交额 的均值）",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"trading 类；见 Jansen 2021 {_C_JAN}"),
        WeightSpec("REV20", w, -1, E, "过去 20 日累计收益",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"反转是**仅有的两类有样本外证据**之一；见 Jansen 2021 {_C_JAN}"),
        WeightSpec("RESMOM", w, +1, E, "残差动量（剔除因子暴露后的 12-1 动量）",
                   citation=_C_DE, validated_on="等权本身见 DeMiguel 2009",
                   caveat=f"残差动量同样有样本外证据；见 Jansen 2021 {_C_JAN}"),
        WeightSpec("FSCORE", w, +1, P, "Piotroski 9 项 0/1 合计（0-9）",
                   citation=_C_PIO, validated_on="美股 1976-1996",
                   caveat="⚠️⚠️ **仅美股 1976-1996，A 股未验证** —— 本项权重虽等权，"
                          "但其**符号方向**的 A 股适用性没有依据；"
                          "且 Jansen 2021 明确指出 quality 类在 A 股证据明显更弱"),
    ]


# ══════════════════════════════════════════════════════════════════
# 三、归一化：行业内 + 规模档内的横截面百分位（**无参数，抗极值**）
# ══════════════════════════════════════════════════════════════════
def cross_section_percentile(value: float, peers: list) -> Optional[float]:
    """在一组同类（同行业、同规模档）的数值里，value 的百分位 ∈ (0,1)。

    ★ 用百分位而不是 z-score：**无参数、抗极值**（§6.2 第 1 步的原话）。
      A 股财报有大量离群值（一次性损益、ST 公司），z-score 会被拽飞。

    返回 None 表示**同类样本不足**，调用方必须按"该指标缺数据"处理，
    **不许当成 0 或 0.5**（那会凭空产生一个方向分）。
    """
    if value is None or not peers:
        return None
    vals = [p for p in peers if p is not None]
    n = len(vals)
    if n < MIN_PEERS:
        return None
    below = sum(1 for p in vals if p < value)
    equal = sum(1 for p in vals if p == value)
    # 中位秩法（处理并列）：百分位落在 (0,1) 开区间
    pct = (below + 0.5 * equal) / n
    return min(1.0 - 1e-9, max(1e-9, pct))


MIN_PEERS = 20      # 同类样本少于这个数就不给百分位（宁可不给，不给假的）


def to_z(pct: Optional[float]) -> Optional[float]:
    """百分位 → 归一分 z ∈ [-1, +1]。"""
    if pct is None:
        return None
    return 2.0 * pct - 1.0


# ══════════════════════════════════════════════════════════════════
# 四、硬过滤（不参与打分，**命中即出局**，且不许静默吞掉）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class HardFilter:
    id: str
    why: str
    citation: str = ""


HARD_FILTERS = (
    HardFilter("DELIST_RISK", "ST / *ST / 退市整理期",
               "交易所风险警示名单（S0 级事实，可直接断言）"),
    HardFilter("LIMIT_BOARD", "涨跌停 / 一字板 / T 字板",
               "compute/rules.py：主板 10%、双创 20%、北交所 30%、ST 5%，价格须先复权对齐"),
    HardFilter("NEW_LISTING", "上市不足 250 个交易日", "数据可得性：算不出 250 日回撤"),
    HardFilter("SUSPENDED", "当前停牌", "数据可得性"),
    HardFilter("PIT_UNAVAILABLE", "财报未披露（时点不可得）",
               "compute/pit.py：不许把分析日之后才披露的财报当已知"),
    HardFilter("TINY_CAP", "市值落在**最小 30%**内",
               "Liu-Stambaugh-Yuan 2019：这批公司是借壳标的，不是正常定价对象"),
)


# ══════════════════════════════════════════════════════════════════
# 五、打分结果
# ══════════════════════════════════════════════════════════════════
@dataclass
class DirectionResult:
    code: str
    as_of: str
    direction: str                       # 看多 / 看空 / 观望
    score: float                         # ∈ [-1, +1]
    delta: float                         # 本次用的死区
    contributions: dict = field(default_factory=dict)   # w_k·a_k，Σ == score（可归因）
    confidence: Optional[float] = None   # None = 样本不足
    confidence_source: str = "insufficient_samples"
    flags: dict = field(default_factory=dict)
    excluded: list = field(default_factory=list)        # 被剔除的项及原因（不许静默吞掉）
    stability: dict = field(default_factory=dict)
    flip_rate: float = 0.0
    delta_scan: dict = field(default_factory=dict)
    spec_hash: str = ""
    trials: int = 0
    manifest: list = field(default_factory=list)
    raw_direction: str = ""              # 降级前的方向（保留，便于审计）

    def render(self) -> str:
        """★ 把权重来源**逐字打进输出**（不是藏在日志里）。"""
        L = [f"方向：{self.direction}"
             + (f"（自报 {self.raw_direction}，因自检降级）" if self.raw_direction
                and self.raw_direction != self.direction else "")]
        L.append(f"打分 S = {self.score:+.4f} ｜ 死区 δ = {self.delta:.2f}"
                 f" ｜ 规格哈希 {self.spec_hash[:12]}")
        if self.confidence is None:
            L.append(f"置信度：**样本不足，不给数字**（{self.confidence_source}）")
        else:
            L.append(f"置信度：{self.confidence:.1%}（来源 {self.confidence_source}）")
        n_pub = sum(1 for s in self.manifest if s.provenance is Provenance.PUBLISHED)
        n_eq = sum(1 for s in self.manifest if s.provenance is Provenance.EQUAL)
        n_exp = sum(1 for s in self.manifest if s.provenance is Provenance.EXPERT)
        n_est = sum(1 for s in self.manifest if s.provenance is Provenance.ESTIMATED)
        L.append(f"权重来源：{len(self.manifest)} 项中 "
                 f"{n_pub} 项 published_paper、{n_exp} 项 expert_judgment、"
                 f"{n_est} 项 estimated_from_data、{n_eq} 项 equal_weight")
        for s in self.manifest:
            cite = f"  {s.citation}" if s.citation else ""
            vo = f"（{s.validated_on}）" if s.validated_on else ""
            L.append(f"  · {s.key:8s} s={s.sign:+d}  w={s.weight:.4f}  "
                     f"{s.provenance.value}{cite}{vo}")
            if s.caveat:
                L.append(f"      {s.caveat}")
        L.append("  · 技术指标（MA/MACD/KDJ/RSI/BOLL）**不进打分** ——"
                 "未取得可信证据，只用于位置描述")
        if self.contributions:
            top = sorted(self.contributions.items(), key=lambda kv: -abs(kv[1]))[:4]
            L.append("逐项贡献（可归因）：" + "；".join(f"{k} {v:+.4f}" for k, v in top))
        for k, v in self.flags.items():
            L.append(f"⚠️ {k}：{v}")
        if self.excluded:
            L.append("被剔除的项（**不静默吞掉**）：")
            for k, why in self.excluded:
                L.append(f"  · {k}：{why}")
        return "\n".join(L)


def spec_hash(manifest: list, delta: float, filters: tuple) -> str:
    """★ 规格哈希 —— 把 (指标集合, 符号, 权重, δ, 全体过滤) 序列化后取 SHA-256。

    **改任何一项 = 规格变了**，`trials` 必须 +1（§四 工程硬约束）。
    这是防"看一眼再调一版"的机械手段。
    """
    payload = {
        "indicators": sorted([(s.key, s.sign, round(s.weight, 10), s.provenance.value)
                              for s in manifest]),
        "delta": round(delta, 10),
        "filters": sorted(f.id for f in filters),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ══════════════════════════════════════════════════════════════════
# 六、主流程
# ══════════════════════════════════════════════════════════════════
def decide(code: str, as_of: str, scores: dict, *,
           manifest: Optional[list] = None,
           delta: float = 0.20,
           blocked: Optional[list] = None,
           trials: int = 0,
           do_selfcheck: bool = True) -> DirectionResult:
    """从「各指标的归一分」到「方向」。**全程确定性，零模型调用。**

    scores: {指标 key: 归一分 z ∈ [-1,+1]}。缺的项直接不传。
    blocked: [(过滤 id, 原因)] —— 命中的硬过滤。
    """
    manifest = manifest if manifest is not None else default_manifest()
    assert_provenance_honest(manifest)          # ★ 构造即校验

    weights = {s.key: s.weight for s in manifest}
    signs = {s.key: s.sign for s in manifest}
    a = {k: signs[k] * v for k, v in scores.items() if k in signs and v is not None}

    # ── 硬过滤：命中即出局，**且必须被声明** ──
    excluded: list = []
    for fid, why in (blocked or []):
        excluded.append((fid, why))
    res = DirectionResult(code=code, as_of=as_of, direction="观望",
                          score=0.0, delta=delta, excluded=excluded,
                          manifest=list(manifest), trials=trials)

    if excluded:
        res.flags["硬过滤"] = "命中了前置硬过滤，**不给方向**：" + "、".join(k for k, _ in excluded)
        res.flags["_blocked"] = "1"
        res.spec_hash = spec_hash(manifest, delta, HARD_FILTERS)
        return res

    if not a:
        res.flags["依据不足"] = "一个可打分的指标都没有"
        res.spec_hash = spec_hash(manifest, delta, HARD_FILTERS)
        return res

    # ── 打分 ──
    w_eff, renormal_note = SN.resolve_weights(weights, a)
    res.excluded.extend(renormal_note)
    contrib = {k: w_eff[k] * a[k] for k in w_eff}
    S = sum(contrib.values())
    res.score = S
    res.contributions = contrib
    res.raw_direction = SN.label_of(S, delta)
    res.direction = res.raw_direction

    # ── 后置自检：三项，任一触发即降级 ──
    sens_note = ""
    if do_selfcheck and len(w_eff) >= 2:
        worst = None
        for k in w_eff:
            st = SN.stability_interval(weights, a, k, delta)
            res.stability[k] = (round(st.lo, 6), round(st.hi, 6))
            if worst is None or st.width < worst[1].width:
                worst = (k, st)
        if worst is not None:
            wmin = worst[1].width
            sens_note = f"最敏感项 {worst[0]}（稳定区间宽 {wmin:.4f}）"
            if wmin < STABILITY_WIDTH_MIN:
                res.flags["权重敏感"] = (f"{worst[0]} 的稳定区间宽仅 {wmin:.4f}"
                                        f" < {STABILITY_WIDTH_MIN} —— 轻微动权重标签就变")
        fr = SN.flip_rate(weights, a, delta, M=2000)
        res.flip_rate = fr
        if fr > FLIP_RATE_MAX:
            res.flags["权重敏感"] = (res.flags.get("权重敏感", "")
                                    + f"；同时扰动 2000 次，{fr:.1%} 会翻标签").strip("；")
        scan = SN.delta_scan(weights, a)
        res.delta_scan = scan
        if len(set(scan.values())) > 1:
            res.flags["阈值敏感"] = (f"δ 在 {list(DELTA_GRID)} 之间扫描，标签发生变化："
                                    f"{[f'{d:.2f}→{v}' for d, v in scan.items()]}")

    if any(k in res.flags for k in ("权重敏感", "阈值敏感")):
        res.direction = "观望"
        if sens_note:
            res.flags["_sens"] = sens_note

    res.spec_hash = spec_hash(manifest, delta, HARD_FILTERS)
    return res
