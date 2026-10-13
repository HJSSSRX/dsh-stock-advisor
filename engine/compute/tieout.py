# -*- coding: utf-8 -*-
"""三表勾稽核对 —— **只做"能证明自己没错"的那几条**。

═══════════════════════════════════════════════════════════════════════════
本模块的全部设计依据来自一次真实实测（不是想出来的）
═══════════════════════════════════════════════════════════════════════════
调研对 35 只 A 股（2025 年报，**全部标准无保留意见的干净公司**）逐条跑了误报率：

| 规则 | 误报率（实测） | 结论 |
|---|---|---|
| A1 资产 = 负债 + 所有者权益 | **0%**（35/35 闭合，相对残差 ≤ 1.98e-16） | ✅ **可判 FAIL** |
| A2 现金净增加额 = 经营+投资+筹资+汇率影响 | **0%**（34 只精确 0） | ✅ **可判 FAIL** |
| B2 间接法现金流量表勾稽 | **82.9%~97.1%**（容差 1e-6）；**5% 容差仍 20~40%** | ⛔ **绝不可判 FAIL** |
| A5 未分配利润滚动 | **≈93.3%**（朴素公式） | ⛔ **绝不可判 FAIL** |
| C1 收入-现金桥 | 32%（不分离价税）/ **20%**（完整桥） | ⛔ **绝不可判 FAIL** |

★★★ **这是本模块存在的全部理由**：

    被测样本是**干净公司**。所以"判不平"就等于**冤枉好人**。
    公式是对的（茅台一只能被年报原文第 115 页逐字核到残差恰好 0.00），
    问题在**容差与字段覆盖**。

    把高误报率的判据做成硬闸门 = 系统**批量生产假警报** ——
    这与本项目"先一定保证分析没有问题"**直接冲突**。

所以本模块把规则分成两类，并**在代码层拦死**：
    · `can_fail=True`  —— 只有实测误报率为 0 的才有资格
    · `can_fail=False` —— 想返回 FAIL 会被 `enforce()` 改成 WARN，并记明原因

═══════════════════════════════════════════════════════════════════════════
外部标准答案
═══════════════════════════════════════════════════════════════════════════
巨潮资讯网官方年报 PDF（本项目已有 `data/sources/cninfo.py` 通道）。
实测：贵州茅台 2025 年报第 115 页「补充资料」逐字相加 = 表内经营活动现金流量净额，
残差 0.00 —— 这是字段映射正确性的**唯一硬标准**。
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# 一、规则注册表
# ══════════════════════════════════════════════════════════════════
PASS, FAIL, UNKNOWN, INFO, WARN = "PASS", "FAIL", "UNKNOWN", "INFO", "WARN"

# ★★ 上线门槛：判据的**真误报率上界**必须 ≤ 这个数，才配判 FAIL。
#   出处：本项目自己定的规矩「误报率 > 5% 的判据禁止进 FAIL 闸门」。
FP_TARGET = 0.05


@dataclass(frozen=True)
class Rule:
    """一条勾稽规则。

    ★★ `can_fail` **不是**一个可以随手填的字段 —— 它是**算出来的**：

            can_fail = allow_fail  AND  gate_met
            gate_met = CP95上界(n_flagged, n_tested) ≤ FP_TARGET

    ★ 这一条是被独立证伪逼出来的。原实现直接写 `fp_rate_measured = 0.0`，
      证伪者指出：**35 只样本、0 次误报的 Clopper-Pearson 单侧 95% 上界是 8.20%**，
      高于 5% 的门槛 —— **那批数据连项目自己的门槛都没证过**。
      写「0%」等于把 8.20% 的未知伪装成了确定。

    → 修法不是降低标准，是**扩大样本**：实测 68 只有效样本、0 次误报 → 上界 4.31% ≤ 5%，达标。
    """
    id: str
    name: str
    citation: str                       # 判据的合法性来源（准则条款）
    n_tested: int                       # ★ 有效测试数（**不含**取数失败与 UNKNOWN）
    n_flagged: int                      # ★ 其中被判为异常的公司数
    allow_fail: bool                    # 作者意图：这条判据想不想判 FAIL
    sample_note: str                    # 样本说明（可复核）
    tolerance_source: str
    note: str = ""

    # ── 以下全部是算出来的 ──
    @property
    def fp_point(self) -> float:
        return self.n_flagged / self.n_tested if self.n_tested else 1.0

    @property
    def fp_upper(self) -> float:
        """真误报率的 **Clopper-Pearson 单侧 95% 上界**（精确法，不是近似）。"""
        from judge.ablation import cp_upper
        if not self.n_tested:
            return 1.0
        return cp_upper(self.n_flagged, self.n_tested) or 1.0

    @property
    def gate_met(self) -> bool:
        return self.fp_upper <= FP_TARGET

    @property
    def can_fail(self) -> bool:
        """★ **只有既想判、又证过门槛的，才配判 FAIL。**"""
        return self.allow_fail and self.gate_met

    def fp_text(self) -> str:
        return (f"实测 {self.n_flagged}/{self.n_tested}（点估计 {self.fp_point:.1%}，"
                f"**CP95 上界 {self.fp_upper:.2%}**，门槛 {FP_TARGET:.0%}："
                f"{'达标' if self.gate_met else '❌ 未达标'}）")


RULES: tuple[Rule, ...] = (
    Rule("A1", "资产 = 负债 + 所有者权益",
         "《企业会计准则第30号——财务报表列报》：资产负债表恒等关系",
         68, 0, True,
         "68 只有效样本 / 70 只 A 股跨行业（含银行·保险·券商·地产）/ 2025 年报 / 2026-10-13 实测",
         "实测 p100（相对残差最大 1.98e-16）",
         "同时用 TOTAL_LIAB_EQUITY 再核一次，两次都过才 PASS"),
    Rule("A2", "现金净增加额 = 经营 + 投资 + 筹资 + 汇率影响",
         "《企业会计准则第31号——现金流量表》",
         67, 0, True,
         "67 只有效样本（1 只因汇率字段缺失转 UNKNOWN、已剔除，不计入分母）",
         "实测 p100；千元取整尾差真实存在，故下界 1000 元",
         "★ 若 RATE_CHANGE_EFFECT 缺失 → 必须 UNKNOWN，不许 FAIL"),
    Rule("A3", "期末现金 = 期初现金 + 净增加额",
         "《企业会计准则第31号——现金流量表》",
         68, 0, True,
         "68 只有效样本（与 A1 同批）",
         "同 A1",
         "现金及现金等价物范围变更时属口径变更，应输出说明而非错误"),
    # ── 以下三条：实测误报率远超门槛，allow_fail 与 gate_met 都不成立 ──
    Rule("B2", "间接法现金流量表勾稽",
         "《企业会计准则第31号》第十六条（间接法调整项）",
         35, 29, False,
         "35 只 A 股 / 容差 1e-6 → 82.9%~97.1%；5% 容差仍 20~40%",
         "无 —— 残差来源是字段映射与列报口径差异，不是公司有问题",
         "⛔ 只输出未解释残差当提示。**把残差当造假证据 = 冤枉好公司**"),
    Rule("A5", "未分配利润滚动（期初 + 净利润 − 分红 = 期末）",
         "《企业会计准则第30号》所有者权益变动表",
         30, 28, False,
         "30 只 A 股 / 朴素公式 → 93.3% 超 0.5% 容差",
         "无 —— 「支付」是现金口径、「利润分配」是宣告口径，天生不等",
         "⛔ 且数据源**没有**所有者权益变动表（会企04表），只能走 PDF 解析"),
    Rule("C1", "收入-现金桥（销售收现 vs 营业收入）",
         "《企业会计准则第31号》应用指南：含增值税销项税额",
         25, 5, False,
         "25 只 A 股 / 完整桥 + 13% 价税分离 → 误报 20%",
         "无 —— 增值税率、票据背书、财务公司混入等都会造成差异",
         "⛔ 只输出桥与比值，**不许判异常**"),
)
RULE_BY_ID = {r.id: r for r in RULES}


class CannotFailError(ValueError):
    """★ 试图给一条「不具备 FAIL 资格」的规则返回 FAIL。"""


# ══════════════════════════════════════════════════════════════════
# 二、结果结构（统一返回契约）
# ══════════════════════════════════════════════════════════════════
@dataclass
class TieoutResult:
    rule_id: str
    verdict: str
    caliber: str = ""                    # 合并 / 母公司
    period: str = ""
    inputs: list = field(default_factory=list)     # [{field, value, eid}]
    residual: Optional[float] = None
    residual_rel: Optional[float] = None
    tolerance: Optional[float] = None
    tolerance_source: str = ""
    false_positive_rate_measured: Optional[float] = None
    false_positive_upper: Optional[float] = None      # ★ CP95 上界（真误报率）
    false_positive_sample: str = ""
    n_tested: int = 0
    n_flagged: int = 0
    gate_met: bool = False                            # ★ 是否证过 5% 门槛
    can_fail: bool = False
    suppressed_fail: bool = False         # ★ 曾想判 FAIL 但被降级
    note: str = ""

    def render(self) -> str:
        L = [f"[{self.rule_id}] {self.verdict}｜{self.period}｜{self.caliber}"]
        if self.residual is not None:
            L.append(f"    残差 {self.residual!r}" +
                     (f"（相对 {self.residual_rel:.3e}）" if self.residual_rel is not None else "") +
                     (f"｜容差 {self.tolerance:g}" if self.tolerance is not None else ""))
        if self.n_tested:
            gate = "达标" if self.gate_met else "**未达标**"
            L.append(f"    ★ 误报率：实测 {self.n_flagged}/{self.n_tested}"
                     f"（点估计 {self.false_positive_rate_measured:.1%}，"
                     f"**CP95 上界 {self.false_positive_upper:.2%}**，"
                     f"门槛 5% {gate}）")
            L.append(f"      样本：{self.false_positive_sample}")
        if self.suppressed_fail:
            L.append("    ⚠️ 本规则**不具备判 FAIL 的资格**（误报率过高），已降级为 WARN 并保留残差")
        if self.note:
            L.append(f"    {self.note}")
        return "\n".join(L)


def make(rule_id: str, verdict: str, **kw) -> TieoutResult:
    """★ 统一出口 —— `can_fail=False` 的规则在这里被拦死，绕不过去。"""
    r = RULE_BY_ID.get(rule_id)
    if r is None:
        raise KeyError(f"未注册的规则 {rule_id}")
    suppressed = False
    if verdict == FAIL and not r.can_fail:
        verdict = WARN
        suppressed = True
    return TieoutResult(
        rule_id=rule_id, verdict=verdict,
        false_positive_rate_measured=r.fp_point,
        false_positive_upper=r.fp_upper,
        false_positive_sample=r.sample_note,
        n_tested=r.n_tested, n_flagged=r.n_flagged,
        gate_met=r.gate_met,
        can_fail=r.can_fail, suppressed_fail=suppressed,
        tolerance_source=r.tolerance_source, **kw)


def enforce(res: TieoutResult) -> TieoutResult:
    """★ 二道防线：即使有人手工构造了 TieoutResult，也不许 FAIL 溜过去。"""
    r = RULE_BY_ID.get(res.rule_id)
    if r is None or r.can_fail:
        return res
    if res.verdict == FAIL:
        res.verdict = WARN
        res.suppressed_fail = True
    return res


# ══════════════════════════════════════════════════════════════════
# 三、取数：东财 F10 全表接口（实测可用）
# ══════════════════════════════════════════════════════════════════
F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://emweb.securities.eastmoney.com/"}


def _get(url: str, params: dict, timeout: int = 25) -> str:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=_H)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def company_type(code_sh: str) -> str:
    """取 hidctype：4=通用 3=银行 2=保险 1=证券。**决定调整项清单，必须先取。**"""
    try:
        html = _get("https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index",
                    {"type": "web", "code": code_sh.lower()})
        m = re.search(r'id="hidctype"[^>]*value="(\d)"', html)
        return m.group(1) if m else "4"
    except Exception:                                   # noqa: BLE001
        return "4"


def fetch_table(ep: str, code_sh: str, period: str, ctype: str = "4") -> dict:
    """取一张表。ep ∈ {zcfzbAjaxNew 资产负债表, lrbAjaxNew 利润表, xjllbAjaxNew 现金流量表}。

    返回该报告期的原始 dict（**不做任何加工** —— 加工会让残差无法归因）。
    """
    raw = _get(F10 + ep, {"companyType": ctype, "reportDateType": "0", "reportType": "1",
                          "dates": period, "code": code_sh.upper()})
    d = json.loads(raw)
    rows = d.get("data") or []
    if not rows:
        raise LookupError(f"{code_sh} {period} 在 {ep} 上没有数据")
    return rows[0]


def num(row: dict, key: str) -> Optional[float]:
    """取数值字段。★ **取不到就是 None，不许当 0**。"""
    v = row.get(key)
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s or s in ("-", "--"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ══════════════════════════════════════════════════════════════════
# 四、可判 FAIL 的四条规则
# ══════════════════════════════════════════════════════════════════
def check_A1(bs: dict, *, caliber: str = "合并", period: str = "") -> TieoutResult:
    """资产 = 负债 + 所有者权益。★ 用两条路径各核一次，**两次都过才 PASS**。"""
    TA, TL, TE, TLE = (num(bs, "TOTAL_ASSETS"), num(bs, "TOTAL_LIABILITIES"),
                       num(bs, "TOTAL_EQUITY"), num(bs, "TOTAL_LIAB_EQUITY"))
    inputs = [{"field": k, "value": v} for k, v in
              (("TOTAL_ASSETS", TA), ("TOTAL_LIABILITIES", TL),
               ("TOTAL_EQUITY", TE), ("TOTAL_LIAB_EQUITY", TLE))]
    if None in (TA, TL, TE):
        return make("A1", UNKNOWN, caliber=caliber, period=period, inputs=inputs,
                    note="必备字段缺失，无法闭合 —— **不降级为 PASS**")
    tol = max(1e-6, 1e-9 * abs(TA))
    r1 = TA - (TL + TE)
    r2 = None if TLE is None else TA - TLE
    worst = max(abs(r1), abs(r2)) if r2 is not None else abs(r1)
    ok = worst <= tol
    return make("A1", PASS if ok else FAIL, caliber=caliber, period=period, inputs=inputs,
                residual=worst, residual_rel=(worst / abs(TA) if TA else None), tolerance=tol,
                note=("两条路径（负债+权益 / 负债和所有者权益合计）都闭合" if ok
                      else f"负债+权益差 {r1!r}；对合计项差 {r2!r}"))


def check_A2(cf: dict, *, caliber: str = "合并", period: str = "") -> TieoutResult:
    """现金净增加额 = 经营 + 投资 + 筹资 + 汇率影响。"""
    CCE = num(cf, "CCE_ADD")
    NO, NI, NF = num(cf, "NETCASH_OPERATE"), num(cf, "NETCASH_INVEST"), num(cf, "NETCASH_FINANCE")
    RC = num(cf, "RATE_CHANGE_EFFECT")
    inputs = [{"field": k, "value": v} for k, v in
              (("CCE_ADD", CCE), ("NETCASH_OPERATE", NO), ("NETCASH_INVEST", NI),
               ("NETCASH_FINANCE", NF), ("RATE_CHANGE_EFFECT", RC))]
    if None in (CCE, NO, NI, NF):
        return make("A2", UNKNOWN, caliber=caliber, period=period, inputs=inputs,
                    note="三个现金流小计或净增加额缺失，无法闭合")
    if RC is None:
        # ★★ 关键：缺汇率影响**不是**公司的错，是我们的字段没取到 → UNKNOWN
        return make("A2", UNKNOWN, caliber=caliber, period=period, inputs=inputs,
                    note="RATE_CHANGE_EFFECT 缺失 —— 差额即汇率影响（CAS 31 应用指南），"
                         "**这是取数缺口不是报表问题，不许判 FAIL**")
    tol = max(1000.0, 1e-8 * abs(CCE))       # 千元取整尾差真实存在
    r = CCE - (NO + NI + NF + RC)
    return make("A2", PASS if abs(r) <= tol else FAIL, caliber=caliber, period=period,
                inputs=inputs, residual=r, residual_rel=(abs(r) / abs(CCE) if CCE else None),
                tolerance=tol)


def check_A3(cf: dict, *, caliber: str = "合并", period: str = "") -> TieoutResult:
    """期末现金 = 期初现金 + 净增加额。"""
    END, BEG, ADD = num(cf, "END_CCE"), num(cf, "BEGIN_CCE"), num(cf, "CCE_ADD")
    inputs = [{"field": k, "value": v} for k, v in
              (("END_CCE", END), ("BEGIN_CCE", BEG), ("CCE_ADD", ADD))]
    if None in (END, BEG, ADD):
        return make("A3", UNKNOWN, caliber=caliber, period=period, inputs=inputs,
                    note="期初/期末/净增加额三者不齐，无法闭合")
    tol = max(1000.0, 1e-9 * abs(END))
    r = END - (BEG + ADD)
    return make("A3", PASS if abs(r) <= tol else FAIL, caliber=caliber, period=period,
                inputs=inputs, residual=r, tolerance=tol,
                note="若现金及现金等价物范围发生变更，属口径变更，应人工确认而非直接认错")


# ══════════════════════════════════════════════════════════════════
# 五、只输出残差的三条（**结构性禁止 FAIL**）
# ══════════════════════════════════════════════════════════════════
# ★★ 镜像字段陷阱（实测发现）：同时相加会系统性算错。
#    OILGAS_BIOLOGY_DEPR ≡ FA_IR_DEPR
#    DEFER_TAX            ≡ DT_ASSET_REDUCE + DT_LIAB_ADD
B2_KEYS = ["FA_IR_DEPR", "IA_AMORTIZE", "LPE_AMORTIZE", "USERIGHT_ASSET_AMORTIZE",
           "DEFER_INCOME_AMORTIZE", "DISPOSAL_LONGASSET_LOSS", "FA_SCRAP_LOSS",
           "FAIRVALUE_CHANGE_LOSS", "FINANCE_EXPENSE", "INVEST_LOSS",
           "DT_ASSET_REDUCE", "DT_LIAB_ADD",
           "INVENTORY_REDUCE", "OPERATE_RECE_REDUCE", "OPERATE_PAYABLE_ADD"]
B2_EXCLUDED_MIRRORS = ["OILGAS_BIOLOGY_DEPR", "DEFER_TAX"]


def assert_no_mirrors() -> None:
    """★ 机制性防错：白名单里绝不许出现镜像字段。"""
    overlap = set(B2_KEYS) & set(B2_EXCLUDED_MIRRORS)
    if overlap:
        raise ValueError(f"B2 白名单里混进了镜像字段 {sorted(overlap)} —— "
                         "它们与别的字段同值，相加会把同一笔算两遍")


def impair_loss(is_row: dict) -> tuple:
    """减值损失合计。返回 `(值 或 None, 说明)`。

    ★ 这里有个**必须分清的差别**（实测踩到）：
        · 字段**不存在**于返回里   → 数据源没提供   → 返回 None（上层 UNKNOWN）
        · 字段**存在但值为 None**  → 本期该科目为空 → **当 0，但必须写进说明**
      把这两件事混为一谈，要么会冤枉公司，要么会造出假残差。

    ★ 符号约定是**实证反推**出来的，不是猜的：
      茅台 2025 年报补充资料「信用减值损失」= −17,234,379.37，
      而东财字段 `CREDIT_IMPAIRMENT_INCOME` = **+17,234,379.37**，
      即 **补充资料行值 = −(东财 *_INCOME 字段)**。
    """
    keys = ("ASSET_IMPAIRMENT_LOSS", "ASSET_IMPAIRMENT_INCOME",
            "CREDIT_IMPAIRMENT_LOSS", "CREDIT_IMPAIRMENT_INCOME")
    if not any(k in is_row for k in keys):
        return None, "利润表里没有任何减值字段 —— 数据源没提供"
    tot = 0.0
    empty = []
    for loss_k, inc_k in (("ASSET_IMPAIRMENT_LOSS", "ASSET_IMPAIRMENT_INCOME"),
                          ("CREDIT_IMPAIRMENT_LOSS", "CREDIT_IMPAIRMENT_INCOME")):
        v = num(is_row, loss_k)
        if v is not None:
            tot += v
            continue
        v = num(is_row, inc_k)
        if v is not None:
            tot += -v              # ★ 东财的 *_INCOME 是收益正列，补充资料要的是损失
            continue
        if loss_k in is_row or inc_k in is_row:
            empty.append(loss_k.replace("_LOSS", ""))     # 存在但为空 = 本期无此项
            continue
        return None, f"字段 {loss_k} 与 {inc_k} 都不在返回里 —— 数据源没提供"
    note = ("本期为空、按 0 处理的科目：" + "、".join(empty)) if empty else ""
    return tot, note


def check_B2(cf: dict, is_: dict, *, caliber: str = "合并", period: str = "") -> TieoutResult:
    """间接法勾稽。★ **永远返回 INFO/WARN，不返回 FAIL**。"""
    assert_no_mirrors()
    NP, NCO = num(is_, "NETPROFIT"), num(cf, "NETCASH_OPERATE")
    if NP is None or NCO is None:
        return make("B2", UNKNOWN, caliber=caliber, period=period,
                    note="净利润或经营活动现金流量净额缺失")
    imp, imp_note = impair_loss(is_)
    if imp is None:
        return make("B2", UNKNOWN, caliber=caliber, period=period,
                    note=f"减值科目无法取得 → 无法闭合。{imp_note}")
    parts, missing = {}, []
    for k in B2_KEYS:
        v = num(cf, k)
        if v is None:
            missing.append(k)
        else:
            parts[k] = v
    sigma = NP + (num(cf, "ASSET_IMPAIRMENT") or 0.0) + imp + sum(parts.values())
    resid = NCO - sigma
    rel = abs(resid) / abs(NCO) if NCO else None
    extra = f"；{imp_note}" if imp_note else ""
    return make("B2", WARN if (rel or 0) > 0.05 else INFO, caliber=caliber, period=period,
                residual=resid, residual_rel=rel, tolerance=0.05,
                note=("未解释残差 ≠ 公司有问题。**优先怀疑字段映射与列报口径差异**"
                      + (f"；本次缺 {len(missing)} 个调整项字段（{missing[:3]}…）" if missing else "")
                      + extra))


def check_A5(bs_cur: dict, bs_prev: dict, is_: dict, cf: dict, *,
             caliber: str = "合并", period: str = "") -> TieoutResult:
    """未分配利润滚动。★ **永远返回 INFO/WARN**。"""
    E, B = num(bs_cur, "UNASSIGN_RPOFIT"), num(bs_prev, "UNASSIGN_RPOFIT")
    NP = num(is_, "PARENT_NETPROFIT")
    DIV = num(cf, "ASSIGN_DIVIDEND_PORFIT")
    if None in (E, B, NP):
        return make("A5", UNKNOWN, caliber=caliber, period=period,
                    note="期初/期末未分配利润或归母净利润缺失")
    resid = E - (B + NP - (DIV or 0.0))
    rel = abs(resid) / abs(E) if E else None
    return make("A5", WARN if (rel or 0) > 0.05 else INFO, caliber=caliber, period=period,
                residual=resid, residual_rel=rel, tolerance=0.05,
                note="残差主要来自「支付」是现金口径而「利润分配」是宣告口径；"
                     "**且数据源没有所有者权益变动表（会企04表）**")


def check_C1(bs_cur: dict, bs_prev: dict, is_: dict, cf: dict, *,
             vat_rate: float = 0.13, caliber: str = "合并", period: str = "") -> TieoutResult:
    """收入-现金桥。★ **永远返回 INFO/WARN**；增值税率必须显式传入并标注。"""
    SALES = num(cf, "SALES_SERVICES")
    REV = num(is_, "TOTAL_OPERATE_INCOME")
    if SALES is None or REV is None:
        return make("C1", UNKNOWN, caliber=caliber, period=period,
                    note="销售商品收到的现金或营业总收入缺失")
    def d(k):
        a, b = num(bs_cur, k), num(bs_prev, k)
        return None if a is None or b is None else (a - b)
    # 完整桥：扣掉应收与应收票据增加、扣掉合同负债减少
    AR, NOTE, CL = d("ACCOUNTS_RECE"), d("NOTE_RECE"), d("CONTRACT_LIAB")
    if None in (AR, NOTE, CL):
        return make("C1", UNKNOWN, caliber=caliber, period=period,
                    note="应收/应收票据/合同负债期初期末不全 → **不降级为 PASS**")
    bridge = SALES - AR - NOTE + CL
    implied = bridge / (1.0 + vat_rate)
    ratio = implied / REV if REV else None
    return make("C1", INFO, caliber=caliber, period=period,
                residual=(implied - REV), residual_rel=ratio, tolerance=None,
                note=f"用了假设税率 {vat_rate:.0%}（**必须标注，禁止硬编码**）；"
                     f"比值 {ratio:.3f}。实测该判据误报 20%，"
                     "增值税率、票据背书、财务公司混入都会造成差异 —— **不许判异常**")


def run_all(code_sh: str, period: str, prev_period: str = "") -> list:
    """跑一遍全部规则，返回结果列表。**FAIL 只在有资格的规则上出现。**"""
    ct = company_type(code_sh)
    bs = fetch_table("zcfzbAjaxNew", code_sh, period, ct)
    cf = fetch_table("xjllbAjaxNew", code_sh, period, ct)
    is_ = fetch_table("lrbAjaxNew", code_sh, period, ct)
    out = [check_A1(bs, period=period), check_A2(cf, period=period), check_A3(cf, period=period),
           check_B2(cf, is_, period=period)]
    if prev_period:
        bsp = fetch_table("zcfzbAjaxNew", code_sh, prev_period, ct)
        out.append(check_A5(bs, bsp, is_, cf, period=period))
        out.append(check_C1(bs, bsp, is_, cf, period=period))
    return out
