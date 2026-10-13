# -*- coding: utf-8 -*-
"""统一装载器 —— 计算层取数的唯一入口。

═══════════════════════════════════════════════════════════════════
为什么要多这一层
═══════════════════════════════════════════════════════════════════
改造前：每个计算模块（position / risk / …）自己调 `fetch()`，自己读字段。
        字段名是东财的中文键（`最高` / `收盘` / `成交量_手`）。

问题：
  ① 数据来源换成 a-stock-data 后，字段名变成英文（high / close / volume），
     **每个模块都要改**，改漏一个就是静默 0 值
  ② 每个模块重复一遍「异常 → 空表 → 全 0」的样板，错一次就污染结论

改造后：计算层只认这一层给的**规范列**，不再关心数据从哪来。

    [计算层 position / risk / …]
              ↓  load_kline() / load_quote() / load_index()
    [本文件 —— 规范列 + 口径 + 拒答]
              ↓
    ┌──────────┴──────────┐
    ↓                     ↓
 a-stock-data（默认）   我们自己的 registry（对照组）

★★★ 副作用：天然具备「对拍」能力 ★★★
两个独立实现返回同一份数据 → 逐字段比 → **不一致就是有一方错了**。
这是最强的一种验证：不是「我认为它对」，而是「两个不认识彼此的实现给出了同一个数」。
"""
from __future__ import annotations

from typing import Literal, Optional

import polars as pl

# 规范列 —— 计算层只认这些
KLINE_COLS = ["date", "open", "high", "low", "close", "volume", "amount", "change_pct"]
QUOTE_COLS = ["code", "name", "price", "pe_ttm", "pb", "mcap_yi", "turnover_pct",
              "limit_up", "limit_down", "is_stale"]

# 旧中文键 → 规范列（供 registry 老路径做转换；vendor 路径本来就是英文，不需要）
_CN_ALIAS = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量_手": "volume", "成交额_元": "amount", "涨跌幅_%": "change_pct",
    "名称": "name", "最新价": "price", "市盈率_动态": "pe_ttm", "市净率": "pb",
}


class LoadError(RuntimeError):
    """取数失败。**带着原因**，不要变成一列 0。"""

    def __init__(self, why: str, *, error_kind: str = "", kind: str = "", code: str = ""):
        super().__init__(why)
        self.why = why
        self.error_kind = error_kind
        self.kind = kind
        self.code = code


def _norm_rows(rows: list[dict]) -> list[dict]:
    """把中文键翻成规范列（已经是的原样返回）。"""
    if not rows:
        return rows
    if any(k in rows[0] for k in _CN_ALIAS):
        out = []
        for r in rows:
            out.append({_CN_ALIAS.get(k, k): v for k, v in r.items()})
        return out
    return rows


def _to_frame(rows: list[dict], cols: list[str], *, numeric: list[str]) -> pl.DataFrame:
    """list[dict] → polars DataFrame，只保留规范列，数值列强制转 float（空 → None）。

    ★ 保留 None 而不是填 0 —— **填 0 会让「没有数据」看起来像「数据是 0」**。
    """
    norm = _norm_rows(rows)
    data: dict[str, list] = {}
    for c in cols:
        if c not in {k for r in norm for k in r}:
            continue
        vals = []
        for r in norm:
            v = r.get(c)
            if c in numeric:
                try:
                    vals.append(None if v in (None, "", "-", "--") else float(v))
                except (TypeError, ValueError):
                    vals.append(None)
            else:
                vals.append(None if v is None else str(v))
        data[c] = vals
    if not data:
        return pl.DataFrame()
    df = pl.DataFrame(data)
    if "date" in df.columns:
        df = df.with_columns(pl.col("date").cast(pl.Utf8).str.slice(0, 10)).sort("date")
    return df


