# -*- coding: utf-8 -*-
"""端到端 —— 一句话进去，一份**核验过的简报**出来。

═══════════════════════════════════════════════════════════════════
它在整条链里的位置（这是最后一根线，之前一直没接）
═══════════════════════════════════════════════════════════════════

    用户问一句话
        ↓  ① parse()      听懂问什么、问的是哪只票（**认不出就拒答，不猜**）
    取数计划
        ↓  ② 取数         走 registry / vendor，带缓存·降级·失败即值
    原始包 Result
        ↓  ③ 证据桥       Result → Evidence（带口径·时点·原始包指纹）
    证据
        ↓  ④ 计算         指标 / 位置 / 风险 / 市场宽度
    数字
        ↓  ⑤ 规则计数     机械规则 → 多空各几条（**明说权重是人工设定的**）
        ↓  ⑥ claim.build  没有依据的结论构造不出来
    结论
        ↓  ⑦ render       简报：事实｜指标｜多空｜依据｜时效｜缺口

★ 铁律（`交接文档.md` §一）：**模型只干两件事** —— 听懂问题、把答案说成人话。
  本模块里的取数/计算/校验**全是确定性代码**，不调用任何模型。

═══════════════════════════════════════════════════════════════════
★★ 这个模块怎么算"比豆包好"
═══════════════════════════════════════════════════════════════════
不靠"我觉得我说得好"，靠五条**豆包做不到**的事：

  1. 每个数字带**证据号 + 原始包指纹** → 能一路走回抓回来的那一包数据（trace）
  2. 每个财务数字标**口径** —— 净利润 460.33 亿 vs 归属净利润 445.17 亿，差 15 亿
  3. 每个数字带**数据时点**，用了过期缓存必须写明"不是现在的"
  4. 拿不准/取不到 → **明说缺什么**，绝不填空（豆包实测编了个公告编号 2026-032）
  5. 结论给**多空两边 + 失效条件**，不是单边喊单

★ 第 5 条还要加一句诚实话：**机械规则的权重是人工设定的，没有做过实证校准。**
  所以本模块把它明写成「规则计数」，不伪装成"模型判断"或"实证权重"。
"""
from __future__ import annotations

import os
import re
import sys
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# ★ 有些数据源库（akshare）会往 stdout 打进度条，污染简报输出 → 关掉
os.environ.setdefault("TQDM_DISABLE", "1")

from data.registry import fetch as registry_fetch          # noqa: E402
from data.vendor_bridge import fetch_via_vendor             # noqa: E402
from evidence.bridge import BridgeError, to_evidence, trace  # noqa: E402
from evidence.record import Evidence                        # noqa: E402
from judge import claim as C                                # noqa: E402
from judge.ledger import BEAR, BULL, HOLD                   # noqa: E402

# ══════════════════════════════════════════════════════════════════
# 问题类型
# ══════════════════════════════════════════════════════════════════
Q_QUOTE = "行情"
Q_FINANCIAL = "财务"
Q_MARKET = "大盘"
Q_PROFILE = "个股分析"
Q_KINDS = (Q_QUOTE, Q_FINANCIAL, Q_MARKET, Q_PROFILE)

# ══════════════════════════════════════════════════════════════════
# 名称 → 代码（**只认认识的；不认识的直接拒答，绝不猜**）
#   ⚠️ 这是个起步表，不是完整列表。要覆盖全市场应当走 universe 数据源做主键。
#      在补上之前，**宁可拒答也不猜代码** —— 猜错标的是最严重的错。
# ══════════════════════════════════════════════════════════════════
NAME2CODE = {
    "贵州茅台": "600519", "茅台": "600519",
    "平安银行": "000001",
    "宁德时代": "300750",
    "五粮液": "000858",
    "比亚迪": "002594",
    "中国平安": "601318",
    "工商银行": "601398",
    "招商银行": "600036",
    "隆基绿能": "601012",
    "美的集团": "000333",
    "长江电力": "600900",
}

_INDEX_ALIAS = {
    "上证指数": "000001", "上证": "000001", "大盘": "000001",
    "深证成指": "399001", "创业板指": "399006", "创业板": "399006",
    "沪深300": "000300", "上证50": "000016", "科创50": "000688",
    "中证500": "000905",
}


# ══════════════════════════════════════════════════════════════════
# 全市场名单 —— 任意股票名 → 代码（+ 第二个独立来源的收盘价）
# ══════════════════════════════════════════════════════════════════
_UNIVERSE: Optional[dict] = None


def _universe() -> dict:
    """取全市场名单（通达信封，52k 行，走缓存）。失败返回空 dict，**不猜**。"""
    global _UNIVERSE
    if _UNIVERSE is not None:
        return _UNIVERSE
    idx: dict = {"by_name": {}, "row": {}}
    try:
        r = registry_fetch("universe")
        if r.ok and isinstance(r.data, list):
            for row in r.data:
                if not isinstance(row, dict):
                    continue
                nm = str(row.get("name", "")).strip()
                c = str(row.get("code", "")).strip().zfill(6)
                m = str(row.get("market", "")).strip().lower()
                if nm and c:
                    idx["by_name"].setdefault(nm, []).append((m, c))
                    idx["row"][(m, c)] = row
    except Exception:                                       # noqa: BLE001
        pass
    _UNIVERSE = idx
    return idx


def _lookup_stock(name: str) -> tuple[str, str]:
    """名字 → (market, code)，**只认唯一匹配；有歧义就拒答**。"""
    cands = _universe()["by_name"].get(name, [])
    if not cands:
        return "", ""
    if len(cands) > 1:
        raise AskError(
            f"「{name}」在名单里有 {len(cands)} 个匹配（{cands}）——"
            "**有歧义就不猜**。请给 6 位代码。")
    return cands[0]


def _market_of(code: str) -> str:
    """按代码前缀推市场。★ 必须带市场，因为 000001 在沪是上证指数、在深是平安银行。"""
    c = str(code).zfill(6)
    if c.startswith(("600", "601", "603", "605", "688", "689", "999", "000001")):
        return "sh" if not c.startswith(("999",)) else "sh"
    return "sz"


def _second_source_close(code: str) -> Optional[Fact]:
    """★ 从**通达信封**取收盘价 —— 与腾讯快照互为独立第二来源。

    ⚠️ 必须按 **(市场, 代码) 配对**，**不能按代码裸匹配**（实测踩到）：
       `000001` 在 sh 是**上证指数**、在 sz 才是平安银行 ——
       **光看代码分不出来**。裸匹配的后果是"平安银行"的第二来源取到 3797.24，
       输出变成"两个来源的价不一致：腾讯 11.73 vs 通达信 3797.24"。

    → 做法：把该代码的所有候选都取出来，**挑"看起来是股票的那条"**；
       一条都不是就给不出第二来源（**宁可不给，不给错的**）。
    """
    u = _universe()
    if not u["row"]:
        return None
    cands = [(m, c, r) for (m, c), r in u["row"].items() if c == str(code).zfill(6)]
    if not cands:
        return None
    if len(cands) > 1:
        # 排除指数/板块/基金这类非个股名字
        bad = ("指数", "成指", "指", "板块", "ETF", "基金", "债", "LOF", "REIT")
        stockish = [t for t in cands if not any(b in str(t[2].get("name", "")) for b in bad)]
        if len(stockish) != 1:
            return None            # 还是分不清 → 宁可不给第二来源
        cands = stockish
    m, c, row = cands[0]
    close = row.get("close")
    if close is None:
        return None
    val = f"{float(close):,.2f}"
    as_of = str(row.get("date", ""))
    nm = str(row.get("name", ""))
    # ★★ 这条也要有证据号 —— 独立验证实测：
    #    「收盘价（通达信封·第二来源）」曾是全篇**唯一没有证据号的事实**，
    #    即"用来对拍的那条自己无法回溯"。对拍机制生出一个不可回溯的事实，说不过去。
    ev = Evidence(company=f"{c} {nm}".strip(), period=as_of,
                  metric="收盘价（通达信封·第二来源）",
                  value=val.replace(",", ""), unit="元", source="tdx",
                  caliber="通达信盘后包，不复权")
    return Fact(
        label="收盘价（通达信封·第二来源）", value=val,
        unit="元", source="tdx", kind="tdx_package",
        as_of=as_of, caliber="通达信盘后包，不复权",
        eid=ev.eid, origin="", path="",
        note=f"独立第二来源（腾讯实时接口 vs 通达信盘后包）｜配对 {m}.{c}「{nm}」",
    )


class AskError(ValueError):
    """问的问题我没法诚实回答 —— **拒答，不硬编一个答案**。"""


def _f2(x: Any) -> str:
    """数字保留两位、带千分位。取不到就返回占位符，**不返回 0**。"""
    if x is None:
        return "—"
    try:
        return f"{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


# ★ 口径 id → 公告原文里的行标签口径名（PDF 核对要用原文写法）
_PDF_CALIBER = {
    "net_profit_parent": "归属净利润",
    "net_profit": "净利润",
    "operating_revenue_total": "营业总收入",
    "operating_revenue": "营业收入",
    "eps": None,
}

# ⚠️ 起步映射：本机现成的公告原文。
#    真正的做法是**按查询去巨潮下载该标的的公告 PDF**（kind=announcement 已有）。
#    在接上之前，只有这里列出的标的有"公告原文核对"能力 —— 别的标的会如实说没有。
PDF_FOR = {"600519": "mt2026h1.pdf"}


def _find_pdf(code: str) -> str:
    """本机有没有该标的的公告原文（**外部标准答案**）。"""
    fn = PDF_FOR.get(str(code))
    if not fn:
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    for n in (2, 3, 4):
        p = os.path.normpath(os.path.join(here, *([".."] * n), "素材", "保真测试", fn))
        if os.path.exists(p):
            return p
    return ""


_PDF_CACHE: dict = {}

# 公告 PDF 落盘目录（下载一次，以后离线可复用）
_PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pdf_cache")

# 定期报告的标题关键词（**要的是报告正文，不是摘要/更正/英文版**）
_REPORT_KW = ("年度报告", "半年度报告", "季度报告")
_REPORT_SKIP = ("摘要", "更正", "补充", "英文", "问询", "意见", "公告", "提示",
                "说明", "保荐", "募集", "社会责任", "环境")


