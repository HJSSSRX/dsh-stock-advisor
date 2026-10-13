# -*- coding: utf-8 -*-
"""踩雷排查清单 —— **一次把已知的雷全扫一遍，并如实报告哪些扫不了**。

═══════════════════════════════════════════════════════════════════════════
它解决的是哪一类亏损
═══════════════════════════════════════════════════════════════════════════
散户亏钱的原因里，有一类最没必要也最冤：**踩到本来就公开披露、只是没去看的雷**。

    · 不知道它已经 ST / 快到退市指标
    · 不知道大股东质押了 85%
    · 不知道下周有大额限售解禁
    · 不知道大股东正在减持
    · 不知道账上商誉占净资产一大半
    · 不知道三表对不上

**这些每一条都有公开出处。** 这份清单把它们一次性扫完。

═══════════════════════════════════════════════════════════════════════════
★★★ 铁律：**三态，且 UNKNOWN 绝不等于 CLEAR**
═══════════════════════════════════════════════════════════════════════════
    命中    HIT      查到了，有问题
    未发现  CLEAR    查了，没发现
    无法判定 UNKNOWN  数据没取到 / 判据不适用 —— **这不等于「没问题」**

**汇总必须三类分列。** 把 UNKNOWN 混进 CLEAR 是最容易犯、后果最重的错 ——
用户会以为"都查过了没问题"，而实际上有一半根本没查。

**而且每一项都要报它的数据源**：取不到就说取不到，不许用"一般""通常"填空。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

HIT, CLEAR, UNKNOWN = "命中", "未发现", "无法判定"


@dataclass
class CheckResult:
    id: str
    name: str
    verdict: str
    detail: str = ""
    source: str = ""          # ★ 数据源 —— 空 = 没取到
    citation: str = ""        # ★ 判据出处
    threshold: str = ""       # ★ 阈值（有的话）

    def render(self) -> str:
        icon = {HIT: "🔴", CLEAR: "🟢", UNKNOWN: "⚪"}.get(self.verdict, "?")
        L = [f"{icon} [{self.id}] {self.verdict}｜{self.name}"]
        if self.detail:
            L.append(f"      {self.detail}")
        if self.citation:
            L.append(f"      判据：{self.citation}" + (f"｜阈值 {self.threshold}" if self.threshold else ""))
        if self.verdict == UNKNOWN and self.source == "":
            L.append("      ⚠️ **数据没取到 —— 这一项没查，不是「没问题」**")
        return "\n".join(L)


# ══════════════════════════════════════════════════════════════════
# 各项检查
# ══════════════════════════════════════════════════════════════════
def check_st(code: str) -> CheckResult:
    """交易所风险警示名单。★ S0 级事实，可直接断言。

    ⚠️ 走**适配层** `data.adapters.st_snapshot()`，不走 vendor 直取 ——
       vendor 的 `st_stock_list()` 有一个已知缺陷（北交所 ST 同时出现在
       风险警示板和北交所全表里 → 重复 → 整个名单抛错，详见 `data/adapters.py`）。
       适配层修掉它，并且**报告走的是哪条路**。
    """
    try:
        from data import adapters as AD
        hit, path, note = AD.find(code)
        if hit is None:
            return CheckResult("ST", "风险警示（ST/*ST）", UNKNOWN, note, source="")
        src = "eastmoney（经适配层去重）" if path == AD.PATH_ADAPTER else "eastmoney"
        return CheckResult("ST", "风险警示（ST/*ST）",
                           HIT if hit else CLEAR, note, source=src,
                           citation="交易所风险警示名单（S0 级事实）")
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("ST", "风险警示（ST/*ST）", UNKNOWN,
                           f"{type(e).__name__}: {str(e)[:60]}", source="")


def check_pledge(code: str) -> CheckResult:
    """股权质押比例。阈值出处：深交所指引11号第25条(七)。"""
    try:
        from data.vendor_bridge import fetch_via_vendor
        r = fetch_via_vendor("equity_pledge", code)
        if not r.ok or not r.data:
            return CheckResult("PLEDGE", "股权质押", UNKNOWN,
                               f"质押接口返回空/失败：{str(r.error)[:50]}", source="")
        rows = r.data if isinstance(r.data, list) else [r.data]
        row = next((x for x in rows if isinstance(x, dict)), None)
        if row is None:
            return CheckResult("PLEDGE", "股权质押", UNKNOWN, "返回结构无法解析", source="")
        pct = row.get("pledge_ratio_pct")
        if pct is None:
            return CheckResult("PLEDGE", "股权质押", UNKNOWN, "没有质押比例字段", source="")
        pct = float(pct)
        # 80% 是深交所指引11号第25条(七)的「不得高于C」线；
        # 但那条说的是**控股股东及其一致行动人**的质押比例，
        # 而本接口给的是**全公司**质押比例 —— 口径不同，所以这里只做提示不判 HIT。
        if pct >= 30:
            v = HIT
            d = f"全公司质押比例 {pct:.2f}%（较高）"
        elif pct > 0:
            v = CLEAR
            d = f"全公司质押比例 {pct:.2f}%"
        else:
            v, d = CLEAR, "无质押"
        return CheckResult("PLEDGE", "股权质押", v, d,
                           source=getattr(r, "source", "eastmoney"),
                           citation="深交所指引11号第25条(七)：控股股东及一致行动人 >80% 质押 → 不得高于C",
                           threshold="30%（本检查的提示线，非监管线）")
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("PLEDGE", "股权质押", UNKNOWN,
                           f"{type(e).__name__}: {str(e)[:60]}", source="")


def check_reduction(code: str) -> CheckResult:
    """大股东/高管增减持。"""
    try:
        from data.vendor_bridge import fetch_via_vendor
        r = fetch_via_vendor("holder_trades", code)
        if not r.ok:
            return CheckResult("REDUCE", "大股东/高管减持", UNKNOWN,
                               f"接口失败：{str(r.error)[:50]}", source="")
        rows = r.data if isinstance(r.data, list) else ([r.data] if r.data else [])
        rows = [x for x in rows if isinstance(x, dict)]
        if not rows:
            return CheckResult("REDUCE", "大股东/高管减持", CLEAR,
                               "近期无增减持记录", source=getattr(r, "source", "eastmoney"))
        red = [x for x in rows if "减" in str(x.get("direction", ""))]
        add = [x for x in rows if "增" in str(x.get("direction", ""))]
        if red:
            who = "、".join(str(x.get("holder", ""))[:12] for x in red[:2])
            d = f"近期有减持记录 {len(red)} 条（{who}）"
            return CheckResult("REDUCE", "大股东/高管减持", HIT, d,
                               source=getattr(r, "source", "eastmoney"),
                               citation="交易所权益变动披露")
        return CheckResult("REDUCE", "大股东/高管减持", CLEAR,
                           f"近期只有增持 {len(add)} 条、无减持",
                           source=getattr(r, "source", "eastmoney"),
                           citation="交易所权益变动披露")
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("REDUCE", "大股东/高管减持", UNKNOWN,
                           f"{type(e).__name__}: {str(e)[:60]}", source="")


def check_unlock(code: str) -> CheckResult:
    """限售解禁。"""
    try:
        from data.vendor_bridge import fetch_via_vendor
        r = fetch_via_vendor("restricted_release", code)
        if not r.ok or not r.data:
            return CheckResult("UNLOCK", "限售解禁", UNKNOWN,
                               f"接口失败/空：{str(r.error)[:50]}", source="")
        d0 = r.data[0] if isinstance(r.data, list) and r.data else r.data
        if not isinstance(d0, dict):
            return CheckResult("UNLOCK", "限售解禁", UNKNOWN, "结构无法解析", source="")
        up = d0.get("upcoming") or []
        if not up:
            return CheckResult("UNLOCK", "限售解禁", CLEAR, "无待解禁记录",
                               source=getattr(r, "source", "eastmoney"))
        first = up[0] if isinstance(up[0], dict) else {}
        return CheckResult("UNLOCK", "限售解禁", HIT,
                           f"有 {len(up)} 批待解禁，最近一批 {first.get('date', '日期未知')}",
                           source=getattr(r, "source", "eastmoney"),
                           citation="交易所限售股解禁安排")
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("UNLOCK", "限售解禁", UNKNOWN,
                           f"{type(e).__name__}: {str(e)[:60]}", source="")


def check_warning(code: str) -> CheckResult:
    """业绩预告：预亏 / 大幅下滑。"""
    try:
        from data.vendor_bridge import fetch_via_vendor
        r = fetch_via_vendor("earnings_forecast", code)
        if not r.ok or not r.data:
            return CheckResult("WARN", "业绩预告", UNKNOWN,
                               f"接口失败/空：{str(r.error)[:50]}", source="")
        rows = [x for x in (r.data if isinstance(r.data, list) else [r.data])
                if isinstance(x, dict)]
        if not rows:
            return CheckResult("WARN", "业绩预告", CLEAR, "无业绩预告记录",
                               source=getattr(r, "source", "eastmoney"))
        rows.sort(key=lambda x: str(x.get("report_date") or x.get("date") or ""), reverse=True)
        last = rows[0]
        lo = last.get("change_pct_lower")
        hi = last.get("change_pct_upper")
        try:
            lo_f = float(lo) if lo is not None else None
        except (TypeError, ValueError):
            lo_f = None
        if lo_f is not None and lo_f < 0:
            return CheckResult("WARN", "业绩预告", HIT,
                               f"最近一期预告同比变动 {lo}% ~ {hi}%（为负）",
                               source=getattr(r, "source", "eastmoney"),
                               citation="上市公司业绩预告（强制披露）")
        return CheckResult("WARN", "业绩预告", CLEAR,
                           f"最近一期预告同比变动 {lo}% ~ {hi}%",
                           source=getattr(r, "source", "eastmoney"),
                           citation="上市公司业绩预告（强制披露）")
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("WARN", "业绩预告", UNKNOWN,
                           f"{type(e).__name__}: {str(e)[:60]}", source="")


# ★ 判据文案抽成常量：**CLEAR 与 HIT 两个分支必须说同一句话**。
#   第一版两边写法不同（CLEAR 少了「只做提示，不进结论」），被测试抓出来了。
_GW_CITATION = ("⚠️ **30% 是经验阈值，无法规依据** —— 只做提示，不进结论"
                "（调研未找到给出该阈值的实证来源）")


def check_goodwill(code: str, period: str = "2025-12-31") -> CheckResult:
    """商誉占归母净资产。

    ⚠️ 阈值是**经验值，无法规依据**（调研未找到给出阈值的实证来源），**只做提示、不进结论**。

    ★ 数据源换过一次：新浪三表**没有商誉字段**（第一版因此报 UNKNOWN），
      改用东财 F10 全表的 GOODWILL / TOTAL_PARENT_EQUITY。
    ★ 还要分清「字段不存在」与「字段存在但为空」：
      茅台的 GOODWILL 字段**在表里但值为 None** = 本期无商誉 → 按 0 处理；
      字段**不在表里** = 数据源没提供 → UNKNOWN。
    """
    try:
        from compute import tieout as TO
        mk = "SH" if str(code).startswith("6") else "SZ"
        ct = TO.company_type(f"{mk}{code}")
        bs = TO.fetch_table("zcfzbAjaxNew", f"{mk}{code}", period, ct)
    except Exception as e:                                   # noqa: BLE001
        return CheckResult("GOODWILL", "商誉占比", UNKNOWN,
                           f"F10 全表取不到：{type(e).__name__}: {str(e)[:60]}", source="")
    if "GOODWILL" not in bs:
        return CheckResult("GOODWILL", "商誉占比", UNKNOWN,
                           "F10 资产负债表里**没有 GOODWILL 字段** —— "
                           "数据源没提供，**这一项没查**", source="")
    gw, eq = bs.get("GOODWILL"), bs.get("TOTAL_PARENT_EQUITY")
    try:
        gw_f = float(str(gw).replace(",", "")) if gw not in (None, "", "--") else 0.0
        eq_f = float(str(eq).replace(",", "")) if eq not in (None, "", "--") else None
    except (TypeError, ValueError):
        return CheckResult("GOODWILL", "商誉占比", UNKNOWN, "字段值无法解析", source="")
    if eq_f in (None, 0):
        return CheckResult("GOODWILL", "商誉占比", UNKNOWN,
                           "缺归母净资产（TOTAL_PARENT_EQUITY）—— 算不出比例", source="")
    if gw_f == 0:
        return CheckResult("GOODWILL", "商誉占比", CLEAR,
                           "无商誉（字段在表里、值为空 = 本期无此项）",
                           source="emdc_f10", citation=_GW_CITATION)
    ratio = gw_f / eq_f * 100
    return CheckResult("GOODWILL", "商誉占比", HIT if ratio >= 30 else CLEAR,
                       f"商誉 {gw_f/1e8:.2f} 亿 / 归母净资产 {eq_f/1e8:.2f} 亿 = **{ratio:.1f}%**",
                       source="emdc_f10",
                       citation=_GW_CITATION, threshold="30%（经验值）")


# ══════════════════════════════════════════════════════════════════
CHECKS = (check_st, check_pledge, check_reduction, check_unlock,
          check_warning, check_goodwill)


@dataclass
class MinefieldReport:
    code: str
    results: list = field(default_factory=list)

    @property
    def n_hit(self) -> int:
        return sum(1 for r in self.results if r.verdict == HIT)

    @property
    def n_clear(self) -> int:
        return sum(1 for r in self.results if r.verdict == CLEAR)

    @property
    def n_unknown(self) -> int:
        return sum(1 for r in self.results if r.verdict == UNKNOWN)

    def render(self) -> str:
        n = len(self.results)
        L = [f"【踩雷排查】共 {n} 项：**命中 {self.n_hit} ｜ 未发现 {self.n_clear} "
             f"｜ 无法判定 {self.n_unknown}**"]
        for r in self.results:
            L.append("  " + r.render().replace("\n", "\n  "))
        L.append("  ⚠️ **「未发现」≠「没有」，「无法判定」更 ≠「没问题」** ——"
                 " 无法判定的那几项，本次**根本没查**。")
        L.append("  ⚠️ 本清单只覆盖**有公开出处、可机械判定**的雷；"
                 "**覆盖不了**：财务造假（做得好就查不出）、大股东掏空、"
                 "行业周期反转、政策突变。")
        return "\n".join(L)


def run(code: str) -> MinefieldReport:
    """跑全部检查。**任何一项失败都不许让它消失 —— 失败的记成 UNKNOWN。**"""
    out = MinefieldReport(code=code)
    for fn in CHECKS:
        try:
            out.results.append(fn(code))
        except Exception as e:                               # noqa: BLE001
            out.results.append(CheckResult(
                getattr(fn, "__name__", "?"), getattr(fn, "__doc__", "") or "?", UNKNOWN,
                f"检查本身崩了：{type(e).__name__}: {str(e)[:60]}", source=""))
    return out
