# -*- coding: utf-8 -*-
"""akshare 源 —— **东财被封时的换源通道**。

═══════════════════════════════════════════════════════════════════
为什么会有这个文件
═══════════════════════════════════════════════════════════════════
2026-10-12 实测：**东方财富把 `clist` 接口族单独封了**。

三个独立项目在同一接口上的表现（同一台机器、同一时段）：
    我们自己（直连 HTTP）                → RemoteDisconnected
    a-stock-data 10,795★（有串行限流+会话复用）→ RemoteDisconnected
    **akshare 22,897★（最成熟的库）**      → RemoteDisconnected

**→ 不是我们的代码问题，是东财的服务端行为。**
   直接 curl 那个 URL：HTTP 000 / size 0 / **0.095 秒就断**。

═══════════════════════════════════════════════════════════════════
★★ 成熟项目的答案：不是"能不能用东财"，而是"同一份数据永远准备多个源"
═══════════════════════════════════════════════════════════════════
akshare 自己就是**多源命名结构**：
    `stock_board_industry_name_em`   ← 东财（现在挂）
    `stock_board_industry_name_ths`  ← 同花顺（**实测通**）
    `stock_board_concept_name_ths`   ← 同花顺（**实测通，375 行**）

**所以我们照做：把 akshare 接成一个源，专门走非东财的通道。**

⭐ 而且 akshare 里有**比东财更权威**的选择：
    `stock_industry_category_cninfo`  ← **巨潮（交易所指定披露平台）**，S0 级
    比东财/同花顺的自家分类更硬 —— 那两家是"我们认为它属于这个行业"，
    巨潮是"按规定它属于这个行业"。
"""
from __future__ import annotations

import time
import warnings
from datetime import date, timedelta
from typing import Any, Callable, Optional

warnings.filterwarnings("ignore")

# ── 限流：akshare 内部大量用 requests，没有统一节流，我们自己加 ──
MIN_INTERVAL = 1.2
_LAST: dict[str, float] = {}


class AkshareError(RuntimeError):
    """akshare 调用失败。带 kind 供上层分类。"""

    def __init__(self, msg: str, kind: str = "source_error") -> None:
        super().__init__(msg)
        self.kind = kind


def _ak():
    try:
        import akshare  # noqa: PLC0415
    except ImportError as exc:                       # pragma: no cover
        raise AkshareError(f"akshare 未安装：{exc}", "not_installed") from exc
    return akshare


def _throttle(key: str) -> None:
    last = _LAST.get(key, 0.0)
    wait = MIN_INTERVAL - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST[key] = time.time()


def _call(label: str, fn: Callable[[], Any], key: Optional[str] = None) -> Any:
    _throttle(key or label)
    try:
        return fn()
    except Exception as exc:                         # noqa: BLE001
        msg = str(exc)
        low = msg.lower()
        kind = "source_error"
        if "ssl" in low:
            kind = "source_error"
        elif "remote disconnected" in low or "connection aborted" in low \
                or "connectionerror" in low:
            kind = "rate_limited"
        elif "attribute" in low and "has no attribute" in low:
            kind = "not_implemented"
        raise AkshareError(f"{label}: {type(exc).__name__}: {msg[:120]}", kind) from exc


def _df_to_rows(df) -> list[dict]:
    if df is None:
        return []
    try:
        import pandas as pd                          # noqa: PLC0415
        if isinstance(df, pd.DataFrame):
            return df.to_dict("records")
        if isinstance(df, pd.Series):
            return [df.to_dict()]
    except Exception:                                # noqa: BLE001
        pass
    if isinstance(df, list):
        return [x if isinstance(x, dict) else {"value": x} for x in df]
    if isinstance(df, dict):
        return [df]
    return []


def _today() -> str:
    return date.today().strftime("%Y%m%d")


def _recent(n: int = 5) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y%m%d")