def _download_report_pdf(code: str, brief: Brief, log: FetchLog) -> tuple[str, str]:
    """★ **按查询去巨潮下该标的的定期报告原文** —— 有了它，"回原文逐字核对"
    才能覆盖全部标的，而不只是本机预存的那一只。

    返回 (本地路径, 标题)；下不到就返回 ("", "")，**不假装有**。
    """
    os.makedirs(_PDF_DIR, exist_ok=True)
    res = _get("announcement", code, {"page_size": 60, "days": 500}, log=log)
    if res is None:
        return "", ""
    rows = [r for r in (res.data or []) if isinstance(r, dict)]
    cands = []
    for r in rows:
        title = str(r.get("标题", "") or "")
        url = str(r.get("PDF", "") or "")
        if not url or not title:
            continue
        if any(k in title for k in _REPORT_KW) and not any(s in title for s in _REPORT_SKIP):
            cands.append((str(r.get("日期", "")), title, url, str(r.get("公告ID", ""))))
    if not cands:
        brief.gaps.append("巨潮公告里没找到该标的的定期报告（年报/半年报/季报）")
        return "", ""
    cands.sort(reverse=True)                    # 最新在前
    date, title, url, aid = cands[0]
    path = os.path.join(_PDF_DIR, f"{code}-{aid}.pdf")
    if not os.path.exists(path):
        try:
            import httpx
            with httpx.Client(timeout=60, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"}) as cli:
                resp = cli.get(url)
                if resp.status_code != 200 or not resp.content:
                    brief.gaps.append(f"公告 PDF 下载失败（HTTP {resp.status_code}）：{url}")
                    return "", ""
                with open(path, "wb") as fh:
                    fh.write(resp.content)
        except Exception as e:                              # noqa: BLE001
            brief.gaps.append(f"公告 PDF 下载异常：{e}")
            return "", ""
    brief.notices.append(
        f"📄 **已取到该标的的定期报告原文**：{title}（{date}）"
        f" —— 下面的财务数字会拿它逐字核对。")
    return path, title


def _cross_check_pdf(code: str, value: str, caliber_id: str,
                     pdf_path: str = "") -> Optional[str]:
    """★★ 拿这个数字去**交易所公告原文**里逐字核对。

    这是"比豆包好"最硬的一条：豆包实测编了个公告编号（2026-032），
    而它答错的那个数（280.12 亿）**在公告原文里根本找不到**。
    """
    path = pdf_path or _find_pdf(code)
    if not path:
        return None
    try:
        doc = _PDF_CACHE.get(path)
        if doc is None:
            import contextlib
            import io
            from evidence.pdf_source import extract
            # PyMuPDF 会往 stdout 打一句建议，污染简报 → 关掉
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                doc = extract(path)
            if not doc.ok:
                return None
            _PDF_CACHE[path] = doc
        from evidence.pdf_source import verify_number
        exp = _PDF_CALIBER.get(caliber_id, "__UNSET__")
        if exp == "__UNSET__":
            v, why, _ = verify_number(doc, value)
        else:
            v, why, _ = verify_number(doc, value, expect_caliber=exp, expect_amount=True)
        return f"{v}｜{why}"
    except Exception as e:                                  # noqa: BLE001
        return f"核对失败：{e}"


# ══════════════════════════════════════════════════════════════════
# 一条事实（简报的最小单位）
# ══════════════════════════════════════════════════════════════════
@dataclass
class Fact:
    label: str                       # 人话标签
    value: str
    unit: str = ""
    source: str = ""
    kind: str = ""                   # ★ 数据层 kind（trust 分档用）——**从 Result 直接带过来，不猜**
    as_of: str = ""                  # 数据本身的时点
    caliber: str = ""                # ★ 口径，必须写
    eid: str = ""                    # 证据号
    origin: str = ""                 # 原始包指纹（能走回去）
    path: str = ""                   # 在原始包的哪个字段
    stale_note: str = ""             # ★ 过期必须写
    note: str = ""

    def line(self) -> str:
        s = f"{self.label}：{self.value}"
        if self.unit:
            s += f" {self.unit}"
        if self.caliber:
            from evidence.caliber import alias_of
            al = alias_of(self.caliber)
            s += f"（口径：{al}）" if al and al != self.caliber else f"（口径：{self.caliber}）"
        tail = []
        if self.as_of:
            tail.append(f"数据时点 {self.as_of}")
        if self.source:
            tail.append(self.source)
        if self.eid:
            tail.append(f"证据 {self.eid}")
        if tail:
            s += "　｜ " + " · ".join(tail)
        if self.stale_note:
            s += f"\n      ⚠️ {self.stale_note}"
        if self.note:
            s += f"\n      {self.note}"
        return s

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("label", "value", "unit", "source", "as_of", "caliber",
                 "eid", "origin", "path", "stale_note", "note")}


# ══════════════════════════════════════════════════════════════════
# 取数（带失败即值：取不到要留痕，不许静默）
# ══════════════════════════════════════════════════════════════════
@dataclass
class FetchLog:
    tries: list[dict] = field(default_factory=list)

    def add(self, kind: str, code: str, res: Any) -> None:
        self.tries.append({
            "kind": kind, "code": code,
            "ok": bool(getattr(res, "ok", False)),
            "source": getattr(res, "source", ""),
            "error_kind": getattr(res, "error_kind", ""),
            "why": (res.why() if not getattr(res, "ok", False)
                    and hasattr(res, "why") else ""),
        })

    def gaps(self) -> list[str]:
        return [f"{t['kind']}({t['code']})：{t['why'] or t['error_kind'] or '取数失败'}"
                for t in self.tries if not t["ok"]]


def _get(kind: str, code: str = "", params: Optional[dict] = None,
         log: Optional[FetchLog] = None):
    """取数：**先 vendor（不被封），失败退 registry**。取不到返回 None 并留痕。"""
    res = None
    try:
        res = fetch_via_vendor(kind, code, params or {})
    except Exception:                                       # noqa: BLE001
        res = None
    if res is None or not getattr(res, "ok", False):
        try:
            res2 = registry_fetch(kind, code, params or {})
            if getattr(res2, "ok", False):
                res = res2
        except Exception:                                   # noqa: BLE001
            pass
    if log is not None and res is not None:
        log.add(kind, code, res)
    return res if (res is not None and getattr(res, "ok", False)) else None


def _stale_note(res: Any) -> str:
    """★ 过期兜底必须写进回答 —— 不写就等于用旧数据冒充实时。"""
    if getattr(res, "stale", False):
        h = float(getattr(res, "age_seconds", 0.0)) / 3600.0
        return (f"这是 {h:.1f} 小时前的缓存数据，**不是现在的**"
                "（所有源都没取到，用的是旧的）")
    return ""


def _is_tradable_stock(code: str):
    """这个 6 位代码**是不是一只 A 股股票**？

    ★★ 独立验证实测：ETF / 指数 / 板块指数 / 北交所公司债 / B 股 **全部走个股通道**，
      还给它们算「涨停价/跌停价」（按股票 ±10%），给指数喊「能不能买」。
      「认不出」和「不是股票」是两件事，前者我们拒答，后者原来**放行了**。
    """
    from compute.breadth import _is_stock
    u = _universe()
    rows = [(m, c, r) for (m, c), r in u["row"].items() if c == str(code).zfill(6)]
    if not rows:
        return False, "这个代码不在全市场名单里（多半不存在或已退市）"
    stockish = [(m, c, r) for m, c, r in rows if _is_stock(m, c)]
    if not stockish:
        names = "、".join(f"{r.get('name', '')}({m})" for m, c, r in rows)
        return False, (f"这不是一只 A 股股票，而是 **{names}**"
                       "（ETF/指数/债券/B股之类）。本系统只做个股分析，"
                       "不会拿股票的规则往上套。")
    if len(stockish) > 1:
        return False, f"这个代码在名单里有多个股票匹配：{[r.get('name') for _m, _c, r in stockish]}"
    return True, ""


def _plausible(label: str, v: float):
    """这个值是不是**看起来就不可能**（上游用 0 / -1 之类的哨兵值表示「不适用」）。

    ★★ 独立验证实测：沪深300ETF（510300）的行情包返回 `pe_ttm=0, pb=0`，
      创业板指（399006）返回 `limit_up=-1, limit_down=-1`。
      这些**不是数据**，是上游表示「我没有这个字段」的占位。
      第一版把它们当真实数值印出来，而且 **PB=0 还触发了「破净」这条看空触发器**，
      造出一个「有依据」的看空结论 —— 这比不答更坏。
    """
    if label in ("市盈率TTM", "市净率") and v <= 0:
        return False, f"{label} 是 {v}（不可能的值，多半是上游的「不适用」哨兵值）"
    if label in ("最新价", "涨停价", "跌停价", "收盘价") and v <= 0:
        return False, f"{label} 是 {v}（价格不可能 ≤ 0）"
    if label in ("总市值", "换手率") and v < 0:
        return False, f"{label} 是负数（{v}）"
    return True, ""


def _row_prefix(res: Any, idx: int = 0) -> str:
    """payload 是**列表**时，字段路径必须带行号（`0.price`）。

    ★ 实测踩到：不带行号 → `dig` 找不到 → 事实一条都出不来，而且**不报错**。
      跟血泪清单第 6 条是同一类：取不到静默变空。
    """
    return f"{idx}." if isinstance(getattr(res, "data", None), list) else ""


def _fact_from(res: Any, *, metric: str, path: str, unit: Optional[str] = None,
               caliber: Optional[str] = None, note: str = "") -> Optional[Fact]:
    """原始包 → 一条事实。**取不到字段就返回 None，绝不填 0。**"""
    try:
        ev = to_evidence(res, metric=metric, path=path, unit=unit, caliber=caliber)
    except BridgeError:
        return None
    return Fact(
        label=metric, value=ev.value, unit=ev.unit, source=ev.source,
        kind=str(getattr(res, "kind", "")),      # ★ 直接带过来，不猜
        as_of=ev.period, caliber=ev.caliber, eid=ev.eid,
        origin=ev.origin_eid, path=ev.origin_path,
        stale_note=_stale_note(res), note=note,
    )


# ══════════════════════════════════════════════════════════════════
# ① 解析问题
# ══════════════════════════════════════════════════════════════════
@dataclass
class Query:
    raw: str
    qtype: str
    code: str = ""
    name: str = ""
    index_code: str = ""


_FIN_KW = ("净利润", "营收", "营业收入", "业绩", "财报", "收入", "每股收益", "赚了多少",
           "利润", "毛利", "roe", "净资产收益")
_QUOTE_KW = ("多少钱", "现价", "股价", "价格", "报价", "现在多少", "收盘", "涨了", "跌了", "涨幅")
_MARKET_KW = ("大盘", "市场", "今天股市", "行情怎么样", "涨跌家数", "涨停", "两市", "成交额",
              "大盘怎么样", "市场情绪", "股市怎么样")
_PROFILE_KW = ("能不能买", "该不该买", "值不值得", "分析", "怎么样", "看好", "能不能","评估",
               "如何", "怎么看", "贵不贵", "便宜")
# ★ 问"消息/事件"—— 情报通道还没接线，**必须拒答，不许返回一个空的别的类型简报**
_NEWS_KW = ("消息", "新闻", "利好", "利空", "传闻", "热点", "怎么了", "发生了什么",
            "有什么公告", "出事")


