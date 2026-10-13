# -*- coding: utf-8 -*-
"""计算层·制度规则 自测。

跑：python -m compute.selftest   （在 stockharness 目录下）

★ 其中「制度判定必须用不复权价」那一组是**实测踩出来的**：
  用复权价判涨停，7 次涨停会全部漏掉。
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, ".")
from compute.rules import (  # noqa: E402
    Board, CALIBER_NOTES, Status, board_of, classify_bar, limit_pct,
)

N_PASS = N_FAIL = 0


def check(name: str, got, want) -> None:
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


print("═" * 70)
print("一、板块判定（含两个真实踩过的坑：920xxx 是北交所、900xxx 是 B 股）")
print("═" * 70)
for code, want in [("300417", Board.GEM), ("301001", Board.GEM), ("688379", Board.STAR),
                   ("689009", Board.UNKNOWN), ("600519", Board.MAIN), ("000001", Board.MAIN),
                   ("002415", Board.MAIN), ("003816", Board.MAIN),
                   ("920627", Board.BSE), ("430047", Board.BSE), ("831010", Board.BSE),
                   ("900901", Board.UNKNOWN), ("200011", Board.UNKNOWN), ("abc", Board.UNKNOWN)]:
    check(f"{code} → {want.value}", board_of(code), want)

print()
print("═" * 70)
print("二、★ 创业板改革（2020-08-24）：这是 hikyuu 自己承认「不按历史分段」的地方")
print("═" * 70)
check("改革前一天 300417 = 10%", limit_pct("300417", date(2020, 8, 21)).limit_pct, 0.10)
check("改革当天 300417 = 20%", limit_pct("300417", date(2020, 8, 24)).limit_pct, 0.20)
check("改革前一天 600519 仍是 10%", limit_pct("600519", date(2020, 8, 21)).limit_pct, 0.10)

print()
print("═" * 70)
print("三、各板块现行幅度")
print("═" * 70)
check("主板 10%", limit_pct("600519", "2026-10-10").limit_pct, 0.10)
check("创业板 20%", limit_pct("300417", "2026-10-10").limit_pct, 0.20)
check("科创板 20%", limit_pct("688379", "2026-10-10").limit_pct, 0.20)
check("北交所 30%", limit_pct("920627", "2026-10-10").limit_pct, 0.30)

print()
print("═" * 70)
print("四、不确定的情况必须**显式标出来**，不许猜")
print("═" * 70)
r = limit_pct("300417", "2026-10-10", new_listing_days=3)
check("新股第 3 天 → 无幅度", r.limit_pct, None)
check("新股 → 标记 uncertain", r.uncertain, True)
r = limit_pct("920627", "2020-01-01")
check("北交所票在开市前 → 无幅度", r.limit_pct, None)
check("开市前 → 标记 uncertain", r.uncertain, True)
r = limit_pct("688379", "2019-01-01")
check("科创板开市前 → uncertain", r.uncertain, True)
check("主板 ST → 5%", limit_pct("600519", "2026-10-10", Status.ST).limit_pct, 0.05)

print()
print("═" * 70)
print("五、涨跌停判定（用真实价格样本）")
print("═" * 70)
# 2024-04-17 南华仪器：前收 5.85 → 收 7.02（实测涨停，20%）
b = classify_bar("300417", "2024-04-17", 5.85, 6.10, 7.02, 6.05, 7.02)
check("涨停价算对（5.85×1.2=7.02）", b.upper, 7.02)
check("判定为涨停", b.is_limit_up, True)
check("不是一字板", b.is_one_word, False)

# 一字板构造：开=高=低=收=涨停价
b = classify_bar("300417", "2024-04-17", 5.85, 7.02, 7.02, 7.02, 7.02)
check("一字板被识别", b.is_one_word, True)
# T 字板：开=收=涨停价，但盘中有回落
b = classify_bar("300417", "2024-04-17", 5.85, 7.02, 7.02, 6.50, 7.02)
check("T 字板被识别", b.is_t_word, True)
check("T 字板不算一字板", b.is_one_word, False)

# 跌停
b = classify_bar("300417", "2024-04-17", 10.00, 8.50, 8.60, 8.00, 8.00)
check("跌停价算对（10×0.8=8.00）", b.lower, 8.00)
check("判定为跌停", b.is_limit_down, True)

# 主板 10%
b = classify_bar("600519", "2024-04-17", 100.0, 110.0, 110.0, 109.0, 110.0)
check("主板涨停价 110", b.upper, 110.0)
check("主板涨停成立", b.is_limit_up, True)

print()
print("═" * 70)
print("六、★ 口径声明必须存在（这批是给输出层用的，缺了就说不清）")
print("═" * 70)
for k in ("复权", "制度判定必须用不复权价", "停牌", "涨跌停截断", "主力资金口径"):
    check(f"CALIBER_NOTES 含「{k}」", k in CALIBER_NOTES, True)
note = CALIBER_NOTES["制度判定必须用不复权价"]
check("口径声明写清了实测证据", "+20.53%" in note and "fqt=0" in note, True)

print()
print("═" * 70)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 70)
sys.exit(1 if N_FAIL else 0)
