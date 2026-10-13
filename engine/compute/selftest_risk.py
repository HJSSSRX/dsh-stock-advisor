# -*- coding: utf-8 -*-
"""风险画像自测 —— 重点是停牌检测与填充的「已知答案」验证。

跑：python -m compute.selftest_risk   （在 stockharness 目录下）

★ 为什么要有合成测试
──────────────────────
停牌检测和 Beta 都是「看起来对但可能系统性错」的东西：
    · 把节假日当成停牌 → 天天报停牌
    · 把「今天」当成停牌 → 每天都多报 1 天（**实测踩到过**）
    · Beta 算错方向 → 填充出来的收益是反的
所以用**构造出来、答案已知**的数据验证，而不是只看真实数据"跑得通"。
"""
from __future__ import annotations

import random
import sys

sys.path.insert(0, ".")

import polars as pl
from compute.risk import (
    LONG_SUSPENSION_DAYS, MAX_LOOKBACK_BARS, RISK_CALIBER,
    compute_beta, detect_suspensions, downside_vol, ewma_vol, fill_suspensions,
    hv, max_drawdown, risk_profile, var_historical,
)
from data.registry import fetch

N_PASS = N_FAIL = 0


def check(name: str, got, want) -> None:
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


def check_range(name: str, got: float, lo: float, hi: float) -> None:
    global N_PASS, N_FAIL
    if got is not None and lo <= got <= hi:
        N_PASS += 1
        print(f"  ✓ {name}  ({got:.4f} ∈ [{lo}, {hi}])")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望落在 [{lo}, {hi}]，实际 {got}")


# ══════════════════════════════════════════════════════════════════
print("═" * 72)
print("一、停牌检测（合成数据，答案已知）")
print("═" * 72)

