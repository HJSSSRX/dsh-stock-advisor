# -*- coding: utf-8 -*-
"""位置分析自测 —— 重点是那个「随机游走对照组」。

跑：python -m compute.selftest_position   （在 stockharness 目录下）

★ 为什么必须有随机游走对照组
──────────────────────────────
A股 3 万条规则经 SPA 校正后只剩 0.57% 显著（Jiang/Song/Tong, IRF）。
**在纯随机序列上，任何「支撑压力位」算法都会找出"位"并给出强度分。**
如果随机游走上算出的反弹概率不是 ≈ 0.5，那这个强度分**只是在复述算法自己的偏差**，
不是市场性质 —— 那种分拿去给用户看就是骗人。

本文件用 200 条随机游走检验这一点。
"""
from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, ".")

import polars as pl
from compute.position import (
    LEVEL_CALIBER, MIN_BARS_FOR_LEVELS, MIN_TOUCHES_FOR_STRENGTH,
    average_increment, find_levels,  # noqa: F401
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
    if lo <= got <= hi:
        N_PASS += 1
        print(f"  ✓ {name}  ({got:.4f} ∈ [{lo}, {hi}])")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望落在 [{lo}, {hi}]，实际 {got:.4f}")


def random_walk(n: int, seed: int, vol: float = 0.02) -> pl.DataFrame:
    """生成一条随机游走的价格序列（几何布朗运动的离散版）。"""
    rnd = random.Random(seed)
    p = 10.0
    rows = []
    for _ in range(n):
        p *= (1 + rnd.gauss(0, vol))
        p = max(p, 0.01)
        rows.append({"high": p * (1 + abs(rnd.gauss(0, vol / 3))),
                     "low": p * (1 - abs(rnd.gauss(0, vol / 3))),
                     "close": p})
    return pl.DataFrame(rows)


def real_data(code: str = "300417", bars: int = 800) -> pl.DataFrame:    # ★ 必须走统一装载器（理由同 selftest_indicators）
    from compute.load import load_kline
    df, _meta = load_kline(code, bars=bars, adjust="none")
    return df


# ══════════════════════════════════════════════════════════════════
print("═" * 72)
print("一、基本机制")
print("═" * 72)
df = real_data()
print(f"  真实数据: {len(df)} 根  {df['date'][0]} ~ {df['date'][-1]}")
ls = find_levels(df)
check("返回类型正确", type(ls).__name__, "LevelSet")
check("样本充分标记为 True", ls.sufficient, True)
check("找出了位", len(ls.levels) > 0, True)
print(f"  找到 {len(ls.levels)} 个位：")
for lv in ls.levels[:4]:
    st = f"{lv.strength:.3f}" if lv.strength is not None else "—"
    print(f"    {'支撑' if lv.kind=='support' else '压力'} "
          f"[{lv.low:.2f}, {lv.high:.2f}]  触及{lv.touches} 反弹{lv.recoveries} "
          f"强度{st}  距上次{lv.bars_since_touch}根")
check("区间下界 < 上界", all(lv.low < lv.high for lv in ls.levels), True)
check("触及次数 ≥ 反弹次数", all(lv.recoveries <= lv.touches for lv in ls.levels), True)
check("强度落在 [0,1]", all(lv.strength is None or 0 <= lv.strength <= 1 for lv in ls.levels), True)

print()
print("═" * 72)
print("二、样本不足时必须拒答（不硬答）")
print("═" * 72)
short = find_levels(df.head(MIN_BARS_FOR_LEVELS - 1))
check("短样本不出结论", short.levels, [])
check("短样本标记为不充分", short.sufficient, False)
check("短样本给出原因", bool(short.disclaimer), True)

few = find_levels(random_walk(200, seed=1, vol=0.001))
weak = [lv for lv in few.levels if lv.touches < MIN_TOUCHES_FOR_STRENGTH]
check("触及不足时强度为 None", all(lv.strength is None for lv in weak), True)
check("触及不足时写明原因", all(bool(lv.note) for lv in weak), True)

print()
print("═" * 72)
print("三、★★★ 随机游走对照组 —— 强度分是不是自欺")
print("═" * 72)
print("  在纯随机序列上，真实的「反弹概率」应该是 0.5（无记忆性）。")
print("  如果算出显著偏离 0.5，说明强度分复述的是算法偏差，不是市场性质。\n")

all_strengths: list[float] = []
per_run_mean: list[float] = []
RUNS = 200
for s in range(RUNS):
    w = random_walk(400, seed=1000 + s)
    r = find_levels(w)
    st = [lv.strength for lv in r.levels if lv.strength is not None]
    all_strengths += st
    if st:
        per_run_mean.append(statistics.mean(st))

if not all_strengths:
    check("随机游走上能算出强度分", False, True)
else:
    m = statistics.mean(all_strengths)
    sd = statistics.pstdev(all_strengths)
    print(f"  样本: {RUNS} 条随机游走 × 400 根 → {len(all_strengths)} 个强度分")
    print(f"  全部强度分: 均值 {m:.4f}  标准差 {sd:.4f}")
    if per_run_mean:
        print(f"  每条游走的均值: 均值 {statistics.mean(per_run_mean):.4f}  "
              f"标准差 {statistics.pstdev(per_run_mean):.4f}  "
              f"范围 [{min(per_run_mean):.3f}, {max(per_run_mean):.3f}]")
    print()
    # 随机游走上，均值应落在 0.5 附近
    check_range("随机游走强度均值 ≈ 0.5", m, 0.35, 0.65)

print()
print("═" * 72)
print("四、真实数据 vs 随机游走（有没有差别）")
print("═" * 72)
real_st = [lv.strength for lv in ls.levels if lv.strength is not None]
if real_st and all_strengths:
    rm = statistics.mean(real_st)
    print(f"  真实数据（南华仪器 {len(df)} 根）强度均值: {rm:.4f}  （{len(real_st)} 个位）")
    print(f"  随机游走强度均值:                        {m:.4f}")
    print(f"  差值: {rm - m:+.4f}")
    print()
    print("  ★ 注意：这**不是**「真实数据更有效」的证据 —— 单只票的样本远不够。")
    print("    要证明有效性需要横截面 + SPA 校正，那是另一个量级的工作。")
    print("    这里只确认算法可运行、不产生系统性偏差。")

print()
print("═" * 72)
print("五、输出契约（必须自带口径与免责）")
print("═" * 72)
d = ls.to_dict()
for k in ("样本根数", "样本充分", "位", "口径声明", "免责"):
    check(f"输出含「{k}」", k in d, True)
check("口径声明含区间定义", "区间定义" in LEVEL_CALIBER, True)
check("口径声明含强度定义", "强度定义" in LEVEL_CALIBER, True)
check("口径声明含「为什么不能只给一根线」", "为什么不能只给一根线" in LEVEL_CALIBER, True)
check("口径声明含已知局限", "已知局限" in LEVEL_CALIBER, True)
check("免责声明非空", bool(d["免责"]), True)
check("免责声明提到「事后统计」", "事后统计" in d["免责"], True)

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