# ══════════════════════════════════════════════════════════════════
# 行情
# ══════════════════════════════════════════════════════════════════
def load_kline(
    code: str,
    *,
    bars: int = 800,
    adjust: Literal["qfq", "hfq", "none"] = "qfq",
    source: Literal["vendor", "legacy", "auto"] = "auto",
) -> tuple[pl.DataFrame, dict]:
    """取日 K，返回 (规范 DataFrame, 元信息)。

    元信息至少含：source / as_of / caliber / eid / volume_unit
    **volume 的单位不是常数**（见 vendor_bridge.volume_unit）—— 已放在元信息里，
    调用方换算成交量前**必须**先看它。

    adjust：
        'qfq'  前复权 —— ⚠️ 腾讯是**等差口径**，高分红老股早年可能是负数；
                        长区间回测不要用它
        'hfq'  后复权
        'none' 不复权 —— **算涨跌停/回撤必须用这个**
    """
    from data.vendor_bridge import fetch_via_vendor, volume_unit

    fqt = {"none": 0, "qfq": 1, "hfq": 2}[adjust]
    res = None

    if source in ("vendor", "auto"):
        res = fetch_via_vendor("kline", code, {"freq": "daily", "limit": bars, "fqt": fqt})
        if not res.ok and source == "vendor":
            raise LoadError(res.error or "vendor 取数失败",
                            error_kind=res.error_kind, kind="kline", code=code)

    if (res is None or not res.ok) and source in ("legacy", "auto"):
        from data.registry import fetch
        res = fetch("kline", code, {"freq": "daily", "limit": bars, "fqt": fqt})
        if not res.ok:
            raise LoadError(res.error or "legacy 取数失败",
                            error_kind=res.error_kind, kind="kline", code=code)

    df = _to_frame(res.data or [], KLINE_COLS,
                   numeric=["open", "high", "low", "close", "volume", "amount", "change_pct"])
    if df.is_empty():
        raise LoadError("取到 0 行（该标的确实没有这段数据）",
                        error_kind="no_data_in_window", kind="kline", code=code)

    unit, factor = volume_unit(code)
    meta = {
        "source": res.source, "as_of": res.as_of, "caliber": res.caliber,
        "eid": res.eid, "status": res.status, "rows": len(df),
        "volume_unit": unit, "volume_to_share_factor": factor,
        "adjust": adjust,
    }
    return df, meta


def load_index_kline(code: str = "000001", *, bars: int = 800) -> tuple[pl.DataFrame, dict]:
    """取指数日 K。**必须用这个，不要用 load_kline 取指数** ——

    `kline('000001')` 拿到的可能是**平安银行**（11 元），不是上证指数（3800 点），
    而且**不会报错**。实测踩过。见 caliber.index_secid 的说明。
    """
    from data.registry import fetch
    res = fetch("index_kline", code, {"freq": "daily", "limit": bars, "fqt": 0})
    if not res.ok:
        raise LoadError(res.error or "指数取数失败",
                        error_kind=res.error_kind, kind="index_kline", code=code)
    df = _to_frame(res.data or [], KLINE_COLS,
                   numeric=["open", "high", "low", "close", "volume", "amount", "change_pct"])
    if not df.is_empty() and df["close"][-1] < 100 and code.startswith("000"):
        raise LoadError(
            f"取到的 {code} 收盘 {df['close'][-1]} 看着像个股不像指数（上证应在千点量级）"
            " —— 极可能取错标的", error_kind="wrong_symbol", kind="index_kline", code=code)
    return df, {"source": res.source, "as_of": res.as_of, "caliber": res.caliber,
                "eid": res.eid, "status": res.status, "rows": len(df)}


def load_quote(code: str) -> tuple[dict, dict]:
    """取实时快照，返回 (单条 dict, 元信息)。

    ⚠️ 快照的 `is_stale` 字段 = 僵尸报价检测（腾讯对退市/停牌股照样返回 200 + 数据）。
       **is_stale 为 True 时不要把这个价格当现价用。**
    """
    from data.vendor_bridge import fetch_via_vendor
    res = fetch_via_vendor("quote", code)
    if not res.ok:
        raise LoadError(res.error or "快照取数失败",
                        error_kind=res.error_kind, kind="quote", code=code)
    rows = res.data or []
    if not rows:
        raise LoadError("快照为空", error_kind="no_data_in_window", kind="quote", code=code)
    return rows[0], {"source": res.source, "as_of": res.as_of, "caliber": res.caliber,
                     "eid": res.eid, "status": res.status,
                     "is_stale": rows[0].get("is_stale")}