def _name_index() -> dict:
    """名字 → [(market, code)]。**全市场名单 + 我们的常用别名合并**。

    ⚠️ 只查名单会漏：名单里是「贵州茅台」，而用户口语说「茅台」。
       第一版只查名单 → 「茅台和五粮液哪个好」只认出五粮液 → **静默只答一只**。
    """
    idx: dict = {k: [("", v)] for k, v in NAME2CODE.items()}
    for nm, cands in _universe()["by_name"].items():
        idx.setdefault(nm, cands)
    return idx


def _mentions(q: str) -> list[tuple[str, str]]:
    """问题里提到了**哪几只**标的（按名字长度降序匹配，处理「贵州茅台」包含「茅台」）。

    返回 [(名字, 代码)]，按出现顺序。**这是防"答的不是问的"的第一道闸。**
    """
    idx = _name_index()
    out: list[tuple[str, str]] = []
    rest = q
    # ★★ 6 位代码**也算提到了一只标的**。
    #    第一版只认名字 → 「600519和000858哪个好」**绕过了对比闸门**，
    #    静默只答 600519。独立验证实测命中 3/4 例。
    for m in re.finditer(r"(?<!\d)(\d{6})(?!\d)", q):
        out.append((m.group(1), m.group(1)))
        rest = rest.replace(m.group(1), "　")
    for nm in sorted(idx, key=len, reverse=True):
        if len(nm) < 2 or nm not in rest:
            continue
        cands = idx.get(nm) or []
        codes = {c for _m, c in cands}
        if len(codes) != 1:
            continue                      # 有歧义的名字不参与识别（名单里有重名的）
        out.append((nm, cands[0][1]))
        rest = rest.replace(nm, "　")     # 挖掉，避免「贵州茅台」又匹配出「茅台」
    return out


def parse(question: str) -> Query:
    """听懂问题。**认不出就抛 AskError —— 不猜标的、不猜意图。**"""
    q = (question or "").strip()
    if not q:
        raise AskError("问题为空")

    low = q.lower()

    # ────────────────────────────────────────────────────────────
    # ★★ 两道"防跑偏"闸门：**答的不是问的，比答错更糟**
    # ────────────────────────────────────────────────────────────
    # ① 对比问题：提到了 ≥2 只标的 —— 现在只支持单标的，**不许静默只答一只**
    mentions = _mentions(q)
    if len(mentions) > 1:
        raise AskError(
            "这是一个**对比问题**（提到了 "
            + "、".join(f"{n}({c})" for n, c in mentions)
            + f" 共 {len(mentions)} 只标的）。本系统现在**只支持单标的分析**，"
            "不会偷偷只回答其中一只 —— 请一次问一只。"
        )
    # ② 消息/事件问题 —— 情报通道还没接线，**不许返回一个空的别的类型简报**
    if any(k in q for k in _NEWS_KW) and not any(k in q for k in _FIN_KW + _QUOTE_KW):
        raise AskError(
            "这是问**消息/事件**的问题。我们的**情报通道（新闻/快讯/传闻）还没接线**，"
            "而且按架构铁律**情报永不进结论** —— 所以这条我现在答不了，"
            "不会拿一个空的大盘简报糊弄过去。"
            "（可以换问：某只票的行情、财务、估值分位，或大盘涨跌家数/成交额）"
        )

    # ── 标的：先找 6 位代码，再找名字 ──
    code, name = "", ""
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", q)
    if m:
        code = m.group(1)
    if not code:
        for nm in sorted(NAME2CODE, key=len, reverse=True):
            if nm in q:
                code, name = NAME2CODE[nm], nm
                break
    # ★ 认不出就走**全市场名单**（52k 行）—— 覆盖任意 A 股名字
    if not code:
        for nm in sorted(_universe()["by_name"], key=len, reverse=True):
            if nm and nm in q and len(nm) >= 2:
                _m, code = _lookup_stock(nm)
                name = nm
                break

    # ── 指数别名 ──
    idx = ""
    for nm in sorted(_INDEX_ALIAS, key=len, reverse=True):
        if nm in q:
            idx = _INDEX_ALIAS[nm]
            name = name or nm
            break

    # ── 类型判定（顺序有讲究：先具体后笼统）──
    is_market = any(k in q for k in _MARKET_KW)
    is_fin = any(k in q for k in _FIN_KW)
    is_quote = any(k in q for k in _QUOTE_KW)
    is_profile = any(k in q for k in _PROFILE_KW)

    if is_fin:
        qtype = Q_FINANCIAL
    elif is_quote and not is_market:
        qtype = Q_QUOTE
    elif is_market and not code:
        qtype = Q_MARKET
    elif is_profile:
        qtype = Q_PROFILE
    elif is_market:
        qtype = Q_MARKET
    elif code:
        qtype = Q_PROFILE
    else:
        # ★ 既没认出标的、也没有任何能识别的关键词 → **拒答**。
        #   原来的默认是 Q_MARKET，于是「这和问的是不是同一只」会拿到一个
        #   **只有 0 条事实的空大盘简报** —— 那是最坏的一种回答（看着像答了）。
        raise AskError(
            "我没听懂这个问题问的是什么，也**没认出标的**。"
            "本系统只会答这几类：某只票的行情 / 财务 / 估值分位 / 综合分析，"
            "或大盘的涨跌家数与成交额。**不会硬凑一个答案。**"
        )

    # ★★ “认不出”与“不是股票”是两件事：前者已经拒答，后者原来**放行了**
    #   （独立验证：ETF/指数/债券/B股 全部走个股通道，还给指数喊“看空 70%”）
    if code and qtype != Q_MARKET:
        istock, why = _is_tradable_stock(code)
        if not istock:
            raise AskError(f"【不是个股】{why}")

    if qtype != Q_MARKET and not code:
        raise AskError(
            f"我没认出你问的是哪只股票（关键词认出来的类型是「{qtype}」）。"
            "本系统**不会猜代码** —— 猜错标的是最严重的错。"
            "请给 6 位代码，或换一个我认得的名字。"
        )
    return Query(raw=q, qtype=qtype, code=code, name=name, index_code=idx)


# ══════════════════════════════════════════════════════════════════
# ⑤ 机械规则 —— 方向从哪来
# ══════════════════════════════════════════════════════════════════
@dataclass
class Rule:
    key: str
    stance: str          # BULL / BEAR
    text: str
    metric: str          # 引用的证据 metric


RULE_DISCLOSURE = (
    "⚠️ **这些规则的权重是人工设定的，没有做过实证校准。**\n"
    "    它们是「机械计数」，不是预测模型 —— 比分 3:1 只说明有 3 条规则站在这一边，\n"
    "    **不说明上涨概率是 75%**。本项目禁止把民间说法当信号，所以这里只用可算的事实。"
)