def _last_report_period() -> str:
    """最近一个**已结束的报告期**（季度末）。

    ★ 为什么不能用「N 天前」：实测 `stock_industry_pe_ratio_cninfo` 的 date
      必须是报告期（0331/0630/0930/1231），传 20260912 会报错「'证监会行业分类'」，
      **报错信息完全看不出是这个原因** —— 必须用报告期。
    """
    today = date.today()
    y = today.year
    for m, d in ((12, 31), (9, 30), (6, 30), (3, 31)):
        cand = date(y, m, d)
        if cand <= today:
            return cand.strftime("%Y%m%d")
    return date(y - 1, 12, 31).strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════
# 行业 / 板块 —— **这里是东财被封后的主要替代通道**
# ══════════════════════════════════════════════════════════════════
def industry_board_ths(code: str = "", **kw) -> tuple[list, str, str]:
    """行业板块列表（**同花顺源**）。"""
    ak = _ak()
    rows = _df_to_rows(_call("ths 行业板块", lambda: ak.stock_board_industry_name_ths(), "ths"))
    if not rows:
        raise AkshareError("同花顺行业板块返回空", "no_data_in_window")
    return (rows, _today(),
            "同花顺行业板块分类。⚠️ **与东财的分类不同** —— "
            "「属于哪个行业」多家口径不一，只能说「按同花顺的分类」。")


def concept_board_ths(code: str = "", **kw) -> tuple[list, str, str]:
    """概念板块列表（**同花顺源**）。"""
    ak = _ak()
    rows = _df_to_rows(_call("ths 概念板块", lambda: ak.stock_board_concept_name_ths(), "ths"))
    if not rows:
        raise AkshareError("同花顺概念板块返回空", "no_data_in_window")
    return (rows, _today(),
            "同花顺概念板块。⚠️ **概念归属没有唯一答案**（东财/同花顺/通达信各不同）。")


def industry_cninfo(code: str = "", **kw) -> tuple[list, str, str]:
    """★ **巨潮行业分类**（法定披露级）。"""
    ak = _ak()
    symbol = kw.get("symbol") or "巨潮行业分类标准"
    rows = _df_to_rows(_call("巨潮行业分类",
                             lambda: ak.stock_industry_category_cninfo(symbol=symbol), "cninfo"))
    if not rows:
        raise AkshareError("巨潮行业分类返回空", "no_data_in_window")
    return (rows, _today(),
            "★★ **巨潮（交易所指定披露平台）行业分类** —— **法定披露级，S0/S1**，"
            "比东财/同花顺的自家分类更硬。"
            "东财说「我们认为它属于这个行业」，巨潮说「按规定它属于这个行业」。")


def industry_pe_cninfo(code: str = "", **kw) -> tuple[list, str, str]:
    """行业市盈率（巨潮源）。"""
    ak = _ak()
    # ⚠️ 只有「中国上市公司协会上市公司行业分类」这个名字有效（实测），
    #    传「巨潮行业分类标准」「申万行业分类」都会报一个看不出原因的错。
    symbol = kw.get("symbol") or "证监会行业分类"
    day = kw.get("date") or _last_report_period()   # ⚠️ 必须是报告期
    rows = _df_to_rows(_call("巨潮行业市盈率",
                             lambda: ak.stock_industry_pe_ratio_cninfo(symbol=symbol, date=day),
                             "cninfo"))
    if not rows:
        raise AkshareError("巨潮行业市盈率返回空", "no_data_in_window")
    return (rows, day, "巨潮行业市盈率（证监会行业分类）。")


def company_profile_cninfo(code: str = "600519", **kw) -> tuple[list, str, str]:
    """个股公司概况（**巨潮源**）—— 替代挂掉的东财 `stock_info`。"""
    ak = _ak()
    rows = _df_to_rows(_call(f"巨潮公司概况 {code}",
                             lambda: ak.stock_profile_cninfo(symbol=str(code)), "cninfo"))
    if not rows:
        raise AkshareError(f"{code} 公司概况返回空", "no_data_in_window")
    return (rows, _today(),
            "★ **巨潮公司概况**（法定披露级）。"
            "⚠️ 涉及经营数据仍须回财报核对。")


