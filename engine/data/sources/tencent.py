# -*- coding: utf-8 -*-
"""腾讯行情 —— 备源（最轻，一次可取多只）。

实测：https://qt.gtimg.cn/q=sh600519,sz300417
返回 v_sh600519="1~贵州茅台~600519~现价~昨收~今开~..."（~ 分隔，★ GBK 编码）
"""
from __future__ import annotations

from datetime import datetime

from ..caliber import prefixed
from .http import FetchError, get_text

NAME = "tencent"
_TIMEOUT = 8


def _f(parts: list, idx: int):
    try:
        v = parts[idx]
        return float(v) if v not in ("", "-", "~") else None
    except Exception:                                        # noqa: BLE001
        return None


def quote(code: str) -> tuple[dict, str, str]:
    """单只行情。"""
    txt = get_text(f"https://qt.gtimg.cn/q={prefixed(code)}", encoding="gbk", timeout=_TIMEOUT)
    if "=" not in txt or '""' in txt:
        raise FetchError("腾讯返回为空（代码可能不存在）")
    body = txt.split("=", 1)[1].strip().strip('";')
    p = body.split("~")
    if len(p) < 35:
        raise FetchError(f"腾讯字段数异常：{len(p)}")

    price = _f(p, 3)
    prev = _f(p, 4)
    out = {
        "名称": p[1], "代码": p[2],
        "现价": price, "昨收": prev, "今开": _f(p, 5),
        "成交量_手": _f(p, 6),
        "涨跌额": _f(p, 31), "涨跌幅_%": _f(p, 32),
        "最高": _f(p, 33), "最低": _f(p, 34),
        "成交额_万元": _f(p, 37), "换手率_%": _f(p, 38),
        "市盈率": _f(p, 39), "振幅_%": _f(p, 43),
        "流通市值_亿": _f(p, 44), "总市值_亿": _f(p, 45), "市净率": _f(p, 46),
    }
    # 停牌识别（与东财同一纪律）
    if not price and prev:
        out["⚠️停牌"] = True
        out["现价"] = prev
        out["备注"] = "现价为 0（停牌/无成交），此处显示昨收价"

    as_of = ""
    t = p[30] if len(p) > 30 else ""
    if len(t) >= 14:      # 形如 20261009161459
        try:
            as_of = datetime.strptime(t[:14], "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:                                    # noqa: BLE001
            as_of = t[:8]
    return out, as_of or datetime.now().strftime("%Y-%m-%d"), "不复权（实时快照）"


def quote_many(codes: list[str]) -> tuple[list, str, str]:
    """批量行情（腾讯的强项：一次请求多只）。"""
    q = ",".join(prefixed(c) for c in codes)
    txt = get_text(f"https://qt.gtimg.cn/q={q}", encoding="gbk", timeout=_TIMEOUT)
    rows, as_of = [], ""
    for line in txt.strip().split(";"):
        if "=" not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        p = body.split("~")
        if len(p) < 35:
            continue
        rows.append({
            "代码": p[2], "名称": p[1], "现价": _f(p, 3), "昨收": _f(p, 4),
            "涨跌幅_%": _f(p, 32), "成交额_万元": _f(p, 37), "换手率_%": _f(p, 38),
        })
        if not as_of and len(p) > 30 and len(p[30]) >= 8:
            as_of = f"{p[30][:4]}-{p[30][4:6]}-{p[30][6:8]}"
    if not rows:
        raise FetchError("腾讯批量返回为空")
    return rows, as_of, "不复权（实时快照）"