def _su_rule(cites) -> tuple[str, tuple]:
    """★★ **只吃依据的纯函数**：返回 (方向, 用到的 metric 列表)。

    这是消融能成立的前提：方向必须是**依据的函数**，
    去掉一条结果就可能变。本函数不读 bundle、不读网络、不读时间。

    ═════════════════════════════════════════════════════
    ★★★ 2026-10-13 重写：**把技术指标从方向里拿掉了**
    ═════════════════════════════════════════════════════
    旧版用 5 条手定规则（净利同比 / 价格vsMA60 / 回撒 / PE分位 / 两融）投票，
    其中 **MA60 与回撒是技术指标** —— 而本项目自己的设计文档明令：

    > 「技术指标（MACD/KDJ/RSI/BOLL）**不进打分**，只用于位置描述」
    > —— 因为未取得可信证据，不引用其结论。

    **所以旧版是「产品在违反自己的设计」。** 现在改成：

        方向 = 确定性打分器（judge/score.py），8 项等权，每项带 DOI，
               殃样从文献来，权重来源说谎就构造不出来。

    **但本函数仍然只能吃 cites** —— 所以把横截面百分位**做成事实**
    （`_collect_xsection` 里产出，带 eid），本函数再把它们变成 z 分。
    如果横截面取不到 → **方向为观望 + 声明原因**，不回退到技术指标。

    ★ 依据为空时抛 NoEvidenceError —— 让消融能识别出「删掉它结论就构造不出来」这一最强的一档。
    """
    if not cites:
        raise C.NoEvidenceError("没有依据，算不出方向")

    from decimal import Decimal as D, InvalidOperation
    by: dict = {}
    for c in cites:
        by.setdefault(c.evidence.metric, []).append(c)

    def val(m: str):
        lst = by.get(m) or []
        if not lst:
            return None
        try:
            return D(str(lst[0].evidence.value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return None

    # ── 横截面百分位 → 归一分 z = 2p − 1 ──
    PERC = (("EP分位", "EP"), ("BP分位", "BP"), ("换手率分位", "TURN20"))
    z, used = {}, []
    for label, key in PERC:
        p = val(label)
        if p is None:
            continue
        try:
            pf = float(p)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= pf <= 1.0):
            continue                     # 百分位必须在 [0,1]，不是就不用
        z[key] = 2.0 * pf - 1.0
        used.append(label)

    if not z:
        raise C.NoEvidenceError(
            "横截面百分位一个都没有 —— **不回退到技术指标**，直接给观望")

    # ── 走确定性打分器（权重来源写死在 judge/score.py）──
    from judge import score as SC
    res = SC.decide(code="【来自依据】", as_of="", scores=z)
    used += [k for k in z]
    return res.direction, tuple(dict.fromkeys(used))


def _rules(bundle: dict) -> tuple[list[Rule], list[Rule]]:
    """**给人看的**多空规则清单（与 `_su_rule` 判同一件事，只是带文案）。

    ⚠️ 方向本身以 `_su_rule` 为准 —— 那个是依据的函数，这个只负责解释。
    """
    bull: list[Rule] = []
    bear: list[Rule] = []

    yoy = bundle.get("归母净利润同比")
    if yoy is not None:
        if yoy > 0:
            bull.append(Rule("fin_yoy", BULL,
                             f"归母净利润同比为正（{yoy*100:+.2f}%，"
                             "本期与上年同期两个数都取自财报、均可回溯）",
                             "归属于母公司所有者的净利润"))
        else:
            bear.append(Rule("fin_yoy", BEAR,
                             f"归母净利润同比为负（{yoy*100:+.2f}%，"
                             "本期与上年同期两个数都取自财报、均可回溯）",
                             "归属于母公司所有者的净利润"))

    px, ma60 = bundle.get("close"), bundle.get("ma60")
    if px is not None and ma60 is not None:
        if px > ma60:
            bull.append(Rule("above_ma60", BULL,
                             f"价格在 60 日均线上方（{ma60:.2f}）", "最新价"))
        else:
            bear.append(Rule("below_ma60", BEAR,
                             f"价格在 60 日均线下方（{ma60:.2f}）", "最新价"))

    dd = bundle.get("距250日高点回撤%")
    if dd is not None and dd >= 20:
        bear.append(Rule("drawdown", BEAR, f"距 250 日高点回撤 {dd:.1f}%", "最新价"))

    mc = bundle.get("_margin_chg_20d")
    if mc is not None:
        yi = mc / 1e8
        if yi > 1:
            bull.append(Rule("margin_in", BULL,
                             f"近 20 日融资余额增加 **{yi:.2f} 亿**（杠杆资金在进）",
                             "融资余额"))
        elif yi < -1:
            bear.append(Rule("margin_out", BEAR,
                             f"近 20 日融资余额减少 **{abs(yi):.2f} 亿**（杠杆资金在退）",
                             "融资余额"))

    pe_pct = bundle.get("_市盈率TTM_pct")
    if pe_pct is not None:
        if pe_pct <= 30:
            bull.append(Rule("valuation_low", BULL,
                             f"PE(TTM) 处于自身近三年 **{pe_pct:.1f}% 分位**（偏低）",
                             "市盈率TTM历史分位"))
        elif pe_pct >= 70:
            bear.append(Rule("valuation_high", BEAR,
                             f"PE(TTM) 处于自身近三年 **{pe_pct:.1f}% 分位**（偏高）",
                             "市盈率TTM历史分位"))

    return bull, bear


# ══════════════════════════════════════════════════════════════════
# 简报
# ══════════════════════════════════════════════════════════════════
@dataclass
class Brief:
    q: Query
    facts: list[Fact] = field(default_factory=list)
    market: dict = field(default_factory=dict)
    bundle: dict = field(default_factory=dict)
    bull: list[Rule] = field(default_factory=list)
    bear: list[Rule] = field(default_factory=list)
    claim: Optional[C.Claim] = None
    bear_scan: Any = None
    ablation: Any = None
    margin: Any = None
    tieout: Any = None
    intel: Any = None
    minefield: Any = None
    events: Any = None
    xsection_meta: Any = None
    score_result: Any = None
    kinds_seen: set = field(default_factory=set)   # ★ 已成功取到的数据 kind（三态判定用）
    gaps: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    indicators_caliber: dict = field(default_factory=dict)
    generated_at: str = ""

    # ------------------------------------------------------------------
    def render(self) -> str:
        L: list[str] = []
        L.append("═" * 74)
        L.append(f"问题：{self.q.raw}")
        L.append(f"类型：{self.q.qtype}"
                 + (f"｜标的：{self.q.code} {self.q.name}" if self.q.code else ""))
        L.append(f"生成时间：{self.generated_at}")
        L.append("═" * 74)

        if self.notices:
            L.append("")
            L.append("【必须说明】")
            for n in self.notices:
                L.append(f"  ⚠️ {n}")

        if self.facts:
            L.append("")
            L.append("【事实】（每个数字都能走回原始数据包）")
            for f in self.facts:
                L.append("  · " + f.line())

        if self.market:
            L.append("")
            L.append("【大盘】　（来源：通达信盘后包 kind=tdx_package，"
                     "**整块一个来源、不逐项发证据号**）")
            for k, v in self.market.items():
                L.append(f"  · {k}：{v}")

        if self.bundle:
            L.append("")
            # ★ 独立验证统计：这两段的数字原来在渲染里看不见证据号。
            #   修法不是硬塞号（它们确实没有单独的原始字段），而是**如实说明证据在哪** ——
            #   把"看不出来"变成"写清楚了"。本项目最忌讳的就是让读者以为验过了。
            L.append("【算出来的指标】　（**由上面【事实】里的点值算出**，"
                     "不单独发证据号；点值的事实号见【事实】与【溯源自证】）")
            for k, v in self.bundle.items():
                if k.startswith("_"):
                    continue                      # 内部用的原始值，不给人看
                L.append(f"  · {k}：{v}")

        if self.score_result is not None:
            # ★★★ 方向由**确定性打分器**定，所以**显示也必须是打分器**。
            #   第一版只改了判定没改显示：结论说「观望」（打分器给的），
            #   而这里还写着「看多 1 vs 看空 2」（旧的手定规则给的）——
            #   **显示与判定不一致，比两个都错更坏。**
            L.append("")
            L.append("【方向判定】（确定性打分器 · **不是机械计数**）")
            L.append(self._indent(self.score_result.render(), "  "))
        elif self.bull or self.bear:
            L.append("")
            L.append("【多空对峙】（机械规则计数，不是预测）")
            L.append(f"  看多 {len(self.bull)} 条：")
            for r in self.bull:
                L.append(f"    + {r.text}")
            L.append(f"  看空 {len(self.bear)} 条：")
            for r in self.bear:
                L.append(f"    - {r.text}")
            L.append("  " + RULE_DISCLOSURE.replace("\n", "\n  "))

        if self.bear_scan is not None:
            L.append("")
            L.append("【看空触发条件】（法条与经验阈值**分开标注**，不混为一谈）")
            L.append(self._indent(self.bear_scan.report(), "  "))

        if self.claim is not None:
            L.append("")
            L.append("【结论】")
            L.append(self._indent(self.claim.render(with_evidence=False), "  "))

        if self.margin is not None:
            L.append("")
            L.append("【资金面·两融】（本系统**唯一能进结论**的资金流指标）")
            L.append(self._indent(self.margin.render(), "  "))
        if self.tieout:
            L.append("")
            n_ok = sum(1 for r in self.tieout if r.verdict == "PASS")
            n_f = sum(1 for r in self.tieout if r.verdict == "FAIL")
            L.append(f"【三表勾稽】（**只做能证明自己没错的那几条**；"
                     f"共 {len(self.tieout)} 条，PASS {n_ok}、FAIL {n_f}）")
            for r in self.tieout:
                L.append("  " + r.render().replace("\n", "\n  "))

        if self.minefield is not None:
            L.append("")
            L.append(self.minefield.render())

        if self.events is not None:
            L.append("")
            L.append(self.events.render())

        # ★ 情报层 —— 三类分档。**与结论物理隔开**，且自带免责。
        if self.intel and self.intel.get("representatives"):
            from data import intel as IN
            L.append("")
            L.append(IN.build_class_section(self.intel["representatives"]))

        if self.ablation is not None:
            L.append("")
            L.append("【脆弱性】（去掉一条依据，结论会不会翻）")
            L.append(self._indent(self.ablation.render(), "  "))

        if self.gaps:
            L.append("")
            L.append("【没取到的】（**不填空**，如实列出）")
            for g in self.gaps:
                L.append(f"  ✗ {g}")

        # ── 追溯能力自证：★ 逐条回溯，**不许抽 1 条替全证** ──
        #    独立验证实测指出：原来只抽"第一条能解析的"（永远是 quote.price），
        #    文末一个 ✅ 读起来像全量都验过了。**那是自证替全证。**
        t = self.trace_all()
        if t:
            L.append("")
            L.append("【溯源自证】" + t)

        # ── ★★ 输出契约自检：该说的话是不是**真的出现在正文里** ──
        #    内部知道 ≠ 输出里说了。"内部诚实、输出骗人"是最危险的那种失败。
        L.append("")
        L.append("【输出契约自检】")
        try:
            from judge import contract as CT
            ctx = CT.OutputContext(
                text="\n".join(L),
                claim_flags=dict(self.claim.flags) if self.claim else {},
                gaps=list(self.gaps),
                stale_notes=[f.stale_note for f in self.facts if f.stale_note],
                conflicts=[n for n in self.notices
                           if ("不一致" in n or "冲突" in n or "对不上" in n)],
                bear_unknown=(self.bear_scan.n_unknown if self.bear_scan else 0),
                bear_hits=(len(self.bear_scan.hits) if self.bear_scan else 0),
                evidence_ids=(self.claim.evidence_ids if self.claim else []),
                direction=(self.claim.direction if self.claim else ""),
                invalidators=(self.claim.invalidators if self.claim else []),
                caliber=(self.claim.caliber if self.claim else ""),
                n_facts=len(self.facts),
            )
            L.append(self._indent(CT.report(CT.check(ctx)), "  "))
        except Exception as e:                              # noqa: BLE001
            L.append(f"  ⚠️ 自检本身出错（这不算通过）：{type(e).__name__}: {e}")
        return "\n".join(L)

    @staticmethod
    def _indent(text: str, pad: str) -> str:
        return "\n".join(pad + ln for ln in text.split("\n"))

    # ------------------------------------------------------------------
    def _results(self) -> dict:
        return getattr(self, "_res_map", {}) or {}

    def trace_all(self) -> str:
        """★ **逐条**走回原始包核对，并如实报覆盖率。

        不是抽一条 —— 抽一条给一个 ✅，读起来像全量都验过了，**那是自证替全证**。

        分三类如实报：
          · 可回溯（有原始包指纹 + 字段路径）→ **逐条真的走一遍**
          · 计算类指标（没有 payload 字段，是从行情算出来的）→ 说清它属于哪一类
          · 拿不到原始包的 → 明说
        """
        res_map = self._results()
        ok_n = bad_n = 0
        bad: list[str] = []
        computed: list[str] = []
        for f in self.facts:
            if not f.path:
                computed.append(f.label)
                continue
            r = res_map.get(f.origin)
            if r is None:
                bad.append(f"{f.label}（原始包不在本次会话里）")
                bad_n += 1
                continue
            ev = Evidence(company=str(getattr(r, "code", "")),
                          period=f.as_of, metric=f.label,
                          value=f.value.replace(",", ""),
                          unit=f.unit or "—", source=f.source,
                          caliber=f.caliber or "—",
                          origin_eid=f.origin, origin_path=f.path)
            good, why = trace(ev, r)
            if good:
                ok_n += 1
            else:
                bad.append(f"{f.label}：{why}")
                bad_n += 1
        total = ok_n + bad_n + len(computed)
        L = [f"逐条回溯 **{ok_n}/{total}** 条事实被现场走回原始包核对通过；"
             f"其余 {len(computed)} 条是**算出来的指标**（没有原始字段可回，"
             f"但它们的输入来自上面那些可回溯的事实）。"]
        if bad:
            L.append(f"  ❌ **{bad_n} 条没走回去**：")
            for b in bad:
                L.append(f"    - {b}")
        else:
            L.append("  ✅ 所有**点值事实**（有原始字段的那些）全部回溯通过。")
        return "\n".join(L)

    def trace_one(self) -> str:
        """（保留旧名字，等价于 trace_all —— 不许再抽 1 条替全证。）"""
        return self.trace_all()


# ══════════════════════════════════════════════════════════════════
# 各类型的执行
# ══════════════════════════════════════════════════════════════════
def _collect_quote(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    res = _get("quote", q.code, log=log)
    if res is None:
        return
    rows = res.data or []
    if not rows:
        return
    if isinstance(rows, list):
        rows = rows[0]
    res_map[getattr(res, "eid", "")] = res
    brief.kinds_seen.add(str(getattr(res, "kind", "")))
    if not q.name and isinstance(rows, dict):
        q.name = str(rows.get("name") or rows.get("名称") or "")
    pre = _row_prefix(res)
    specs = [("最新价", "price", "元"), ("涨跌幅", "change_pct", "%"),
             ("市盈率TTM", "pe_ttm", "倍"), ("市净率", "pb", "倍"),
             ("换手率", "turnover_pct", "%"), ("总市值", "mcap_yi", "亿元"),
             ("涨停价", "limit_up", "元"), ("跌停价", "limit_down", "元")]
    skipped: list[str] = []
    for label, path, unit in specs:
        f = _fact_from(res, metric=label, path=pre + path, unit=unit, caliber="实时快照")
        if f is None:
            continue
        try:
            fv = float(str(f.value).replace(",", ""))
        except (TypeError, ValueError):
            brief.facts.append(f)
            continue
        okp, whyp = _plausible(label, fv)
        if not okp:
            skipped.append(whyp)          # ★ 哨兵值不能当事实
            continue
        brief.facts.append(f)
    if skipped:
        brief.notices.append(
            "⚠️ 以下字段上游给的是**不适用哨兵值**（不是数据），已剔除、"
            "**不当事实用、也不参与结论**：" + "；".join(skipped))
    if rows.get("is_stale"):
        brief.notices.append(
            "⚠️ 行情源把这条报价标为**僵尸报价**（退市/停牌股照样返回数据）"
            " —— 这个价格不能当现价用。")

    # ★★ 第二个独立来源对拍：腾讯实时接口 vs 通达信盘后包
    f2 = _second_source_close(q.code)
    if f2 is not None:
        brief.facts.append(f2)
        p1 = _dec_safe(next((f.value for f in brief.facts if f.label == "最新价"), None))
        p2 = _dec_safe(f2.value)
        if p1 is not None and p2 is not None:
            if p1 == p2:
                brief.notices.append(
                    f"✅ **收盘价被两个独立来源印証一致**：腾讯实时接口 {p1} 元、"
                    f"通达信盘后包 {p2} 元 —— 两家的实现互不认识，给出同一个数。")
            else:
                # ★★★ 这里必须分辨两种完全不同的情况（实测踩到）：
                #
                #   【源分歧】两家对**同一天同一个价**给出不同数字 → 我们不知道谁对
                #   【时点差】一家给昨天收盘、一家给今天实时 → **两者都对**
                #
                #   实测例子（南华生物 000504）：
                #       通达信 11.35（10-12 收盘） vs 腾讯 10.81（10-13 实时）
                #       系统报「两个来源的价不一致」—— 但 11.35×(1−4.76%) = 10.81，
                #       **4.76% 正是系统自己算出的当日涨跌幅**。
                #       那不是源打架，是今天跌了 4.76%。
                #
                #   把时点差说成源冲突，是把一件**正常的事**说成**异常的事** ——
                #   用户第一次用就撞上它，学到的第一件事是「这系统会搞错」。
                today = str(next((f.as_of for f in brief.facts if f.label == "最新价"), "") or "")
                tdx_date = str(f2.as_of or "")
                chg = _dec_safe(next((f.value for f in brief.facts if f.label == "涨跌幅"), None))
                as_time_gap = False
                # ★ 判定逻辑抽成了纯函数 `_is_time_gap` —— 这样它**可以被单测**。
                #   第一版把它写在流程里 + 一个 `except Exception`，
                #   结果 `Decimal` 没在模块级 import（只在函数里局部 import 过），
                #   `Decimal("1")` 抛 NameError → 被那个宽 except 吞掉 →
                #   **时点差判定永远不生效，而输出看起来完全正常**。
                #   这就是静默失败：bug 伪装成了「功能没触发」。
                if _is_time_gap(p1, p2, chg, today, tdx_date):
                    as_time_gap = True
                    brief.notices.append(
                        f"ℹ️ **两个来源的价不同，但这是「时点差」不是「源分歧」**："
                        f"腾讯 {p1} 元是 **{today}** 的实时价，"
                        f"通达信 {p2} 元是 **{tdx_date}** 的收盘价 ——"
                        f" 两者之差（{(p1/p2-1)*100:+.2f}%）与当日涨跌幅"
                        f"（{chg:+.2f}%）一致，**两个数都对**。")
                if not as_time_gap:
                    brief.notices.append(
                        f"⚠️ **两个来源的价不一致**：腾讯 {p1}（{today or '日期未知'}）"
                        f" vs 通达信 {p2}（{tdx_date or '日期未知'}）"
                        " —— 按纪律**只报分歧，不调和**；请以交易所行情为准。")


def _is_time_gap(p1, p2, chg, d1: str, d2: str,
                 tol: "Decimal" = Decimal("0.15")) -> bool:
    """★ 两个来源的价不同 —— 是「时点差」还是「源分歧」？

    实测踩出来的区分（南华生物 000504）：
        通达信 11.35（10-12 收盘） vs 腾讯 10.81（10-13 实时）
        第一版报「**两个来源的价不一致**」—— 读起来像「数据源打架了」。
        但 11.35×(1−4.76%) = 10.81，**4.76% 正是系统自己算出的当日涨跌幅**。
        所以不是源打架，是**今天跌了 4.76%，两个数都对**。

    判据：**日期不同** 且 **两个价之比对应的涨跌幅 ≈ 当日涨跌幅**（容差 0.15 个百分点）。
    """
    if p1 is None or p2 is None or chg is None:
        return False
    if not d1 or not d2 or d1 == d2:
        return False
    if p2 == 0:
        return False
    try:
        implied = (p1 / p2 - Decimal("1")) * Decimal("100")
        return abs(implied - chg) < tol
    except (InvalidOperation, ZeroDivisionError, ValueError, TypeError):
        return False


def _dec_safe(x: Any):
    try:
        from decimal import Decimal
        return Decimal(str(x).replace(",", "").strip()) if x is not None else None
    except Exception:                                       # noqa: BLE001
        return None


def _collect_financial(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    res = _get("financial_sina", q.code, log=log)
    if res is None:
        return
    rows = res.data or []
    if not rows:
        return
    res_map[getattr(res, "eid", "")] = res
    brief.kinds_seen.add(str(getattr(res, "kind", "")))
    # ★★ 先把公告原文拿到手（本机没有就去巨潮下）—— 财务数字要拿它逐字核对
    pdf_path, _pdf_title = _find_pdf(q.code), ""
    if not pdf_path:
        pdf_path, _pdf_title = _download_report_pdf(q.code, brief, log)
    # 取最新一期
    def _key(r):
        return str(r.get("报告期", ""))
    latest = sorted(rows, key=_key)[-1]
    idx = rows.index(latest)
    base = f"{idx}."
    # ★★ 口径必须分清：净利润（合并） vs 归属净利润，差 15 亿
    specs = [
        ("归属于母公司所有者的净利润", "归属于母公司所有者的净利润", "元", "net_profit_parent"),
        ("净利润", "净利润", "元", "net_profit"),
        ("营业总收入", "营业总收入", "元", "operating_revenue_total"),
        ("营业收入", "营业收入", "元", "operating_revenue"),
        ("基本每股收益", "基本每股收益", "元/股", "eps"),
    ]
    got_any = False
    for label, path, unit, cal in specs:
        f = _fact_from(res, metric=label, path=base + path, unit=unit, caliber=cal)
        if f is None:
            continue
        # ★★ 逐字回公告原文核对（本机预存的，或刚从巨潮下来的）
        cc = _cross_check_pdf(q.code, f.value, cal, pdf_path=pdf_path)
        if cc:
            f.note = f"公告原文核对：{cc}"
        got_any = True
        brief.facts.append(f)

    if not pdf_path:
        brief.gaps.append(
            "**没能拿到该标的的公告原文 → 未做「回原文逐字核对」**。"
            "（这是豆包过不去的那一关；取不到时如实说明，不假装核对过）")

    # ★ 口径提示：这是豆包不会说的那句话
    if got_any:
        # ⚠️ 这个差额**必须现算**。第一版写死了「差 15.16 亿」——
        #    那是**茅台**的数，对宁德时代（实际差 37.47 亿）就是**在输出里印一个错数字**。
        #    实测踩到（用户当场问「宁德时代」时暴露）。
        _gap = ""
        try:
            _np = float(latest.get("净利润") or 0)
            _npp = float(latest.get("归属于母公司所有者的净利润") or 0)
            if _np and _npp:
                _gap = f"**{abs(_np - _npp)/1e8:.2f} 亿**"
        except (TypeError, ValueError):
            _gap = "一笔钱"
        brief.notices.append(
            f"★ 财报里**两个都叫「净利润」的数差 {_gap}**："
            "合并净利润（含少数股东损益）vs 归属于母公司股东的净利润（股东实际享有的）。"
            "中国媒体说「净利润」通常指后者，而不少数据接口返回的是前者。"
            "上面**两个都列出来了，并标了口径** —— 用哪个必须说清。")

    # ★★ 金融企业陷阱（实测踩到）：**营业收入 ≠ 营业总收入**，差得很离谱
    #    东方财富 2026H1：营业收入 22.80 亿，但净利润 80.64 亿 —— 看着荒谬。
    #    真相：券商/银行/保险的「营业收入」只是**手续费及佣金**那一块，
    #    大头在「营业总收入」里（东方财富是 105.05 亿）和投资收益里。
    #    **只报一个数就会得出"利润比收入还多"的荒唐结论。**
    try:
        _rt, _rv = latest.get("营业总收入"), latest.get("营业收入")
        if _rt and _rv and float(_rv) > 0 and float(_rt) / float(_rv) > 1.2:
            brief.notices.append(
                f"★★ **营业收入 ≠ 营业总收入，这家差得很大**：营业收入 {float(_rv)/1e8:.2f} 亿"
                f" vs 营业总收入 {float(_rt)/1e8:.2f} 亿（差 {float(_rt)/float(_rv)-1:.0%}）。"
                "**金融类企业（券商/银行/保险）尤其明显** —— 它们的「营业收入」通常只含"
                "手续费及佣金，利息收入与投资收益在「营业总收入」里。"
                "**只看「营业收入」会得出「利润比收入还多」的荒唐结论**，两个都必须报。")
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    yoy = latest.get("归属于母公司所有者的净利润_同比")

    # ★★ 上期同列一起取出来 —— 这样「同比」可以被**两条独立路径**验证：
    #    路径 A：我们自己用 (本期 − 上期) / 上期 算
    #    路径 B：接口直接给的 _同比 字段
    #    两者对上 = 这个数字有两个独立来源；对不上 = 有问题，必须报出来。
    f_prev = None
    if idx >= 1:
        f_prev = _fact_from(res, metric="归属于母公司所有者的净利润（上年同期）",
                            path=f"{idx-1}.归属于母公司所有者的净利润",
                            unit="元", caliber="net_profit_parent")
        if f_prev is not None:
            brief.facts.append(f_prev)

    cur_f = next((f for f in brief.facts
                  if f.label == "归属于母公司所有者的净利润"), None)
    if cur_f is not None and f_prev is not None:
        try:
            from decimal import Decimal
            c = Decimal(cur_f.value.replace(",", ""))
            p = Decimal(f_prev.value.replace(",", ""))
            done = (c - p) / p
            brief.bundle["归母净利润同比（自算）"] = f"{done*100:+.2f}%"
            brief.bundle["_yoy_calc"] = float(done)
            if yoy is not None:
                diff = abs(float(done) - float(yoy))
                brief.bundle["归母净利润同比（接口给的）"] = f"{float(yoy)*100:+.2f}%"
                brief.bundle["_yoy_raw"] = float(yoy)
                if diff < 1e-4:
                    brief.notices.append(
                        f"✅ **同比数字被两条独立路径验证一致**："
                        f"自己用两个报告期算得 {float(done)*100:+.2f}%，"
                        f"接口给的同比字段是 {float(yoy)*100:+.2f}% —— 互相印证。")
                else:
                    brief.notices.append(
                        f"⚠️ **同比数字两条路径对不上**：自算 {float(done)*100:+.2f}% vs "
                        f"接口 {float(yoy)*100:+.2f}%（差 {diff*100:.4f} 个百分点）"
                        " —— 按纪律**只报分歧，不调和**，请以能回公告原文核对的那条为准。")
        except Exception:                                   # noqa: BLE001
            pass
    elif yoy is not None:
        brief.bundle["归母净利润同比（接口给的）"] = f"{float(yoy)*100:+.2f}%"
        brief.bundle["_yoy_raw"] = float(yoy)
    if not got_any:
        brief.gaps.append("financial_sina 取到了包，但一个财务字段都没解析出来")


def _collect_market(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    from compute.breadth import market_snapshot
    import contextlib
    import io
    try:
        # 关掉第三方库的进度条（会打印到 stdout 污染简报）
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            b = market_snapshot()
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"市场宽度算不出来：{e}")
        return
    d = b.to_dict()
    brief.market["交易日"] = d.get("日期")
    brief.market["成交额（两市）"] = f"{_f2(d.get('成交额_亿'))} 亿元"
    brief.market["　其中沪市"] = f"{_f2(d.get('其中沪市_亿'))} 亿元"
    brief.market["　其中深市"] = f"{_f2(d.get('其中深市_亿'))} 亿元"
    brief.market["上涨 / 下跌 / 平盘"] = f"{d.get('上涨')} / {d.get('下跌')} / {d.get('平盘')}"
    lu = d.get("涨停明细") or {}
    brief.market["涨停"] = (f"{d.get('涨停')} 只"
                          f"（主板 {lu.get('主板_10%')} · 双创 {lu.get('创业板科创板_20%')} · "
                          f"ST {lu.get('ST_5%')} · 北交所 {lu.get('北交所_30%')}）")
    brief.market["跌停"] = f"{d.get('跌停')} 只"
    if d.get("封板率") is not None:
        brief.market["封板率"] = f"{float(d['封板率'])*100:.1f}%"
    for k, v in (d.get("指数") or {}).items():
        if isinstance(v, dict):
            brief.market[f"指数·{k}"] = f"{_f2(v.get('收盘'))}（成交额 {_f2(v.get('成交额_亿'))} 亿元）"
        else:
            brief.market[f"指数·{k}"] = str(v)
    if d.get("口径"):
        brief.indicators_caliber["市场宽度"] = d["口径"]
    for n in (d.get("提示") or []):
        brief.notices.append(n)


def _collect_valuation(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """估值分位 —— "贵不贵"要靠它，光看 PE 绝对值没有意义（跨行业不可比）。

    ★★ 三条纪律（调研得出，都是会静默出错的地方）：
      1. **负 PE 必须标「不适用」** —— 亏损股的 PE 是负数，排分位毫无意义，
         而且会把它算成"史上最便宜"（负数是分位最低的）。
      2. 分位只在**同一个标的历史内**算，不跨标的、不跨行业。
      3. 历史值必须是**正的** PE 才进排名样本。
    """
    res = _get("valuation_history", q.code, log=log)
    if res is None:
        return
    rows = [r for r in (res.data or []) if isinstance(r, dict)]
    if len(rows) < 60:
        brief.gaps.append(f"估值历史只有 {len(rows)} 条，太短，不出分位")
        return
    res_map[getattr(res, "eid", "")] = res
    brief.kinds_seen.add("valuation_history")
    rows.sort(key=lambda r: str(r.get("date", "")))
    latest = rows[-1]
    d0, d1 = str(rows[0].get("date", ""))[:10], str(latest.get("date", ""))[:10]
    win_note = f"{d0} ~ {d1}（{len(rows)} 个交易日）"

    for label, key, unit in (("市盈率TTM", "peTTM", "倍"), ("市净率", "pbMRQ", "倍"),
                             ("市销率TTM", "psTTM", "倍")):
        cur = latest.get(key)
        if cur is None:
            continue
        try:
            curf = float(cur)
        except (TypeError, ValueError):
            continue
        vals = []
        for r in rows:
            v = r.get(key)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v > 0:                       # ★ 只拿正数进排名（负数是亏损，不可比）
                vals.append(v)
        if label == "市盈率TTM" and curf <= 0:
            brief.notices.append(
                f"⚠️ **PE(TTM) = {curf}（为负）→ 标「不适用」**：公司处于亏损，"
                "负 PE 排分位会得出「史上最便宜」这种反的结论，所以本项不参与。")
            continue
        if not vals or curf <= 0:
            continue
        below = sum(1 for v in vals if v <= curf)
        pct = below / len(vals) * 100
        brief.bundle[f"{label} 分位"] = (
            f"{pct:.1f}%（当前 {curf:g}，样本 {len(vals)} 个正值）")
        brief.bundle[f"_{label}_pct"] = pct
        brief.facts.append(_computed_fact(
            q, f"{label}历史分位", f"{pct:.1f}", unit="%",
            source=str(getattr(res, "source", "")), kind="valuation_history",
            as_of=d1, caliber=f"在自身 {win_note} 内的正数值分位",
            origin=str(getattr(res, "eid", "")),
            note=f"当前 {curf:g} 倍，窗口 {win_note}"))

    # ★ 与行情快照的 PE 对拍（两个独立来源）
    qpe = next((f for f in brief.facts if f.label == "市盈率TTM" and f.kind == "quote"), None)
    if qpe is not None and latest.get("peTTM") is not None:
        a, b = _dec_safe(qpe.value), _dec_safe(latest.get("peTTM"))
        if a and b:
            rel = abs(a - b) / b * 100
            if rel < 3:
                brief.notices.append(
                    f"✅ **市盈率被两个独立来源印証**：行情快照 {a} 倍 vs 估值历史 {b} 倍"
                    f"（差 {rel:.2f}%，属正常时点差）")
            else:
                brief.notices.append(
                    f"⚠️ **两个来源的市盈率差 {rel:.2f}%**：快照 {a} vs 历史 {b}"
                    " —— 只报分歧，不调和。")


def _collect_intel(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """情报层 —— 三类分档。

    ★★★ 它**不进 facts、不参与方向** —— 这条由
        `data/selftest_intel_class.py` 里的结构断言守着（AST 扫 `_su_rule`）。

    它只回答「市场上在说什么」，不回答「这消息是真是假」。

    为什么要这么严（硬数据）：
        2026-05 一篇论文实测：最好的前沿模型做深度研究时，
        **链接有效性 94%+、相关性 80%+，但事实准确率只有 39~77%**；
        且工具调用从 2 次增至 150 次，事实核查准确率**平均下降 42%**。
        —— 检索得多，不等于更准。
    """
    if not q.code:
        return
    try:
        from data.sources.news import stock_news
        from data import intel as IN
        items, src, asof = stock_news(q.code, 20, q.name or "")
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"情报未取到：{type(e).__name__}: {str(e)[:70]}")
        return
    if not items:
        brief.gaps.append("情报通道本次没有相关新闻（这是结果，不是故障）")
        return
    try:
        res = IN.process(items)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"情报加工失败：{type(e).__name__}: {str(e)[:70]}")
        return
    brief.intel = res
    brief.kinds_seen.add("news")


def _collect_xsection(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """横截面百分位 —— **打分器要的就是这三个数**。

    ★ 带来的变化：方向从「5 条手定规则投票」换成「确定性打分器」，
      而且 **技术指标（MA60/回撒）不再产生方向分** ——
      这是本项目自己的设计文档明令要求的，之前一直违反着。

    ★★ 只做 3 项（EP / BP / TURN20），剩下 5 项**如实标未实现**：
        IVOL / RESMOM —— 需因子回归
        ILLIQ / REV20  —— 需逐只日线（取数太慢）
        FSCORE         —— Piotroski 原文未取得，**按规矩不许凭记忆写 9 条判据**
      打分器本来就能处理缺项：缺的进 `excluded` 并声明。
    """
    if not q.code:
        return
    try:
        from compute import xsection as XS
        xs = XS.load()
        row = XS.find(xs, q.code)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"横截面未取到（**这意味着方向算不出）**："
                          f"{type(e).__name__}: {str(e)[:60]}")
        return
    if row is None:
        brief.gaps.append("横截面里没有这只票（可能是新股/停牌）—— **方向算不出**")
        return
    brief.xsection_meta = {"as_of": xs.as_of, "source": xs.source,
                           "industry": row.industry, "bucket": xs.bucket_of(row.mktcap)}
    specs = [
        ("EP分位", (1.0 / row.pe) if row.pe else None,
         lambda r: (1.0 / r.pe) if r.pe else None, "归母净利润 TTM / 总市值"),
        ("BP分位", (1.0 / row.pb) if row.pb else None,
         lambda r: (1.0 / r.pb) if r.pb else None, "归母净资产 / 总市值"),
        ("换手率分位", row.turnover, lambda r: r.turnover, "近期换手率"),
    ]
    for label, val, getter, desc in specs:
        pct, level = xs.percentile(val, getter, industry=row.industry, mktcap=row.mktcap)
        if pct is None:
            brief.gaps.append(f"{label}：{level}")
            continue
        ev = Evidence(company=f"{q.code} {q.name}".strip(), period=xs.as_of,
                      metric=label, value=f"{pct:.6f}", unit="百分位",
                      source="eastmoney clist",
                      caliber=f"中位秩法；{level}（{desc}）")
        brief.facts.append(Fact(label=label, value=f"{pct:.4f}", unit="百分位",
                                source="eastmoney clist", kind="xsection", as_of=xs.as_of,
                                caliber=f"{level}", eid=ev.eid, origin="", path="",
                                note=f"{desc}；对比口径 {level}"))
    brief.kinds_seen.add("xsection")


def _collect_events(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """事件雷达 —— **只报告「这里发生了不寻常的事」，不判断它是机会还是陷阱**。"""
    if not q.code:
        return
    try:
        from compute import events as EV
        brief.events = EV.run(q.code)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"事件雷达未跑：{type(e).__name__}: {str(e)[:70]}")


def _collect_minefield(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """踩雷排查清单 —— 一次把已知的雷全扫一遍。

    它解决的是最冤的一类亏损：**踩到本来就公开披露、只是没去看的雷**。

    ★★ 三态铁律：命中 / 未发现 / **无法判定**。
       最后一个**绝不等于「没问题」** —— 它只是说「这一项本次没查」。
    """
    if not q.code:
        return
    try:
        from compute import minefield as MF
        rep = MF.run(q.code)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"踩雷排查未跑：{type(e).__name__}: {str(e)[:70]}")
        return
    brief.minefield = rep
    brief.kinds_seen.add("minefield")


def _collect_tieout(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """三表勾稽核对 —— **只做"能证明自己没错"的那几条**。

    依据：调研对 35 只 A 股（全部标准无保留意见的干净公司）实测了每条恒等式的**误报率**：
        A1 资产=负债+权益                0%      → 可判 FAIL
        A2 现金净增加额=经营+投资+筹资+汇率   0%      → 可判 FAIL
        A3 期末=期初+净增加               0%      → 可判 FAIL
        B2 间接法勾稽                     83~97%  → **绝不可判 FAIL**
        A5 未分配利润滚动                  ≈93%    → **绝不可判 FAIL**
        C1 收入-现金桥                    20~32%  → **绝不可判 FAIL**

    ★ 被测样本是干净公司 → 判"不平"就是冤枉好人。
      `compute/tieout.py` 在**代码层**禁止后三条判 FAIL（写 FAIL 会被拦成 WARN）。
    """
    if q.qtype not in ("个股分析", "财务") or not q.code:
        return
    try:
        from compute import tieout as TO
        mk = _market_of(q.code)
        code_sh = f"{mk.upper()}{q.code}"
        # 取最近两个年报期（上一期用于 A5/C1）——拿不到就只跑当期的四条
        cur = "2025-12-31"
        prev = "2024-12-31"
        results = TO.run_all(code_sh, cur, prev_period=prev)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"三表勾稽未跑：{type(e).__name__}: {str(e)[:80]}")
        return
    if not results:
        brief.gaps.append("三表勾稽未跑：没有返回任何结果")
        return
    brief.tieout = results
    brief.kinds_seen.add("tieout")
    # 给每条 PASS 的规则建事实（带证据号）
    for r in results:
        if r.verdict != "PASS":
            continue
        val = f"{r.residual!r}"
        ev = Evidence(company=f"{q.code} {q.name}".strip(), period=r.period,
                      metric=f"勾稽·{r.rule_id}", value=val, unit="元",
                      source="emdc_f10", caliber=f"{r.caliber}报表")
        brief.facts.append(Fact(
            label=f"勾稽核对·{r.rule_id}（{r.rule_id == 'A1' and '资产=负债+权益' or r.rule_id == 'A2' and '现金净增加额闭合' or '期末=期初+净增加'}）",
            value=val, unit="元", source="emdc_f10", kind="tieout",
            as_of=r.period, caliber=f"{r.caliber}报表",
            eid=ev.eid, origin="", path="",
            note=f"残差 {r.residual!r}，容差 {r.tolerance:g}；该判据实测误报率 "
                 f"{r.false_positive_rate_measured:.0%}（{r.false_positive_sample}）"))


def _collect_margin(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """两融 —— **本系统唯一能进结论的资金流指标**（因为是"可复算量"）。

    调研结论：东财「主力资金流」是厂商黑箱、无第二来源、实证符号矛盾 → 不进结论；
    「北向净流入」字段**已不存在**；只有两融是交易所每日披露、且有**官方会计恒等式**：

        本日融资余额 = 前日融资余额 + 本日融资买入额 − 本日融资偿还额

    → 所以它既可复算，又能**拿交易所的定义反过来校验数据源**（见 `margin.verify_identity`）。
    """
    try:
        from compute import margin as MG
        rep = MG.load(q.code)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"两融模块异常：{type(e).__name__}: {e}")
        return
    brief.margin = rep
    if rep.error or not rep.rows:
        brief.gaps.append(f"两融取不到：{rep.error or '没有数据'}")
        return
    brief.kinds_seen.add("margin_trading")
    last = rep.rows[-1]
    specs = [("融资余额", last.rzye), ("融资买入额", last.rzmre),
             ("融资偿还额", last.rzche), ("融券余额", last.rqye)]
    for label, v in specs:
        if v is None:
            continue
        val = f"{v:,.0f}"
        ev = Evidence(company=f"{q.code} {q.name}".strip(), period=last.date,
                      metric=label, value=val.replace(",", ""), unit="元",
                      source=rep.source, caliber="交易所口径：融资余额一阶差分可复算")
        brief.facts.append(Fact(label=label, value=val, unit="元", source=rep.source,
                                kind="margin_trading", as_of=last.date,
                                caliber="交易所口径", eid=ev.eid, origin="", path="",
                                note="可复算量：与交易所公布的会计恒等式一致"))
    if rep.net_margin_buy is not None:
        v = rep.net_margin_buy
        val = f"{v:,.0f}"
        ev = Evidence(company=f"{q.code} {q.name}".strip(), period=last.date,
                      metric="净融资买入", value=val.replace(",", ""), unit="元",
                      source=rep.source, caliber="融资余额一阶差分（= 买入 − 偿还）")
        brief.facts.append(Fact(label="净融资买入", value=val, unit="元",
                                source=rep.source, kind="margin_trading",
                                as_of=last.date,
                                caliber="融资余额一阶差分（= 买入 − 偿还）",
                                eid=ev.eid, origin="", path="",
                                note="可复算量：与交易所公布的会计恒等式一致"))
        brief.bundle["_net_margin_buy"] = v
    if len(rep.rows) >= 21 and rep.rows[-21].rzye is not None:
        v20 = rep.rows[-21].rzye
        val20 = f"{v20:,.0f}"
        ev20 = Evidence(company=f"{q.code} {q.name}".strip(), period=rep.rows[-21].date,
                        metric="融资余额（20日前）",
                        value=val20.replace(",", ""), unit="元",
                        source=rep.source, caliber="交易所口径")
        brief.facts.append(Fact(label="融资余额（20日前）", value=val20,
                                unit="元", source=rep.source, kind="margin_trading",
                                as_of=rep.rows[-21].date, caliber="交易所口径",
                                eid=ev20.eid, origin="", path="",
                                note="用于算 20 日变化（两条依据都可回溯到同一来源）"))
    if rep.chg_20d is not None:
        brief.bundle["_margin_chg_20d"] = rep.chg_20d


def _collect_st(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """ST 名单 —— **交易所判定的事实**（S0 级，可直接断言）。

    ★ 取不到时不造假：不生成这条事实 → 对应的法定触发器自动变成 UNKNOWN，
      而 **UNKNOWN 绝不会被当成 CLEAR**。这就是三态设计的用处。
    """
    # ★★★ 走**适配层**，不走 vendor 直取。两个原因（都是实测踩到的）：
    #   ① vendor 的 `st_stock_list()` 有已知缺陷（北交所 ST 在两张表里重复 → 整个名单抛错）
    #   ② 它**每次都要翻 60 页 clist（全市场 5562 只），实测 10.5 秒** —— 而这是
    #      「一份数据服务全市场」，适配层里加了当日落盘缓存，第二次是 0.01 秒。
    #   ⚠️ 之前这里和 `compute/minefield.py` 各写了一份 —— **duplicate owner**，已合并到适配层。
    try:
        from data import adapters as AD
        hit, _path, _note = AD.find(q.code)
    except Exception as e:                                   # noqa: BLE001
        log.append("st_list", ok=False, note=f"{type(e).__name__}: {str(e)[:60]}")
        return
    if hit is None:
        # ★ 三态：取不到 → 不造假，也不生成事实 → 法定触发器自动变 UNKNOWN
        brief.gaps.append(f"ST 名单未取到（{_note[:60]}）—— **这一项没查，不是「不在名单里」**")
        return
    brief.kinds_seen.add("st_list")
    f = Fact(label="ST状态", value=("是（在风险警示名单中）" if hit else "否（不在名单中）"),
             unit="", source="eastmoney clist",
             kind="fact", as_of="", caliber=_note[:120], eid="", origin="", path="",
             note="交易所风险警示名单（S0 级事实）")
    brief.facts.append(f)


def _scan_bear(brief: Brief) -> None:
    """跑法定看空触发器（**反方不是我编的，是法条阈值算出来的**）。

    ★ 用 `kinds_seen`（**真的取到了哪些数据**）判三态，
      不是用"有没有变成事实" —— 算出来的指标（MA60 / 回撤）没有 payload 字段，
      用后者判会把它们误当成"数据没取到"，进而**把命中静默吞掉变成假 CLEAR**。
    """
    from judge import bear as BEAR_MOD
    by_label = {f.label: f for f in brief.facts}
    try:
        brief.bear_scan = BEAR_MOD.scan(by_label, set(brief.kinds_seen))
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"法定触发器扫描失败：{e}")


def _computed_fact(q: Query, label: str, value: str, *, unit: str, source: str,
                   kind: str, as_of: str, caliber: str, origin: str,
                   note: str = "") -> Fact:
    """**算出来的**指标也要变成"事实"，否则触发器读不到 → 假 CLEAR。

    它的证据号是按自己字段算的内容指纹（计算类声明），
    原始包指纹指向日线那一包 —— 所以「这个指标是从哪段行情算的」仍然可追。
    """
    ev = Evidence(company=f"{q.code} {q.name}".strip() or q.code, period=as_of,
                  metric=label, value=str(value).replace(",", ""), unit=unit or "—",
                  source=source, caliber=caliber or "—", origin_eid=origin)
    return Fact(label=label, value=value, unit=unit, source=source, kind=kind,
                as_of=as_of, caliber=caliber, eid=ev.eid, origin=origin, path="",
                note=note)


def _collect_tech(q: Query, brief: Brief, log: FetchLog, res_map: dict) -> None:
    """技术面：日线 + 指标 + 位置 + 风险。"""
    from compute.load import LoadError, load_index_kline, load_kline
    try:
        df, meta = load_kline(q.code, bars=800, adjust="none")
    except LoadError as e:
        brief.gaps.append(f"日线取不到：{e.why}")
        return
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"日线取不到：{e}")
        return
    if meta.get("eid"):
        pass                      # 日线只进 bundle，不进 facts（facts 只放可逐条核验的点值）
    brief.kinds_seen.add("kline")

    from compute.indicators import with_indicators
    try:
        ind, calibers = with_indicators(df, need=260)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"指标算不出来：{e}")
        ind, calibers = None, {}
    brief.indicators_caliber.update(calibers)

    last = df.tail(1).to_dicts()[0]
    close = float(last["close"])
    brief.bundle["收盘价"] = f"{close:.2f} 元（{str(last['date'])[:10]}，不复权）"

    if ind is not None and not ind.is_empty():
        row = ind.tail(1).to_dicts()[0]
        # ★ 列名是大写的（实测：MA5 / MA20 / MA60 / RSI14 / ATR14 / MACD）
        for k in ("MA5", "MA20", "MA60", "MA120", "MA250", "RSI14", "MACD", "ATR14"):
            if k in row and row[k] is not None:
                brief.bundle[k] = round(float(row[k]), 4)
        brief.bundle["_close"] = close
        if row.get("MA60") is not None:
            brief.bundle["_ma60"] = float(row["MA60"])

    # 位置：距 250 日高低点
    tail = df.tail(250)
    hi = float(tail["high"].max())
    lo = float(tail["low"].min())
    brief.bundle["250日最高"] = f"{hi:.2f} 元"
    brief.bundle["250日最低"] = f"{lo:.2f} 元"
    brief.bundle["距250日高点位置"] = f"{(close/hi-1)*100:+.2f}%"
    brief.bundle["距250日低点位置"] = f"{(close/lo-1)*100:+.2f}%"
    dd = round((1 - close / hi) * 100, 2)
    brief.bundle["距250日高点回撤%"] = dd

    # ★★ 把**算出来的指标**也做成"事实" —— 否则法定触发器读不到它们，
    #    会把「命中」静默吞成「没命中」（假 CLEAR）。实测踩到过。
    kinfo = dict(kind="kline", source=str(meta.get("source", "")),
                 as_of=str(last["date"])[:10], origin=str(meta.get("eid", "")))
    brief.facts.append(_computed_fact(
        q, "收盘价", f"{close:.2f}", unit="元",
        caliber="日线·不复权（制度判定必须用不复权价）", **kinfo))
    brief.facts.append(_computed_fact(
        q, "距250日高点回撤%", f"{dd:g}", unit="%",
        caliber="近 250 个交易日最高价口径", **kinfo))
    if ind is not None and not ind.is_empty():
        row = ind.tail(1).to_dicts()[0]
        if row.get("MA60") is not None:
            brief.facts.append(_computed_fact(
                q, "MA60", f"{float(row['MA60']):.4f}", unit="元",
                caliber="60 日均线（通达信口径，需预热 300 根）", **kinfo))

    # 风险：需要指数
    try:
        idx, _im = load_index_kline("000001", bars=800)
        from compute.risk import risk_profile
        rp = risk_profile(df, idx)
        for k in ("hv_annual", "max_drawdown", "var95", "beta", "downside_vol"):
            v = getattr(rp, k, None)
            if v is not None:
                brief.bundle[f"风险·{k}"] = round(float(v), 4)
        for n in (rp.notes or [])[:3]:
            brief.notices.append(n)
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"风险画像算不出来：{e}")