def broker_gold(code: str = "600519", **kw) -> tuple[list, str, str]:
    """券商月度金股。★ 情报通道。"""
    ak = _ak()
    rows = _df_to_rows(_call("券商金股",
                             lambda: ak.stock_comment_detail_zlkp_jgcyd_em(symbol=str(code)), "ths"))
    if not rows:
        raise AkshareError("券商金股返回空", "no_data_in_window")
    return (rows, _today(), "券商月度金股。★ 情报通道 —— **永不进结论**。")


# ══════════════════════════════════════════════════════════════════
# ★★ 指数 K 线 —— **这条最重要**
# ══════════════════════════════════════════════════════════════════
# 为什么必须有：2026-10-12 实测，东财被限流时 `index_kline` 取不到，
# **整个风险模块（波动率/回撤/Beta）连带两个测试套件全挂** ——
# 因为我们的指数数据**只挂在东财这一条腿上**。
#
# akshare 里有两个非东财的指数源（实测都通）：
#     stock_zh_index_daily       → 新浪，8739 行
#     stock_zh_index_daily_tx    → **腾讯，8729 行**  ← 腾讯不封 IP
# 而东财那两个（stock_zh_index_daily_em / index_zh_a_hist）**同样被掐**。
INDEX_PREFIX = {"000001": "sh", "000300": "sh", "000905": "sh", "000016": "sh",
                "000688": "sh", "000852": "sh", "399001": "sz", "399006": "sz",
                "399005": "sz", "399300": "sz"}


def index_kline_tx(code: str = "000001", **kw) -> tuple[list, str, str]:
    """★ **指数日线（腾讯源）** —— 东财被限流时的替代。"""
    ak = _ak()
    sym = _sym(code)
    df = _call(f"腾讯指数 {sym}",
               lambda: ak.stock_zh_index_daily_tx(symbol=sym), "tx")
    rows = _df_to_rows(df)
    if not rows:
        raise AkshareError(f"腾讯指数 {sym} 返回空", "no_data_in_window")
    limit = int(kw.get("limit") or kw.get("bars") or 0)
    if limit:
        rows = rows[-limit:]
    return (rows, str(rows[-1].get("date"))[:10] if rows else _today(),
            "★ **腾讯指数日线**（交易所口径，与东财对拍过）。"
            "⚠️ **不复权** —— 指数本来就不需要复权。")


def index_kline_sina(code: str = "000001", **kw) -> tuple[list, str, str]:
    """指数日线（新浪源）—— 腾讯之外的第二个备胎。"""
    ak = _ak()
    sym = _sym(code)
    df = _call(f"新浪指数 {sym}",
               lambda: ak.stock_zh_index_daily(symbol=sym), "sina")
    rows = _df_to_rows(df)
    if not rows:
        raise AkshareError(f"新浪指数 {sym} 返回空", "no_data_in_window")
    limit = int(kw.get("limit") or kw.get("bars") or 0)
    if limit:
        rows = rows[-limit:]
    return (rows, str(rows[-1].get("date"))[:10] if rows else _today(),
            "★ **新浪指数日线**。⚠️ 与腾讯/东财可能有个别日的舍入差异。")


def _sym(code: str) -> str:
    """`000001` → `sh000001`。"""
    c = str(code).zfill(6)
    pre = INDEX_PREFIX.get(c)
    if pre:
        return pre + c
    if c.startswith(("399", "159", "15", "16")):
        return "sz" + c
    return "sh" + c


# ══════════════════════════════════════════════════════════════════
# 供 registry 自动挂载
# ══════════════════════════════════════════════════════════════════
EXPORTS = {
    "industry_board_ths": industry_board_ths,
    "concept_board_ths": concept_board_ths,
    "industry_cninfo": industry_cninfo,
    "industry_pe_cninfo": industry_pe_cninfo,
    "company_profile_cninfo": company_profile_cninfo,
    "broker_gold": broker_gold,
    "index_kline_tx": index_kline_tx,
    "index_kline_sina": index_kline_sina,
}
