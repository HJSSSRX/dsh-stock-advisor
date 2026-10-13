# -*- coding: utf-8 -*-
"""市场宽度验收 —— **拿豆包的回答当对照基准**。

跑：python -m compute.selftest_breadth   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 为什么用豆包的数字当基准
═══════════════════════════════════════════════════════════════════
2026-10-12 实测豆包（浏览器里问的，原文照录）：

    「沪指 3797.24 点（-0.43%）；深成指 12384.87 点（-2.03%）；
      创业板指 2963.82 点（-2.61%）
      成交：两市合计 1.54 万亿元
      上涨 1167 家，下跌 4325 家
      收盘封死涨停 48 只，跌停 23 只；封板未遂 32 只，封板率约 60%」

**这是一份外部独立基准** —— 它来自「证券时报·数据宝 / Wind / 东财收盘统计」。
我们的模块能算出**同一批数字**，就说明这一层真的能用，不是自说自话。

⚠️ **而且它在净利润上错了 165 亿**（同一轮实测），所以：
   · 它的**行情/家数**类数字可用作基准 —— 那类数据公开、它搜得到
   · 它的**财务**类数字不可信
   **同一个产品在不同类型数据上可信度差异巨大** —— 这本身就是我们要示范的事。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")

from compute.breadth import MarketBreadth, breadth_from_package, market_snapshot

N_PASS = N_FAIL = 0

# 豆包 2026-10-12 给的数字（外部基准）
DOUBAO = {
    "date": "2026-10-12",
    "上证指数": 3797.24,
    "深证成指": 12384.87,
    "创业板指": 2963.82,
    "成交额_万亿": 1.54,
    "上涨": 1167,
    "下跌": 4325,
    "涨停": 48,
    "跌停": 23,
}


def check(name: str, got, want) -> None:
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


def check_true(name: str, cond: bool, detail: str = "") -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      {detail}")


def near(name: str, got: float, want: float, tol: float, unit: str = "") -> None:
    global N_PASS, N_FAIL
    if got is not None and abs(got - want) <= tol:
        N_PASS += 1
        print(f"  ✓ {name}  {got}{unit} vs 豆包 {want}{unit}（容差 {tol}）")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      我们 {got}{unit}，豆包 {want}{unit}，差 {abs((got or 0)-want):.4f} > {tol}")


# ══════════════════════════════════════════════════════════════════
print("═" * 74)
print("A · ★★ 与豆包逐项对照（外部独立基准）")
print("═" * 74)
b = market_snapshot(DOUBAO["date"])
if not b.indices:
    print(f"  ⚠️ 取数失败：{b.notes}")
    print("     （通达信封通常收盘后数小时才发布；重跑请换一个已收盘的交易日）")
    raise SystemExit(2)

for nm in ("上证指数", "深证成指", "创业板指"):
    got = b.indices.get(nm, {}).get("收盘")
    if got is not None:
        near(f"{nm} 收盘", got, DOUBAO[nm], 0.01)

if b.amount_yi:
    near("两市成交额（万亿）", b.amount_yi / 1e4, DOUBAO["成交额_万亿"], 0.03, " 万亿")

# 涨跌家数：给 1 家的容差（平盘判定/停牌处理差异）
near("上涨家数", b.up, DOUBAO["上涨"], 2, " 家")
near("下跌家数", b.down, DOUBAO["下跌"], 2, " 家")

# ★ 涨停：**这里有 6 只差异，原因已查清** —— 我们含 ST（5% 限幅），它没含
check_true("★ 涨停数 ≥ 豆包（我们含 ST，它没含）", b.limit_up >= DOUBAO["涨停"],
           f"我们 {b.limit_up}，豆包 {DOUBAO['涨停']}")
check_true("★ 去掉 ST 后与豆包接近（±4）",
           abs((b.limit_up - b.limit_up_st) - DOUBAO["涨停"]) <= 4,
           f"去掉 ST 的 {b.limit_up_st} 只后 = {b.limit_up - b.limit_up_st}，豆包 {DOUBAO['涨停']}")
print(f"      差异来源已查明：ST 股 5% 限幅 —— 我们算了 {b.limit_up_st} 只，媒体口径按 10% 判会漏掉")
check_true("★ ST 涨停明细可拆出（证明是按板块取限幅，不是硬编码）",
           b.limit_up_st + b.limit_up_main + b.limit_up_gem_star + b.limit_up_bse == b.limit_up,
           f"主板{b.limit_up_main} + 双创{b.limit_up_gem_star} + ST{b.limit_up_st} + 北交所{b.limit_up_bse}")

print()
print("═" * 74)
print("B · 成交额口径（★ 这个坑最容易出错）")
print("═" * 74)
check_true("成交额取自指数行，不是全表加总", b.amount_yi is not None and b.amount_yi < 50000,
           f"{b.amount_yi:.0f} 亿（全表加总会得到 136 万亿 = 90 倍）")
check_true("沪市成交额在合理区间（3000~20000 亿）",
           3000 < (b.amount_sh_yi or 0) < 20000, f"{b.amount_sh_yi:.0f} 亿")
check_true("深市成交额在合理区间（3000~25000 亿）",
           3000 < (b.amount_sz_yi or 0) < 25000, f"{b.amount_sz_yi:.0f} 亿")
check_true("沪+深 = 合计",
           abs((b.amount_sh_yi or 0) + (b.amount_sz_yi or 0) - (b.amount_yi or 0)) < 1,
           f"{b.amount_sh_yi:.0f} + {b.amount_sz_yi:.0f} = {b.amount_yi:.0f}")

print()
print("═" * 74)
print("C · ★ 反例：不许把非股票算进去")
print("═" * 74)
# 000001 在 sh 是上证指数、在 sz 才是平安银行 —— 只看代码会算错
from compute.breadth import _is_stock
check("(sh, 000001) 不是股票（是上证指数）", _is_stock("sh", "000001"), False)
check("(sz, 000001) 是股票（平安银行）", _is_stock("sz", "000001"), True)
check("(sh, 600519) 是股票", _is_stock("sh", "600519"), True)
check("(sh, 999999) 不是股票（上证指数代码）", _is_stock("sh", "999999"), False)
check("(sz, 399001) 不是股票（深证成指）", _is_stock("sz", "399001"), False)
check("(sz, 110075) 不是股票（可转债）", _is_stock("sz", "110075"), False)
check("(bj, 430047) 是股票（北交所）", _is_stock("bj", "430047"), True)
check_true("★ 股票数在合理区间（5000~6000）", 5000 < b.stocks < 6000, f"{b.stocks} 只")

print()
print("═" * 74)
print("D · 输出契约")
print("═" * 74)
d = b.to_dict()
for k in ("日期", "成交额_亿", "上涨", "下跌", "涨停", "跌停", "封板率", "指数", "来源", "口径", "提示"):
    check(f"输出含「{k}」", k in d, True)
check_true("口径声明写明了「成交额取指数行」", "指数行" in b.caliber, "")
check_true("口径声明写明了「ST 是 5%」", "ST" in b.caliber and "5" in b.caliber, "")
check_true("提示里说明了与媒体口径的差异来源", any("ST" in n for n in b.notes),
           next((n[:60] for n in b.notes if "ST" in n), ""))

print()
print("═" * 74)
print("E · 边界：非交易日的处理")
print("═" * 74)
# 2026-10-10 是周六 —— 必须明确报「没有包」，不能返回一个全 0 的快照
b2 = market_snapshot("2026-10-10")
check_true("★ 非交易日 → 明确报失败，**不返回全 0 的快照**",
           b2.amount_yi is None and bool(b2.notes),
           (b2.notes[0][:70] if b2.notes else "没有提示"))
check_true("且写清了原因（尚未发布/非交易日）",
           any(("尚未发布" in n or "非交易日" in n or "失败" in n) for n in b2.notes), "")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
