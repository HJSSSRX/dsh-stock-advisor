# -*- coding: utf-8 -*-
"""新浪行情 —— 备源。

实测：https://hq.sinajs.cn/list=sh600519
★ 必须带 Referer: https://finance.sina.com.cn，否则 403
★ 返回 GBK
返回：var hq_str_sh600519="贵州茅台,今开,昨收,现价,最高,最低,买一,卖一,成交量,成交额,...,日期,时间,..";
"""
from __future__ import annotations

from datetime import datetime

from ..caliber import prefixed
from .http import FetchError, get_text

NAME = "sina"
_REF = "https://finance.sina.com.cn"
_TIMEOUT = 8


def _f(parts: list, idx: int):
    try:
        v = parts[idx]
        return float(v) if v not in ("", "-") else None
    except Exception:                                        # noqa: BLE001
        return None


def quote(code: str) -> tuple[dict, str, str]:
    txt = get_text(
        f"https://hq.sinajs.cn/list={prefixed(code)}",
        referer=_REF, encoding="gbk", timeout=_TIMEOUT,
    )
    if '="' not in txt:
        raise FetchError("新浪返回格式异常")
    body = txt.split('="', 1)[1].rstrip('";\n')
    p = body.split(",")
    if len(p) < 32:
        raise FetchError(f"新浪字段数异常：{len(p)}")

    price, prev = _f(p, 3), _f(p, 2)
    out = {
        "名称": p[0], "今开": _f(p, 1), "昨收": prev, "现价": price,
        "最高": _f(p, 4), "最低": _f(p, 5),
        "成交量_股": _f(p, 8), "成交额_元": _f(p, 9),
    }
    if price and prev:
        out["涨跌额"] = round(price - prev, 4)
        out["涨跌幅_%"] = round((price - prev) / prev * 100, 4)
    if not price and prev:
        out["⚠️停牌"] = True
        out["现价"] = prev
        out["备注"] = "现价为 0（停牌/无成交），此处显示昨收价"

    d, t = p[30], p[31] if len(p) > 31 else ""
    as_of = f"{d} {t}" if d else datetime.now().strftime("%Y-%m-%d")
    return out, as_of, "不复权（实时快照）"
