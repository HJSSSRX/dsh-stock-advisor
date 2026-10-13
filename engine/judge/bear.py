# -*- coding: utf-8 -*-
"""反方观点 —— **不问模型"给个看空理由"，而是问"证据里有没有看空触发条件"。**

═══════════════════════════════════════════════════════════════════
★ 开工前调研推翻了我们原来的两个假设
═══════════════════════════════════════════════════════════════════
调研（8 主题并行，含 GitHub 排行榜 / 学术 / 官方规则 / 本地竞品源码）：

**假设一：「TradingAgents 的多空辩论是机制化的」—— 不成立。**
  读了本地 fork 的完整源码：`bear_researcher.py` **全文 68 行**，
  以 `"You are a Bear Analyst"` 开头、`"Refute the bull"` 结尾，
  调一次 `llm.invoke(prompt)` 就完事 —— **没有任何证据绑定、不要求引用号、没有事后核验**。
  判定"谁赢"的是一次 LLM 调用。→ **照抄它 = 照抄我们要解决的问题。**

**假设二：「让模型生成反方，然后检查它引没引证据」—— 不够。**
  调研结论原话：
  > 不要问模型「请给个看空理由」，而要问「证据里有没有看空触发条件」。

  A 股的看空理由有大量是**法定的、阈值明确的**：
  证监会令第 224 号《上市公司股东减持股份管理暂行办法》、
  上交所/深交所退市新规的财务类与交易类指标。
  **这些是法条，不是民间规则** —— 于是"有没有反方"变成了
  一个**关于证据的可验证命题**，而不是需要被信任的自述。

═══════════════════════════════════════════════════════════════════
★★ 三态，不是两态（这条写错后果最严重）
═══════════════════════════════════════════════════════════════════
    HIT      命中：证据齐、算过、确实触发
    CLEAR    没命中：**证据齐**、算过、确实没触发
    UNKNOWN  **所需数据没取到** → 绝不能算 CLEAR

把 UNKNOWN 混进 CLEAR，就等于**把"没取到数据"谎报成"没有看空理由"** ——
这正是本项目血泪清单里最怕的那种静默失败。

所以输出永远是「在已扫描的 N 条里无命中，覆盖 K/N」，
**绝不说「无风险」「没有看空理由」**。

═══════════════════════════════════════════════════════════════════
⚠️ 本模块的天花板（如实写在这里，也写进输出）
═══════════════════════════════════════════════════════════════════
法定触发器**只能覆盖有明确法规阈值的看空理由**，覆盖不了基本面质量判断
（竞争格局恶化、技术替代、需求周期下行）。
所以「清单内无命中」**不等于**「无风险」。这个局限是设计的天花板，不是实现缺陷。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

HIT, CLEAR, UNKNOWN = "HIT", "CLEAR", "UNKNOWN"

# 标记名（写死一处，免得调用方各自拼字符串）
F_BEAR_UNKNOWN = "反方覆盖不足"
F_BEAR_UNVERIFIED = "反方引用不可核验"


def _dec(x: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════
# 触发器
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Trigger:
    """一条看空触发条件。

    ★★ `basis` 必须诚实标注它到底是哪一种 —— 这是本模块最容易被批评的地方：
      **把"我们设计的经验阈值"包装成"法条阈值"，就是给民间规则借法定权威。**
      独立验证实测指出：8 条里只有 4 条有法条出处，而输出把它们统称"法定"。

        LAW        有明确法规出处（条号可查）
        HEURISTIC  纯技术/经验阈值（**没有法规依据**，不许叫"法定"）

    `need` 是它需要的数据 kind —— **数据没取到就是 UNKNOWN，不是 CLEAR**。
    `uses` 是它实际读的事实标签 —— 命中时**只引这几条的依据**。
    `check` 是纯函数：吃 label→Fact 的字典，返回命中说明或 None。
    """
    id: str
    law: str
    need: tuple[str, ...]
    uses: tuple[str, ...]
    check: Callable[[dict], Optional[str]]
    note: str = ""
    basis: str = "LAW"


@dataclass(frozen=True)
class BearHit:
    trigger_id: str
    law: str
    why: str
    eids: tuple[str, ...] = ()
    basis: str = "LAW"          # ★ LAW / HEURISTIC —— 输出时必须分开标


def _v(facts: dict, label: str) -> Optional[Decimal]:
    f = facts.get(label)
    return _dec(f.value) if f is not None else None


def _eid(facts: dict, *labels: str) -> tuple[str, ...]:
    return tuple(facts[l].eid for l in labels if l in facts and facts[l].eid)


# ── 退市风险（财务类）────────────────────────────────────────────
def _t_fin_loss(facts: dict) -> Optional[str]:
    v = _v(facts, "归属于母公司所有者的净利润")
    if v is None:
        return None
    if v < 0:
        return (f"归母净利润为负（{v}）—— **财务类退市指标的一项近似**"
                "（法规要求的是「利润总额/净利润/扣非净利润**三者孰低**为负」，"
                "我们只有归母净利润这一个数，所以只是提示，**不构成退市判定**）")
    return None


def _t_fin_revenue(facts: dict) -> Optional[str]:
    """★ 现行法规是**合取条件**：利润孰低为负 **且** 扣除后营收 < 3 亿元。

    独立验证实测指出：第一版只单看「营收 < 1 亿」，
    于是「盈利 2 亿 + 营收 9000 万」被报成命中退市指标 —— **误报**。
    这里改成近似合取（用归母净利润代替"三者孰低"），并把局限写清楚。
    """
    rev = _v(facts, "营业收入")
    npf = _v(facts, "归属于母公司所有者的净利润")
    if rev is None:
        return None
    if rev < Decimal("300000000"):          # 现行门槛 3 亿（不是旧规的 1 亿）
        if npf is None:
            return None
        if npf < 0:
            return (f"扣除后营业收入 {rev} 元（< 3 亿）**且**归母净利润为负 ——"
                    " 同时满足财务类退市指标的两个条件（近似）")
        return None
    return None


def _t_mktcap(facts: dict) -> Optional[str]:
    v = _v(facts, "总市值")
    if v is None:
        return None
    if v < Decimal("5"):                    # ★ A 股门槛是 5 亿；3 亿是**仅发行 B 股**的公司
        return (f"总市值 {v} 亿元，低于 5 亿元（A 股门槛；B 股门槛才是 3 亿）"
                "—— 连续 20 个交易日则触及交易类退市")
    return None


# ── 交易类信号 ───────────────────────────────────────────────────
def _t_below_pb1(facts: dict) -> Optional[str]:
    v = _v(facts, "市净率")
    if v is None:
        return None
    if v < Decimal("1"):
        return f"市净率 {v} < 1（破净）"
    return None


def _t_drawdown(facts: dict) -> Optional[str]:
    v = _v(facts, "距250日高点回撤%")
    if v is None:
        return None
    if v >= Decimal("20"):
        return f"距 250 日高点回撤 {v}%"
    return None


def _t_below_ma60(facts: dict) -> Optional[str]:
    # ★ 读的是**收盘价**（日线口径），不是快照的「最新价」——
    #   第一版读错了，而 `uses` 声明的是「收盘价」，两者不一致 →
    #   命中被判成 CLEAR（静默漏检）。被 selftest 的负对照逮住。
    px, ma = _v(facts, "收盘价"), _v(facts, "MA60")
    if px is None or ma is None:
        return None
    if px < ma:
        return f"收盘价 {px} 在 60 日均线 {ma} 下方"
    return None


def _t_near_limit_down(facts: dict) -> Optional[str]:
    px, ld = _v(facts, "最新价"), _v(facts, "跌停价")
    if px is None or ld is None or ld == 0:
        return None
    if px <= ld * Decimal("1.01"):
        return f"现价 {px} 贴近跌停价 {ld}"
    return None


def _t_st(facts: dict) -> Optional[str]:
    """ST 名单：**交易所判定的事实**，不需要计算。"""
    f = facts.get("ST状态")
    if f is None:
        return None
    if "是" in str(f.value) or "ST" in str(f.value):
        return f"在交易所风险警示名单中（{f.value}）"
    return None


TRIGGERS: tuple[Trigger, ...] = (
    Trigger("DELIST-FIN-LOSS",
            "深交所/上交所股票上市规则·财务类：利润总额、净利润、扣非净利润**三者孰低为负**",
            ("financial_sina",), ("归属于母公司所有者的净利润",), _t_fin_loss,
            "本实现只有归母净利润，**不是三者孰低** —— 与法条有偏差，只是提示",
            basis="LAW"),
    Trigger("DELIST-FIN-REV",
            "同上·财务类：**扣除后营业收入低于 3 亿元**（须与「孰低为负」同时成立）",
            ("financial_sina",), ("营业收入", "归属于母公司所有者的净利润"), _t_fin_revenue,
            "用归母净利润近似「三者孰低」，且用的是「营业收入」口径——与法条有偏差",
            basis="LAW"),
    Trigger("DELIST-MKT-CAP",
            "同上·交易类：连续 20 个交易日收盘总市值低于 **5 亿元**（A 股；B 股才是 3 亿）",
            ("quote",), ("总市值",), _t_mktcap,
            "本实现只看**当前**市值，**没有验证是否连续 20 个交易日** —— 会误报",
            basis="LAW"),
    Trigger("ST-TAG", "交易所风险警示名单（S0 级、可直接断言的事实）",
            ("st_list",), ("ST状态",), _t_st, basis="LAW"),
    # ── 以下 4 条**没有法规依据**，是纯技术/经验阈值 ──
    #    ★ 独立验证实测指出：它们曾被和上面 4 条一起输出为「法定触发器」，
    #      而茅台/ETF/指数的实际命中主要来自这几条 —— 这是给经验阈值借法定权威。
    Trigger("PB-BELOW-1", "破净：市净率 < 1（**经验阈值，无法规依据**）",
            ("quote",), ("市净率",), _t_below_pb1, basis="HEURISTIC"),
    Trigger("DRAWDOWN-20", "距 250 日高点回撤 ≥ 20%（**经验阈值，无法规依据**）",
            ("kline",), ("距250日高点回撤%",), _t_drawdown, basis="HEURISTIC"),
    Trigger("BELOW-MA60", "价格在 60 日均线下方（**技术指标，无法规依据**）",
            ("kline",), ("收盘价", "MA60"), _t_below_ma60, basis="HEURISTIC"),
    Trigger("NEAR-LIMIT-DOWN", "现价贴近跌停价（**经验阈值，无法规依据**）",
            ("quote",), ("最新价", "跌停价"), _t_near_limit_down, basis="HEURISTIC"),
)


@dataclass
class BearScan:
    hits: list[BearHit] = field(default_factory=list)
    coverage: dict[str, str] = field(default_factory=dict)
    n_scanned: int = 0
    n_unknown: int = 0
    unknown_kinds: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.n_unknown == 0

    def report(self) -> str:
        """★ 措辞纪律：有 UNKNOWN 时**不许说「没有看空理由」**；
        ★★ 而且**不许把经验阈值说成「法定」**（独立验证点名的那条）。"""
        L = []
        law_hits = [h for h in self.hits if h.basis == "LAW"]
        heu_hits = [h for h in self.hits if h.basis != "LAW"]
        L.append(f"**看空触发条件**：命中 {len(self.hits)} 条"
                 f"（有法规依据 {len(law_hits)} 条 / 经验或技术阈值 {len(heu_hits)} 条）")
        for h in self.hits:
            tag = "法条" if h.basis == "LAW" else "经验阈值·无法规依据"
            L.append(f"  - [{h.trigger_id}][{tag}] {h.why}")
            L.append(f"      依据：{h.law}")
            if h.eids:
                L.append(f"      证据：{'、'.join(h.eids)}")
        if not self.hits:
            L.append(
                f"  在已扫描的 {self.n_scanned} 条触发器中**无命中**"
                f"（覆盖 {self.n_scanned - self.n_unknown}/{self.n_scanned}）")
        if self.n_unknown:
            L.append(
                f"⚠️ 其中 {self.n_unknown} 条**因为数据没取到而无法判定**"
                f"（缺：{'、'.join(self.unknown_kinds)}）"
                " —— **这不等于「没有风险」**，只能说「没查到」。")
        L.append("⚠️ 其中**没有法规依据**的那几条是纯技术/经验阈值 —— "
                 "**不许当成「法条说它有问题」**。（本项目禁止把民间规则当信号。）")
        L.append("⚠️ 触发器只能覆盖有明确阈值的理由，**覆盖不了基本面质量判断**"
                 "（竞争格局、技术替代、需求周期）。「清单内无命中」≠「无风险」。")
        return "\n".join(L)


def scan(facts_by_label: dict, kinds_present: set[str]) -> BearScan:
    """扫一遍法定触发器。**数据没取到 → UNKNOWN，绝不降级成 CLEAR。**"""
    sc = BearScan()
    for t in TRIGGERS:
        sc.n_scanned += 1
        missing = [k for k in t.need if k not in kinds_present]
        if missing:
            sc.coverage[t.id] = UNKNOWN
            sc.n_unknown += 1
            for k in missing:
                if k not in sc.unknown_kinds:
                    sc.unknown_kinds.append(k)
            continue
        try:
            why = t.check(facts_by_label)
        except Exception:                                   # noqa: BLE001
            sc.coverage[t.id] = UNKNOWN
            sc.n_unknown += 1
            if f"计算异常({t.id})" not in sc.unknown_kinds:
                sc.unknown_kinds.append(f"计算异常({t.id})")
            continue
        if why:
            sc.coverage[t.id] = HIT
            # ★ 只引**这条触发器实际读的那几条事实**的证据号，不把全部事实算成依据
            eids = tuple(facts_by_label[l].eid for l in t.uses
                         if l in facts_by_label and getattr(facts_by_label[l], "eid", ""))
            sc.hits.append(BearHit(t.id, t.law, why, eids, basis=t.basis))
        else:
            sc.coverage[t.id] = CLEAR
    return sc