def _assemble_claim(q: Query, brief: Brief) -> None:
    """把依据 → 结论。

    ★★ 方向**由依据算出**（`_su_rule`），不是外部传进来的 ——
      这是消融能成立的前提，也是调研点名的那个卡点：
      「direction 对 citations 没有数值上的函数依赖 → 消融是空转」。
    """
    # ① 先把**所有可用事实**做成候选引用（规则自己决定读哪几条）
    cand: list[C.Citation] = []
    seen: set = set()
    for f in brief.facts:
        if not f.eid or f.eid in seen:
            continue
        seen.add(f.eid)
        ev = Evidence(company=f"{q.code} {q.name}".strip(), period=f.as_of,
                      metric=f.label, value=f.value.replace(",", ""),
                      unit=f.unit or "—", source=f.source, caliber=f.caliber or "—")
        cand.append(C.Citation(evidence=ev, kind=_kind_of(f)))

    # ② 跑规则 → 方向 + 它实际读了哪几条
    try:
        direction, used = _su_rule(cand)
        try:
            from judge import score as _SC
            _z = {}
            for _c in cand:
                _m = _c.evidence.metric
                if _m in ("EP分位", "BP分位", "换手率分位"):
                    _k = {"EP分位": "EP", "BP分位": "BP", "换手率分位": "TURN20"}[_m]
                    _z[_k] = 2.0 * float(str(_c.evidence.value)) - 1.0
            if _z:
                brief.score_result = _SC.decide(code=q.code, as_of="", scores=_z)
        except Exception:                                    # noqa: BLE001
            pass
    except C.NoEvidenceError:
        brief.claim = None
        brief.notices.append("本次**一条规则都没触发**，按设计不给方向，只给上面的事实。")
        return

    # ③ 只引**规则真正读过的那几条**（不许把全部事实都算成依据）
    cites = [c for c in cand if c.evidence.metric in used]

    nb, ns = len(brief.bull), len(brief.bear)
    conf = min(0.5 + 0.1 * abs(nb - ns), 0.8)

    # ★ 失效条件必须**跟着规则的方向写**，不能写反：
    #   规则看空的理由是"同比为负"，那失效条件就是"转正"，不是"转负"。
    inval: list[str] = []
    for r in brief.bull + brief.bear:
        if r.key == "fin_yoy":
            inval.append("下一期财报若归母净利润**同比转正**，基本面那条依据失效"
                         if r.stance == BEAR else
                         "下一期财报若归母净利润**同比转负**，基本面那条依据失效")
        elif r.key in ("above_ma60", "below_ma60"):
            inval.append("价格**跌破** 60 日均线，趋势那条依据失效"
                         if r.stance == BULL else
                         "价格**站回** 60 日均线，趋势那条依据失效")
        elif r.key == "drawdown":
            inval.append("价格回到距 250 日高点 20% 以内，回撤那条依据失效")
        elif r.key == "valuation_low":
            inval.append("PE 分位升到 70% 以上，估值那条依据失效")
        elif r.key == "valuation_high":
            inval.append("PE 分位回落到 30% 以下，估值那条依据失效")

    # 口径：结论跨了行情与财报两类数据，把用到的口径列出来（不许只写一个）
    cals = []
    for f in brief.facts:
        if f.caliber and f.caliber not in cals:
            cals.append(f.caliber)
    claim_caliber = "；".join(cals[:4]) + "（跨行情与财报两类数据，各数字口径见上）"

    if not cites:
        brief.claim = None
        brief.notices.append("规则触发了但**没对上证据** → 按设计不给方向。")
        return
    try:
        brief.claim = C.build(
            code=q.code, name=q.name, as_of=brief.generated_at[:10],
            direction=direction,
            text=(f"机械规则计数：看多 {nb} 条 vs 看空 {ns} 条。"
                  "**本系统不预测涨跌**；以上是事实与位置描述，不构成买卖建议。"),
            citations=cites,
            caliber=claim_caliber,
            confidence=conf,
            invalidators=inval,
        )
    except C.NoEvidenceError as e:
        brief.claim = None
        brief.gaps.append(f"结论构造被拒：{e}")


