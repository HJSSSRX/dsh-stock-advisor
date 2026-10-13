# -*- coding: utf-8 -*-
"""实测：每个指标真正需要多少根预热，才能与「全历史计算」完全一致。

原理：EMA 类递归指标的初始误差按 (1 - 2/(N+1)) 每根衰减。
      N 越大衰减越慢；叠加层数越多（MACD 有三层 EMA）需要越久。
      所以预热根数**不能拍脑袋**，必须实测。
"""
import sys

sys.path.insert(0, ".")

import polars as pl
from compute.indicators import atr, boll, ema_tdx, kdj, ma, macd, rsi
from data.registry import fetch

TOL = 1e-9
NEED = 250
MAX_WARM = 600


def load(code="300417", bars=3000):
    r = fetch("kline", code, {"freq": "daily", "limit": bars, "fqt": 1})
    if not r.ok:
        raise SystemExit("取数失败: " + r.why())
    return pl.DataFrame([
        {"date": str(x.get("日期"))[:10], "open": float(x.get("开盘") or 0),
         "high": float(x.get("最高") or 0), "low": float(x.get("最低") or 0),
         "close": float(x.get("收盘") or 0)}
        for x in r.data
    ]).sort("date")


# 每个指标：名字 → 计算函数（返回 dict[str, Expr]）
def compute(name, df):
    c, h, l = pl.col("close"), pl.col("high"), pl.col("low")
    if name == "MA60":
        return {"v": ma(c, 60)}
    if name == "EMA26":
        return {"v": ema_tdx(c, 26)}
    if name == "MACD":
        return macd(c)
    if name == "KDJ":
        return kdj(h, l, c)
    if name == "RSI14":
        return {"v": rsi(c, 14)}
    if name == "BOLL":
        return boll(c)
    if name == "ATR14":
        return {"v": atr(h, l, c)}
    raise ValueError(name)


INDICATORS = ["MA60", "EMA26", "MACD", "KDJ", "RSI14", "BOLL", "ATR14"]

df = load()
print(f"  基准数据: {len(df)} 根  {df['date'][0]} ~ {df['date'][-1]}")
print(f"  判定标准: 与全历史计算的最大差异 < {TOL:g}\n")

# 全历史基准
base = {}
for name in INDICATORS:
    exprs = compute(name, df)
    full = df.with_columns([v.alias(k) for k, v in exprs.items()]).tail(NEED)
    base[name] = full.select(list(exprs.keys()))

print("  %-8s %8s   %s" % ("指标", "需要预热", "说明"))
print("  " + "-" * 60)
results = {}
for name in INDICATORS:
    exprs = compute(name, df)
    keys = list(exprs.keys())
    needed = None
    for warm in range(0, MAX_WARM + 1, 5):
        sub = df.tail(NEED + warm).with_columns([v.alias(k) for k, v in exprs.items()]).tail(NEED)
        got = sub.select(keys)
        ok = True
        for k in keys:
            d = (got[k] - base[name][k]).abs()
            m = max((x for x in d.to_list() if x is not None), default=0.0)
            if m >= TOL:
                ok = False
                break
        if ok:
            needed = warm
            break
    results[name] = needed
    note = "（< %d 根内收敛）" % MAX_WARM if needed is None else ""
    print("  %-8s %8s   %s" % (name, needed if needed is not None else ">%d" % MAX_WARM, note))

print()
print("  ══ 差距演示：不同预热下的最大偏差（MACD）══")
exprs = compute("MACD", df)
keys = list(exprs.keys())
print("  %8s %14s %14s %14s" % ("预热", "DIF", "DEA", "MACD"))
for warm in (0, 30, 60, 120, 180, 240, 300):
    sub = df.tail(NEED + warm).with_columns([v.alias(k) for k, v in exprs.items()]).tail(NEED)
    got = sub.select(keys)
    diffs = []
    for k in keys:
        d = (got[k] - base["MACD"][k]).abs()
        diffs.append(max((x for x in d.to_list() if x is not None), default=0.0))
    print("  %8d %14.8f %14.8f %14.8f" % (warm, diffs[0], diffs[1], diffs[2]))

print()
print("  ══ 建议写入 WARMUP_BARS 的值（留 20% 余量）══")
suggest = {}
for name, w in results.items():
    key = name.rstrip("0123456789").lower()
    if w is not None:
        suggest[key] = int(w * 1.2) + 5
for k, v in suggest.items():
    print("      %-8s %d" % (k, v))
