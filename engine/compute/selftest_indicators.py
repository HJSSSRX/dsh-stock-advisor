# -*- coding: utf-8 -*-
"""指标层验证 —— 重点证明「固定取数窗口」是必需的。

核心实验：
  ① 用全历史算 MACD → 取最后 250 根（基准）
  ② 只取 250 根算 MACD → 对比基准
  ③ 用 plan_window 取 250+预热 根算 → 对比基准
  期望：②有差异，③与基准一致。
"""
import sys

sys.path.insert(0, ".")

import polars as pl
from compute.indicators import (
    INDICATOR_CALIBER, ema_tdx, kdj, macd, plan_window, rsi, sma_cn, with_indicators,
)
from data.registry import fetch

N_PASS = N_FAIL = 0


def check(name, got, want):
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


def load(code="300417", bars=2000):    # ★ 必须走统一装载器（规范列：date/open/high/low/close）
    #   直接用 fetch() 再读中文键会**静默拿到 None → 全变 0**，
    #   测试会一边显示"通过"一边在验算 0。这个坑踩过两次（selftest_risk / selftest_position）。
    from compute.load import load_kline
    df, _meta = load_kline(code, bars=bars, adjust="none")
    return df


print("═" * 72)
print("一、通达信口径基础函数")
print("═" * 72)
df = load()
print(f"  数据: {len(df)} 根  {df['date'][0]} ~ {df['date'][-1]}")
c = pl.col("close")

# 通达信 EMA 必须从第一根就有值
e = df.select(ema_tdx(c, 12).alias("e"))["e"]
check("通达信 EMA 第一根就有值（首样本播种）", e[0] is not None, True)
check("第一根 EMA == 第一根收盘（播种值）", round(e[0], 6), round(df["close"][0], 6))

# 递推关系验证：Y[i] = X[i]*2/(N+1) + Y[i-1]*(N-1)/(N+1)
n = 12
alpha = 2.0 / (n + 1)
manual = [df["close"][0]]
for i in range(1, 20):
    manual.append(df["close"][i] * alpha + manual[-1] * (1 - alpha))
check("递推公式逐根吻合", [round(x, 6) for x in e[:20].to_list()],
      [round(x, 6) for x in manual])

# 通达信 SMA 是加权递归，不是算术平均
s = df.select(sma_cn(c, 9, 3).alias("s"))["s"]
manual_s = [df["close"][0]]
for i in range(1, 10):
    manual_s.append(df["close"][i] * (3 / 9) + manual_s[-1] * (1 - 3 / 9))
check("SMA_CN 是加权递归（alpha=M/N）", [round(x, 6) for x in s[:10].to_list()],
      [round(x, 6) for x in manual_s])

print()
print("═" * 72)
print("二、★★ 固定取数窗口的必要性（这是本层的核心机制）")
print("═" * 72)
NEED = 250
w = plan_window(NEED, "macd")
print(f"  窗口规划: {w}")

full = df.with_columns([v.alias(k) for k, v in macd(c).items()])
base = full.tail(NEED).select(["date", "DIF", "DEA", "MACD"])

naive = df.tail(NEED).with_columns([v.alias(k) for k, v in macd(c).items()]) \
          .select(["date", "DIF", "DEA", "MACD"])

warm = df.tail(w.total).with_columns([v.alias(k) for k, v in macd(c).items()]) \
        .tail(NEED).select(["date", "DIF", "DEA", "MACD"])

def maxdiff(a, b, col):
    d = (a[col] - b[col]).abs()
    return max((x for x in d.to_list() if x is not None), default=0.0)

print()
print("  ── ① 只取 %d 根直接算（错误做法）vs 全历史基准 ──" % NEED)
for col in ("DIF", "DEA", "MACD"):
    print("     %-5s 最大差异 %.8f" % (col, maxdiff(naive, base, col)))
d_naive = maxdiff(naive, base, "DIF")

print()
print("  ── ② 取 %d 根（含预热）再截最后 %d 根 vs 全历史基准 ──" % (w.total, NEED))
for col in ("DIF", "DEA", "MACD"):
    print("     %-5s 最大差异 %.8f" % (col, maxdiff(warm, base, col)))
d_warm = maxdiff(warm, base, "DIF")

check("含预热后与全历史一致（差异 < 1e-9）", d_warm < 1e-9, True)
check("不含预热会算错（差异显著）", d_naive > 1e-6, True)
print(f"      → 不含预热 DIF 最大偏差 {d_naive:.6f}；含预热 {d_warm:.2e}")

print()
print("═" * 72)
print("三、批量指标 + 口径声明")
print("═" * 72)
out, calibers = with_indicators(df, need=5)
cols = [k for k in out.columns if k not in ("date", "open", "high", "low", "close")]
print("  产出指标列:", cols)
check("口径声明已附带", len(calibers) >= 6, True)
print("  ── 最后 3 根 ──")
for row in out.tail(3).iter_rows(named=True):
    print("    %s  C=%.2f  DIF=%.4f DEA=%.4f MACD=%.4f  K=%.1f D=%.1f J=%.1f  RSI14=%.1f"
          % (row["date"], row["close"], row["DIF"], row["DEA"], row["MACD"],
             row["K"], row["D"], row["J"], row["RSI14"]))

print()
print("  ── 口径声明（用户问「为什么和软件不一样」时直接引用）──")
for k, v in calibers.items():
    print(f"    【{k}】{v[:96]}")

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