def _run_ablation(brief: Brief) -> None:
    """★★ 证据消融：去掉一条依据，结论会不会翻。

    ★ 用 `_su_rule` 这个**只吃依据的纯函数** —— 它同时被用来算基线方向，
      所以消融评估的就是**同一个推理过程**，不是另做一个。
    """
    if brief.claim is None:
        return
    try:
        from judge import ablation as AB
        rep = AB.analyze(
            brief.claim,
            rule=lambda cs: _su_rule(cs)[0],
            rule_id="su_mechanical_v1",
        )
        brief.ablation = rep
        # ★★ 判脆弱必须**真的降级**：写进 claim 的 flags。
        #   否则"降级为脆弱"只活在打印文本里，
        #   confidence 不动、flags 不加，契约照样报"全部通过"。
        if rep.state == AB.FRAGILE:
            brief.claim.flags[C.F_FRAGILE] = (
                f"去掉 {rep.flip_count} 条依据中任一条，结论就翻"
                f"（共 {rep.n_usable} 条依据）—— **不许给强结论**")
            # 降级至测量不超过"一条依据能支持的上限"
            brief.claim.confidence = min(brief.claim.confidence,
                                         C.CONF_CAP.get(1, 0.5))
    except Exception as e:                                  # noqa: BLE001
        brief.gaps.append(f"消融失败：{type(e).__name__}: {e}")


