# -*- coding: utf-8 -*-
"""指标层 —— 通达信口径 + 固定取数窗口。

★★★ 为什么口径要对齐通达信 ★★★
用户会问「为什么你的 MACD 和我的行情软件不一样」。这个问题有确定答案，
TA-Lib 官方文档 `docs/studies/ema-seeding/README.md` 写得很清楚：

    通达信 EMA  = 首样本播种（rule T）= 从**第一根** K 线就有值
    TA-Lib 默认 = SMA 播种（rule S）  = 要攒够 N 根才输出第一个值

而且 TA-Lib 自己明确否掉了「谁更准」的争论：
    "the defensible claim is 'every value the library publishes is one it stands behind',
     not 'the SMA seed is more accurate'."
→ 所以我们**默认对齐通达信**，并在输出里写清口径，不争论。

★★★ 本模块实测纠正了一条调研说法 ★★★
调研称「`polars_ta.tdx.EMA` 与 `ta.EMA` 是两个口径」。**实测数值完全相同（差异 0.000000）**，
两者都从第 N-1 根起才有值。真正不同的是 **`tdx.EXPMEMA`**（差异 0.098）。
而**通达信的真口径是 `ewm_mean(alpha=2/(N+1), adjust=False, min_samples=1)`** ——
polars_ta 的 `tdx.EMA` 砍掉了前 N-1 根，所以**不等于通达信的显示**。

实证（南华仪器 300417，EMA12，2026-07-27 起两者一致）：
    min_samples=1 → 第 0 根就有值
    tdx.EMA/ta.EMA → 第 11 根才有值
    EXPMEMA        → 第 11 根，且数值与前者不同（最大差 0.098）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import polars as pl

# ══════════════════════════════════════════════════════════════════
# 一、预热长度表 —— 「固定取数窗口」的量化依据
# ══════════════════════════════════════════════════════════════════
#   ★ 核心事实：EMA 类指标是**递归**的，开头若干根的值受"从哪里开始算"影响。
#     误差按 (1 − 2/(N+1)) 每根衰减 —— **N 越大衰减越慢，层数越多越久**。
#
#   ★★ 下面是**实测值**，不是拍脑袋（脚本 `_probe_warmup.py`，南华仪器 2843 根，
#      判定标准：与全历史计算的最大差异 < 1e-9）：
#
#        指标        真正需要的预热      本表取值（含余量）
#        MA60              0                5
#        EMA26           245              300      ← 递归，收敛慢
#        MACD            245              300      ← 三层 EMA 叠加
#        KDJ              70               90      ← SMA(9,3) 衰减快
#        RSI14             0               60      ← 分子分母同种子，误差相消
#        BOLL              0               30
#        ATR14             0               30
#
#   ⚠️ **教训**：我第一版把 macd 写成 60、ema 写成 40，实测发现偏差还有 0.005
#      （预热 0 时偏差 0.149，60 时 0.005，120 时 9e-5，**240 才归零**）。
#      这个常数错了不会报错，只会让"和行情软件对不上"—— **必须靠实测校准**。
WARMUP_BARS: dict[str, int] = {
    "ma": 5,
    "ema": 300,
    "macd": 300,
    "kdj": 90,
    "rsi": 60,
    "boll": 30,
    "atr": 30,
}
DEFAULT_WARMUP = 300          # 未知指标保底（宁可多取）

# 输出里必须带的口径声明（用户问"为什么和软件不一样"时，直接引用）
INDICATOR_CALIBER = {
    "ema": "通达信口径（首样本播种）：Y = X·2/(N+1) + Y'·(N-1)/(N+1)，从第一根 K 线起算",
    "macd": "DIF = EMA(12) − EMA(26)，DEA = DIF 的 EMA(9)，均为通达信口径（首样本播种）",
    "kdj": "RSV 后接通达信 SMA(X,N,M) —— 注意这是**加权递归**（alpha=M/N），"
           "不是 TA-Lib 的算术平均 SMA。这就是 KDJ/RSI/CCI 与 talib 对不上的根因",
    "rsi": "通达信口径，用 SMA(X,N,1) 加权递归，非 talib 算术平均",
    "boll": "中轨为 MA(N)，上下轨 ±K×标准差（总体标准差，非样本）",
    "atr": "TR = max(H−L, |H−C'|, |L−C'|)，ATR = TR 的 MA(N)",
}


# ══════════════════════════════════════════════════════════════════
# 二、通达信口径的基础函数
# ══════════════════════════════════════════════════════════════════
def ema_tdx(x: pl.Expr, n: int) -> pl.Expr:
    """通达信 EMA(X,N) —— **首样本播种，从第一根就有值**。

    ★ 与 polars_ta 的 `tdx.EMA` 的区别：那个用 min_samples=N，砍掉了前 N-1 根，
      严格说**不等于通达信的显示**。这里用 min_samples=1 才是真口径。
    """
    return x.ewm_mean(alpha=2.0 / (n + 1), adjust=False, min_samples=1)


def sma_cn(x: pl.Expr, n: int, m: int) -> pl.Expr:
    """通达信 SMA(X,N,M) —— **加权递归**，alpha = M/N。

    ★★ 这是 KDJ / RSI / CCI 与 TA-Lib 对不上的根因：
       通达信 `SMA(X,N,M)` 是递归加权平均，而 `talib.SMA` 是**算术平均** —— 同名不同义。
       polars_ta 的 `SMA_CN` 注释里专门写了「!!!为防止与talib版SMA误用，这里去了默认值1」。
    """
    return x.ewm_mean(alpha=m / n, adjust=False, min_samples=1)


def ma(x: pl.Expr, n: int) -> pl.Expr:
    """简单移动平均。"""
    return x.rolling_mean(window_size=n, min_samples=n)


# ══════════════════════════════════════════════════════════════════
# 三、常用指标（通达信口径）
# ══════════════════════════════════════════════════════════════════
def macd(close: pl.Expr, fast: int = 12, slow: int = 26,
         signal: int = 9) -> dict[str, pl.Expr]:
    """MACD（通达信口径）。

    返回 DIF / DEA / MACD 三条。**注意 MACD 柱 = 2×(DIF−DEA)**（通达信如此，
    很多库写成 (DIF−DEA)，差一个倍数 —— 这也是"对不上"的常见原因）。
    """
    dif = ema_tdx(close, fast) - ema_tdx(close, slow)
    dea = dif.ewm_mean(alpha=2.0 / (signal + 1), adjust=False, min_samples=1)
    return {"DIF": dif, "DEA": dea, "MACD": (dif - dea) * 2}


def kdj(high: pl.Expr, low: pl.Expr, close: pl.Expr,
        n: int = 9, m1: int = 3, m2: int = 3) -> dict[str, pl.Expr]:
    """KDJ（通达信口径）。

    RSV = (C − LLV(L,N)) / (HHV(H,N) − LLV(L,N)) × 100
    K   = SMA(RSV, M1, 1)   ← 通达信加权递归
    D   = SMA(K,   M2, 1)
    J   = 3K − 2D
    """
    llv = low.rolling_min(window_size=n, min_samples=1)
    hhv = high.rolling_max(window_size=n, min_samples=1)
    rsv = pl.when(hhv - llv == 0).then(50.0).otherwise((close - llv) / (hhv - llv) * 100)
    k = sma_cn(rsv, m1, 1)
    d = sma_cn(k, m2, 1)
    return {"K": k, "D": d, "J": 3 * k - 2 * d}


def rsi(close: pl.Expr, n: int = 14) -> pl.Expr:
    """RSI（通达信口径，用 SMA(X,N,1)）。"""
    diff = close.diff()
    up = pl.when(diff > 0).then(diff).otherwise(0.0)
    dn = pl.when(diff < 0).then(-diff).otherwise(0.0)
    return sma_cn(up, n, 1) / (sma_cn(up, n, 1) + sma_cn(dn, n, 1)) * 100


def boll(close: pl.Expr, n: int = 20, k: float = 2.0) -> dict[str, pl.Expr]:
    """BOLL（通达信口径）。

    ★ 注意用 **总体标准差 ddof=0**（通达信如此）；用样本标准差 ddof=1 会有小差异。
    """
    mid = ma(close, n)
    std = close.rolling_std(window_size=n, min_samples=n, ddof=0)
    return {"MID": mid, "UPPER": mid + k * std, "LOWER": mid - k * std}


def atr(high: pl.Expr, low: pl.Expr, close: pl.Expr, n: int = 14) -> pl.Expr:
    """ATR（真实波幅均值）。"""
    pc = close.shift(1)
    tr = pl.max_horizontal(high - low, (high - pc).abs(), (low - pc).abs())
    return ma(tr, n)


# ══════════════════════════════════════════════════════════════════
# 四、取数窗口工具
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Window:
    """一次指标计算的窗口规划。"""
    need: int          # 用户要看的根数
    warmup: int        # 预热根数
    total: int         # 实际需要取的根数
    reason: str        # 为什么

    def __str__(self) -> str:
        return (f"展示 {self.need} 根 + 预热 {self.warmup} 根 = {self.total} 根"
                f"（{self.reason}）")


def plan_window(need: int, indicator: str) -> Window:
    """按指标算出需要取多少根 —— **移动端不能"只取 need 根就算"**。

    ★ 依据：EMA 类递归指标的开头若干根受"从哪开始算"影响，差异集中在开头约 2N 根。
      取数窗口 = 展示根数 + 预热，结果才与全历史一致。
    """
    warm = WARMUP_BARS.get(indicator, DEFAULT_WARMUP)
    return Window(need, warm, need + warm,
                  f"{indicator} 需要 {warm} 根预热，否则序列开头的值会与全历史不一致")


def with_indicators(df: pl.DataFrame, need: int = 250,
                    which: tuple[str, ...] = ("ma", "ema", "macd", "kdj", "rsi", "boll", "atr"),
                    ) -> tuple[pl.DataFrame, dict[str, str]]:
    """给 K 线 DataFrame 批量算指标。

    df 需含列：date / open / high / low / close（小写）。
    返回 (带指标的 df 只保留最后 need 根, 口径声明 dict)。

    ★★ 关键：**先把全部数据算完，再截取最后 need 根** ——
      反过来（先截取再算）就会踩到"开头未预热"的坑。
    """
    c, h, l = pl.col("close"), pl.col("high"), pl.col("low")
    exprs: list[pl.Expr] = []
    calibers: dict[str, str] = {}

    if "ma" in which:
        for n in (5, 10, 20, 60):
            exprs.append(ma(c, n).alias(f"MA{n}"))
        calibers["MA"] = "简单移动平均"
    if "ema" in which:
        for n in (12, 26):
            exprs.append(ema_tdx(c, n).alias(f"EMA{n}"))
        calibers["EMA"] = INDICATOR_CALIBER["ema"]
    if "macd" in which:
        m = macd(c)
        for k, v in m.items():
            exprs.append(v.alias(k))
        calibers["MACD"] = INDICATOR_CALIBER["macd"]
    if "kdj" in which:
        for k, v in kdj(h, l, c).items():
            exprs.append(v.alias(k))
        calibers["KDJ"] = INDICATOR_CALIBER["kdj"]
    if "rsi" in which:
        for n in (6, 14):
            exprs.append(rsi(c, n).alias(f"RSI{n}"))
        calibers["RSI"] = INDICATOR_CALIBER["rsi"]
    if "boll" in which:
        for k, v in boll(c).items():
            exprs.append(v.alias(f"BOLL_{k}"))
        calibers["BOLL"] = INDICATOR_CALIBER["boll"]
    if "atr" in which:
        exprs.append(atr(h, l, c).alias("ATR14"))
        calibers["ATR"] = INDICATOR_CALIBER["atr"]

    out = df.with_columns(exprs)
    return out.tail(need), calibers
