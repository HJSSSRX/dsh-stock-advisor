# -*- coding: utf-8 -*-
"""字段精度与口径登记表。

为什么必须有这张表：
  东方财富的 JSON 接口里，**同一个数字在不同字段上精度不同** ——
  f43=126300 其实是 1263.00 元；f170=57 其实是 0.57%。
  这些"除以多少"的规则如果散落在代码各处，改一次错一次。
  所以集中登记在这里，取数时统一换算。
"""
from __future__ import annotations

# ── 复权口径 ────────────────────────────────────────────────────────
FQT = {0: "不复权", 1: "前复权", 2: "后复权"}

# ── K 线周期 ────────────────────────────────────────────────────────
KLT = {
    "1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60,
    "daily": 101, "weekly": 102, "monthly": 103,
}

# ── 东财 stock/get 字段：名称 / 除数 / 单位 ──────────────────────────
#   除数 1 表示原值；100 表示需除以 100
EM_QUOTE_FIELDS = {
    "f43": ("现价", 100, "元"),
    "f44": ("最高", 100, "元"),
    "f45": ("最低", 100, "元"),
    "f46": ("今开", 100, "元"),
    "f47": ("成交量", 1, "手"),
    "f48": ("成交额", 1, "元"),
    "f57": ("代码", 1, ""),
    "f58": ("名称", 1, ""),
    "f59": ("价格小数位", 1, ""),
    "f60": ("昨收", 100, "元"),
    "f86": ("时间戳", 1, ""),
    "f107": ("市场", 1, ""),
    "f116": ("总市值", 1, "元"),
    "f117": ("流通市值", 1, "元"),
    "f152": ("状态码", 1, ""),
    "f162": ("市盈率(动)", 100, "倍"),
    "f167": ("市净率", 100, "倍"),
    "f168": ("换手率", 100, "%"),
    "f169": ("涨跌额", 100, "元"),
    "f170": ("涨跌幅", 100, "%"),
    "f171": ("振幅", 100, "%"),
    "f292": ("状态码2", 1, ""),
    "f100": ("行业", 1, ""),
    "f102": ("地域板块", 1, ""),
    "f103": ("概念板块", 1, ""),
}

# ── 腾讯行情字段位置（v_xxx="1~名称~代码~现价~昨收~今开~..."）────────
TX_QUOTE_INDEX = {
    "name": 1, "code": 2, "price": 3, "prev_close": 4, "open": 5,
    "volume": 6, "time": 30, "change": 31, "pct": 32,
    "high": 33, "low": 34, "amount_wan": 37, "turnover": 38, "pe": 39,
    "amplitude": 43, "circ_mv": 44, "total_mv": 45, "pb": 46,
    "limit_up": 47, "limit_down": 48,
}

# ── 新浪行情字段位置（名称,今开,昨收,现价,最高,最低,买一,卖一,...）──
SINA_QUOTE_INDEX = {
    "name": 0, "open": 1, "prev_close": 2, "price": 3, "high": 4, "low": 5,
    "volume": 8, "amount": 9, "date": 30, "time": 31,
}

# ── 市场前缀 ────────────────────────────────────────────────────────
def market_of(code: str) -> int:
    """股票代码 → 东财 market 编号。

    沪市 = 1：6xxxxx（主板/科创板 688）、900xxx（沪 B）
    深市 = 0：0xxxxx、3xxxxx（创业板 300/301）、2xxxxx（深 B）
    北交所 = 0：920xxx（新代码段）、8xxxxx、4xxxxx
    ★ 坑：920xxx 以 9 开头但属于北交所，不能按"9 开头=沪市"处理（实测踩到）
    """
    code = str(code).strip().zfill(6)
    if code.startswith("6"):
        return 1
    if code.startswith("900"):   # 沪市 B 股
        return 1
    return 0


def secid(code: str) -> str:
    """股票代码 → 东财 secid（如 1.600519 / 0.300417 / 0.920627）。"""
    code = str(code).strip().zfill(6)
    return f"{market_of(code)}.{code}"


def index_secid(code: str) -> str:
    """**指数**代码 → 东财 secid。

    ★★★ 为什么必须和 `secid()` 分开（实测踩到）★★★
    指数代码和股票代码**会撞**。最典型的：`000001`
        · 当**股票**看 → `0.000001` = 平安银行（约 11 元）
        · 当**指数**看 → `1.000001` = 上证指数（约 3800 点）
    `market_of()` 按首位数字判定，`000001` 会走深市分支 → **取到平安银行**。
    实测：以为在取上证指数的交易日历，拿到的其实是平安银行的 K 线
    （交集 2000 天 / 缺失 0 天，两个都是个股 —— 一眼就看出来了）。

    规则（与东财网页端一致）：
        000xxx / 999xxx（上证系列、沪深300、中证系列） → 沪市 1
        399xxx（深证成指、创业板指）                     → 深市 0

    ⚠️ 调用方**必须显式说明这是指数**，不能靠代码自动推断 —— 推断不出来。
    """
    code = str(code).strip().zfill(6)
    mk = 1 if code.startswith(("000", "999")) else 0
    return f"{mk}.{code}"


def prefixed(code: str) -> str:
    """股票代码 → 腾讯/新浪前缀（如 sh600519 / sz300417 / bj920627）。"""
    code = str(code).strip().zfill(6)
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("4", "8", "920")):
        return "bj" + code
    return "sz" + code


# ── 涨跌停幅度（单一真值函数 —— 只允许这一处实现）────────────────────
def limit_pct(code: str, name: str = "") -> float:
    """返回该股票的涨跌停幅度（小数）。单一真值，不许在别处再写一遍。"""
    code = str(code).strip()
    if "ST" in (name or "").upper():
        return 0.05
    if code.startswith(("688", "300", "301")):   # 科创板 / 创业板
        return 0.20
    if code.startswith(("4", "8", "920")):       # 北交所
        return 0.30
    return 0.10


def apply_scale(field: str, value):
    """按登记表把原始值换算成可读值。"""
    if value is None or field not in EM_QUOTE_FIELDS:
        return value
    _name, div, _unit = EM_QUOTE_FIELDS[field]
    if div == 1 or not isinstance(value, (int, float)):
        return value
    return round(value / div, 4)


def scale_quote(raw: dict) -> dict:
    """把东财原始字段字典转成 {中文名: 值}，并标注单位。"""
    out = {}
    for k, v in raw.items():
        if k not in EM_QUOTE_FIELDS:
            out[k] = v
            continue
        name, div, unit = EM_QUOTE_FIELDS[k]
        val = apply_scale(k, v)
        out[name] = val
        if unit and isinstance(val, (int, float)):
            out[name + "_单位"] = unit
    return out