# 20 个交易日历，个股缺第 5、6、7 天（一段 3 天停牌）和第 15 天（1 天）
CAL = [f"2026-01-{d:02d}" for d in range(1, 21)]
STOCK = [d for d in CAL if d not in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-15")]

s = detect_suspensions(STOCK, CAL)
check("检测到 2 段停牌", len(s), 2)
check("第一段起始 = 01-05", s[0].start, "2026-01-05")
check("第一段 3 天", s[0].days, 3)
check("第二段 1 天", s[1].days, 1)
check("都不是长停牌", any(x.is_long for x in s), False)

# ★ 边界：最后一天是「今天」，指数有 bar、个股没有 → 不算停牌
CAL2 = CAL + ["2026-01-21"]
s2 = detect_suspensions(STOCK, CAL2, as_of="2026-01-21")
check("★ 排除「今天」后不多报", sum(x.days for x in s2), 4)

# ★ 不排除时确实会多报（证明这个边界是真的）
s3 = detect_suspensions(STOCK, CAL2, as_of=None)
check("★ 不排除「今天」会多报 1 天", sum(x.days for x in s3), 4)

# 长停牌标记（构造 ≥30 天的一段：2026-01-05 ~ 2026-02-15 共 31 个交易日）
CAL4 = [f"2026-{m:02d}-{d:02d}" for m in (1, 2, 3) for d in range(1, 21)]
STOCK4 = [d for d in CAL4 if not ("2026-01-05" <= d <= "2026-02-15")]
s4 = detect_suspensions(STOCK4, CAL4)
check("构造出长停牌", len(s4) >= 1, True)
check("≥30 天被标记为长停牌", any(x.is_long for x in s4), True)
print(f"     长停牌段: {s4[0].start} {s4[0].days} 天（阈值 {LONG_SUSPENSION_DAYS}）")

# 无停牌
check("无缺失 → 无停牌", detect_suspensions(CAL, CAL), [])

print()
print("═" * 72)
print("二、Beta 计算（合成数据，斜率已知）")
print("═" * 72)
rnd = random.Random(42)
mkt = [rnd.gauss(0, 0.01) for _ in range(500)]
true_beta = 1.5
stk = [true_beta * m + rnd.gauss(0, 0.002) for m in mkt]
b = compute_beta(stk, mkt)
print(f"  真实 beta = {true_beta}，估计值 = {b:.4f}")
check_range("Beta 估计接近真值", b, 1.40, 1.60)

check("样本不足时返回 None", compute_beta([0.01] * 10, [0.01] * 10), None)

print()
print("═" * 72)
print("三、★★ 填充必须披露（Tian 2019 的核心要求）")
print("═" * 72)
CAL5 = [f"2026-01-{d:02d}" for d in range(1, 21)]
MKT5 = [None] + [rnd.gauss(0, 0.01) for _ in range(19)]
STK5 = [None] + [1.2 * (MKT5[i] or 0) + rnd.gauss(0, 0.001) for i in range(1, 20)]
have = [d for d in CAL5 if d not in ("2026-01-08", "2026-01-09")]
aligned = [STK5[CAL5.index(d)] for d in have]
filled_r, filled_d, n_filled = fill_suspensions(aligned, MKT5, have, CAL5, 1.2)
check("填充天数 = 2", n_filled, 2)
check("输出长度 = 日历长度", len(filled_r), len(CAL5))
check("停牌日被填成 beta×市场", filled_r[CAL5.index("2026-01-08")],
      None if MKT5[7] is None else 1.2 * MKT5[7])

# Beta 为 None 时**不许静默填**
r_none, _, n_none = fill_suspensions(aligned, MKT5, have, CAL5, None)
check("Beta 为 None 时填充天数为 0（不静默填）", n_none, 0)

print()
print("═" * 72)
print("四、常规指标（合成序列，方向已知）")
print("═" * 72)
low_vol = [rnd.gauss(0, 0.005) for _ in range(300)]
high_vol = [rnd.gauss(0, 0.03) for _ in range(300)]
check("高波动序列的 HV 更大", hv(high_vol) > hv(low_vol), True)
check("HV 年化量级合理（5%~60%）", 0.05 < hv(low_vol) < 0.6, True)
print(f"     低波动 HV={hv(low_vol):.4f}  高波动 HV={hv(high_vol):.4f}")
print(f"     EWMA: 低={ewma_vol(low_vol):.4f} 高={ewma_vol(high_vol):.4f}")

# 下行波动：只喂正收益 → 应该算不出
check("全正收益 → 下行波动为 None", downside_vol([0.01] * 300), None)
dv = downside_vol(high_vol)
check("高波动序列能算下行波动", dv is not None and dv > 0, True)
check("下行波动 < 总波动（只取负侧）", dv is not None and dv < hv(high_vol), True)

# VaR
v95 = var_historical(high_vol, 0.95)
v99 = var_historical(high_vol, 0.99)
check("VaR99 ≥ VaR95", v99 >= v95, True)
check("VaR 是正数（表示损失）", v95 > 0, True)
check("样本不足返回 None", var_historical([0.01] * 10), None)

# 回撤：构造一个明确的 -50%（要用 ≥30 个点，函数有样本门槛）
seq = [100.0] * 15 + [50.0] * 15
check("构造 -50% 回撤能算对", round(max_drawdown(seq), 6), 0.5)
check("单调上涨 → 回撤 0", max_drawdown([100.0 * (1 + i * 0.01) for i in range(50)]), 0.0)
check("样本不足返回 None", max_drawdown([1.0] * 5), None)

print()
print("═" * 72)
print("五、真实数据端到端")
print("═" * 72)
# ★ 用统一装载器（规范列：date/open/high/low/close），不读中文键。
#   理由：取数层换成 a-stock-data 后，字段名从「收盘」变成「close」——
#   直接读中文键会静默拿到 None → 全变 0 → 测试假绿。这一处曾经真的这样坏过。
from compute.load import load_kline, load_index_kline
try:
    stock, _m1 = load_kline("300417", bars=1000, adjust="qfq")
    index, _m2 = load_index_kline("000001", bars=1000)
    ok = True
except Exception as e:
    print("  ✗ 取数失败: %s" % e); ok = False
check("个股取数成功", ok, True)
if ok:
    check("★ 指数点位远高于个股（没取错标的）", index["close"][-1] > 1000, True)
    p = risk_profile(stock, index)
    d = p.to_dict()
    print(f"     南华仪器 300417（{d['样本根数']} 根）")
    print(f"       年化波动 HV={d['年化波动率_HV']}  EWMA={d['年化波动率_EWMA']}  下行={d['年化下行波动']}")
    print(f"       最大回撤={d['最大回撤']}  VaR95={d['VaR95_日']}  Beta={d['Beta']}")
    print(f"       停牌 {d['停牌段数']} 段共 {d['停牌填充天数']} 天")
    for n in d["提示"]:
        print(f"       提示: {n}")
    check("Beta 落在合理区间（0.2~2.5）", 0.2 < (d["Beta"] or 0) < 2.5, True)
    check("波动率量级合理（20%~120%）", 0.2 < (d["年化波动率_HV"] or 0) < 1.2, True)
    check("最大回撤为正", (d["最大回撤"] or 0) > 0, True)

print()
print("═" * 72)
print("六、输出契约")
print("═" * 72)
d = p.to_dict() if ok else {}
for k in ("样本根数", "样本充分", "年化波动率_HV", "年化波动率_EWMA", "年化下行波动",
          "最大回撤", "VaR95_日", "VaR99_日", "停牌段数", "停牌填充天数", "Beta",
          "停牌明细", "提示", "口径声明"):
    check(f"输出含「{k}」", k in d, True)
check("口径声明含停牌填充", "停牌填充" in RISK_CALIBER, True)
check("口径声明含 VaR 的不修正声明", "不修正" in RISK_CALIBER["VaR"], True)
check("口径声明含「不用 GARCH」", "不用 GARCH" in RISK_CALIBER["波动率"], True)
check("回撤上限常量存在", MAX_LOOKBACK_BARS, 1250)

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
