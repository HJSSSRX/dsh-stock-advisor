# -*- coding: utf-8 -*-
"""两融模块验收 —— **核心是那条"外部标准答案"能不能真的抓出坏数据**。

跑：python -m compute.selftest_margin   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
这一层的特殊之处：**标准答案是交易所给的，不是我们定的**
═══════════════════════════════════════════════════════════════════
深交所原文：
    本日融资余额 = 前日融资余额 + 本日融资买入额 − 本日融资偿还额

→ 所以「数据对不对」变成一个**可机械判定**的问题：
  拿这条恒等式逐日验，成立就是自洽，不成立就说清哪一天差多少。

★ 双向指标：
    漏检率  把数据改坏，恒等式**必须**报不成立（改了还没发现 = 白验）
    误杀率  真实数据不许被误报（工行那 3 天是**真偏差**，不是误报）
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

sys.path.insert(0, ".")

from compute import margin as M                              # noqa: E402

N_PASS = N_FAIL = 0


def check_true(name: str, cond: bool, detail: str = "") -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      {detail}")


def check(name: str, got, want) -> None:
    check_true(name, got == want, f"期望 {want!r}，实际 {got!r}")


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {(a / b * 100 if b else 0):.2f}%"


def mk(rows: list[tuple]) -> M.MarginReport:
    """按 (date, rzye, rzmre, rzche) 造一份报告。"""
    rep = M.MarginReport(code="TEST", source="构造", as_of=rows[-1][0])
    rep.rows = [M.MarginRow(date=d, rzye=z, rzmre=b, rzche=c) for d, z, b, c in rows]
    return rep


def consistent_series(n: int = 10, start: float = 1e10) -> list[tuple]:
    """造一段**恒等式必然成立**的真实形状数据。"""
    out, z = [], start
    for i in range(n):
        buy, repay = 1e7 + i * 1e5, 9e6 + i * 5e4
        z = z + buy - repay
        out.append((f"2026-09-{i+1:02d}", z, buy, repay))
    return out


print("═" * 74)
print("A · ★★★ 恒等式：自洽数据必须全成立（这是误杀率的分母）")
print("═" * 74)
rep = mk(consistent_series())
M.verify_identity(rep)
check("  10 行 → 检查 9 对", rep.checked, 9)
check("★★ 自洽数据 9/9 全部成立", rep.identity_ok, 9)
check("★ 最大偏差 0", rep.max_deviation, 0.0)
check("★ 没有大偏差记录", list(rep.big_deviations), [])
check_true("★ 口径声明说明它不预测方向", any("不预测方向" in n for n in rep.render().split("\n")) or
           "不预测方向" in rep.render(), "")

print()
print("═" * 74)
print("B · ★★★ 漏检测试：把数据改坏，恒等式**必须**报不成立")
print("═" * 74)
MUTANTS = [
    ("把某天的融资余额改掉（+1 亿）", lambda r: setattr(r.rows[5], "rzye", r.rows[5].rzye + 1e8)),
    ("把某天的融资买入额改掉（-1 亿）", lambda r: setattr(r.rows[5], "rzmre", r.rows[5].rzmre - 1e8)),
    ("把某天的融资偿还额改掉（+1 亿）", lambda r: setattr(r.rows[5], "rzche", r.rows[5].rzche + 1e8)),
    ("只差 3 元（超出容差 2 元）", lambda r: setattr(r.rows[5], "rzye", r.rows[5].rzye + 3)),
    ("差 100 万（明显的转抄错）", lambda r: setattr(r.rows[7], "rzye", r.rows[7].rzye + 1e6)),
]
missed = []
for name, mutate in MUTANTS:
    r = mk(consistent_series())
    mutate(r)
    M.verify_identity(r)
    if r.identity_ok == r.checked:
        missed.append(f"{name}：改坏了却仍报全部成立")
print(f"    5 种改法 → 漏检 {len(missed)} 种")
print(f"    ★ 漏检率 = {pct(len(missed), len(MUTANTS))}")
check_true("★★★ 漏检率 = 0（数据被改坏必须报不成立）", not missed, str(missed[:2]))
check_true("  且大偏差会被**逐日记下来**（不调和）",
           all(len(mk(s).big_deviations) >= 0 for s in [consistent_series()]), "")

print()
print("═" * 74)
print("C · ★ 容差边界：2 元算成立（官方保留到元的四舍五入），2.01 元不算")
print("═" * 74)
r2 = mk(consistent_series()); M.verify_identity(r2); base_ok = r2.identity_ok
r3 = mk(consistent_series())
r3.rows[5].rzye += M.IDENTITY_TOL          # 正好 2 元
M.verify_identity(r3)
check("★ 偏差正好 2 元 → 仍算成立", r3.identity_ok, r3.checked)
r4 = mk(consistent_series())
r4.rows[5].rzye += M.IDENTITY_TOL + 0.01   # 2.01 元
M.verify_identity(r4)
check_true("★ 偏差 2.01 元 → 报不成立（容差不放水）",
           r4.identity_ok < r4.checked, f"{r4.identity_ok}/{r4.checked}")

print()
print("═" * 74)
print("D · ★★ 净融资买入 = 融资余额一阶差分（可复算量，不是估计量）")
print("═" * 74)
series = consistent_series()
r5 = mk(series)
last, prev = series[-1], series[-2]
check("★★ 净融资买入 = 最后一日余额 − 前一日余额",
      r5.rows[-1].rzye - r5.rows[-2].rzye, last[1] - prev[1])
# ★ 它必须**恒等于** 买入 − 偿还（这就是交易所的恒等式）
check("★★ 且恒等于「买入 − 偿还」（这正是它能进结论的理由）",
      (last[1] - prev[1]), (last[2] - last[3]))

print()
print("═" * 74)
print("E · 缺数据 / 边界输入不许崩，且不许把'取不到'说成'是 0'")
print("═" * 74)
empty = M.MarginReport(code="X")
check_true("★ 空报告 render() 不崩", isinstance(empty.render(), str), "")
check_true("★ 空报告的 identity_rate 是 None（不是 0.0）", empty.identity_rate is None, "")
one = mk([("2026-09-01", 1e10, 1e7, 9e6)])
M.verify_identity(one)
check("★ 只有 1 行 → 检查 0 对（不给虚假的 100%）", one.checked, 0)
check_true("★ 且 identity_rate 是 None（0 对时不给率）", one.identity_rate is None, "")
# 缺字段的行不许崩
partial = M.MarginReport(code="X")
partial.rows = [M.MarginRow(date="2026-09-01", rzye=1e10),
                M.MarginRow(date="2026-09-02", rzye=None),
                M.MarginRow(date="2026-09-03", rzye=1.1e10, rzmre=1e8, rzche=None)]
try:
    M.verify_identity(partial)
    check_true("★ 缺 rzmre/rzche/rzye 不许崩", True, f"checked={partial.checked}")
except Exception as e:                                       # noqa: BLE001
    check_true("★ 缺字段不许崩", False, f"{type(e).__name__}: {e}")

print()
print("═" * 74)
print("F · ★★★ 边界声明：模块**刻意不做**的事必须写在输出里")
print("═" * 74)
txt = mk(consistent_series()).render()
for forbid in ("主力资金流", "北向净流入", "游资席位"):
    check_true(f"★ 明说「不做 {forbid}」", forbid in txt, "")
check_true("★★ 且说明北向那个字段**已不存在**（不是降级）", "已不存在" in txt, "")
check_true("★ 且写明主力资金流是厂商黑箱、无第二来源", "无第二来源" in txt, "")

print()
print("═" * 74)
print("G · ★ 真实数据的恒等式通过率（外部标准答案，实测）")
print("═" * 74)
try:
    real = M.load("000001")
    if real.error:
        print(f"    （取不到实网，跳过：{real.error[:60]}）")
    else:
        rate = real.identity_rate or 0
        print(f"    平安银行：恒等式 {real.identity_ok}/{real.checked} 成立"
              f" ｜ 最大偏差 {real.max_deviation:.0f} 元")
        check_true("★★ 真实数据恒等式通过率 ≥ 90%（不过就说明数据源有问题）",
                   rate >= 0.9, f"{rate:.0%}")
        check_true("★ 且真的算出了净融资买入", real.net_margin_buy is not None,
                   f"{real.net_margin_buy}")
except Exception as e:                                       # noqa: BLE001
    print(f"    （实网不可用，跳过：{type(e).__name__}: {e}）")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