def _kind_of(f: Fact) -> str:
    """数据层 kind —— **从 Result 直接带过来的，不是从来源字符串猜的**。

    ★ 第一版是按 source 文本猜（"腾讯"→quote），实测直接崩：新浪的财务包
      source 写的是 `sina`，两个分支都不匹配 → 返回空 → claim 判"没标 kind
      → 保守默认不能进结论" → **整条结论作废**。
      猜来源是最不该做的事：猜错就是静默错，猜不中就是静默废。
    """
    return f.kind


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════
def analyze(question: str) -> Brief:
    q = parse(question)
    brief = Brief(q=q, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log = FetchLog()
    res_map: dict = {}
    brief._res_map = res_map                            # noqa: SLF001

    if q.qtype in (Q_QUOTE, Q_PROFILE):
        _collect_quote(q, brief, log, res_map)
    if q.qtype in (Q_FINANCIAL, Q_PROFILE):
        _collect_financial(q, brief, log, res_map)
    if q.qtype in (Q_PROFILE,):
        _collect_tech(q, brief, log, res_map)
        _collect_valuation(q, brief, log, res_map)
    if q.qtype in (Q_MARKET, Q_PROFILE):
        _collect_market(q, brief, log, res_map)

    if q.qtype == Q_PROFILE:
        _collect_st(q, brief, log, res_map)
        _collect_margin(q, brief, log, res_map)
        _collect_tieout(q, brief, log, res_map)
        _collect_intel(q, brief, log, res_map)
        _collect_xsection(q, brief, log, res_map)
        _collect_minefield(q, brief, log, res_map)
        _collect_events(q, brief, log, res_map)
        b = dict(brief.bundle)
        b["close"] = brief.bundle.get("_close")
        b["ma60"] = brief.bundle.get("_ma60")
        # ★ 优先用**自算**的同比（它由两个可回溯的报告期数算出）；
        #   没有就用接口给的。两个都有时，前面已经做过一致性对拍。
        b["归母净利润同比"] = (brief.bundle.get("_yoy_calc")
                          if brief.bundle.get("_yoy_calc") is not None
                          else brief.bundle.get("_yoy_raw"))
        brief.bull, brief.bear = _rules(b)
        _scan_bear(brief)
        _assemble_claim(q, brief)
        _run_ablation(brief)

    brief.gaps = log.gaps() + brief.gaps
    return brief


def _main(argv: list[str]) -> int:
    if not argv:
        print("用法：python -m ask \"茅台现在多少钱\"")
        return 2
    try:
        brief = analyze(" ".join(argv))
    except AskError as e:
        print(f"【拒答】{e}")
        return 1
    print(brief.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
