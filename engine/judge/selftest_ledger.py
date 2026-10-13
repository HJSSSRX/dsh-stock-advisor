# -*- coding: utf-8 -*-
"""决策台账验收 —— **重点在指标口径，因为口径错了整个统计就废了。**

跑：python -m judge.selftest_ledger   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 这一层的验收为什么全是口径测试
═══════════════════════════════════════════════════════════════════
别人的血教训（TradingAgents-astock v0.5.2）：

    「v0.5.2 曾把 direction_accuracy 叫"胜率"，
      导致给 Sell 之后股价下跌（**判断正确**）被记成失败。」

**这不是 bug，是口径错误 —— 而且它会静默污染整个统计。**

所以验收重点是：
    看空 + 股价跌 → 必须判**对**（而不是"跌了所以错"）
    观望        → 必须**不计入**（而不是算成错）
    up_rate     → 必须与 correct **无关**
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, ".")

from judge import ledger as L          # noqa: E402

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


def mk(**kw) -> L.Decision:
    base = dict(made_on="2026-01-05", code="600519", name="贵州茅台",
                direction=L.BULL, thesis="基本面稳健", confidence=0.7,
                horizon_days=60, benchmark="000300",
                entry_price=100.0, bench_price=4000.0)
    base.update(kw)
    return L.Decision(**base)


print("═" * 74)
print("A · ★★★ 核心口径：看空+跌 = 判断正确（别人的血教训）")
print("═" * 74)
# 标的跌 10%，基准跌 2% → alpha = -8% → 看空是对的
check("★★ 看空 + alpha=-8% → **正确**",
      L.judge_correct(L.BEAR, -8.0), True)
check("★★ 看空 + alpha=+8% → **错误**（它没跌，反而跑赢）",
      L.judge_correct(L.BEAR, 8.0), False)
check("★ 看多 + alpha=+8% → 正确", L.judge_correct(L.BULL, 8.0), True)
check("★ 看多 + alpha=-8% → 错误", L.judge_correct(L.BULL, -8.0), False)
check_true("★ 观望 → None（**不计入**，不是算成错）",
           L.judge_correct(L.HOLD, -8.0) is None, "None")
check_true("alpha 缺失 → None（不猜对错）",
           L.judge_correct(L.BULL, None) is None, "")
check("★ 阈值内（+0.5%）不算赢（避免噪音）",
      L.judge_correct(L.BULL, 0.5), False)
check("★ 阈值外（+1.5%）才算赢",
      L.judge_correct(L.BULL, 1.5), True)

print()
print("═" * 74)
print("B · 反例：把「看空+跌」的场景做完整结算，看会不会被误判")
print("═" * 74)
# 场景：看空某票，60 天后它跌了，基准涨了 —— 这是**看对了**
short = mk(direction=L.BEAR, entry_price=100.0, bench_price=4000.0)
L.settle_one(short, exit_price=90.0, bench_exit=4080.0, on="2026-03-06")
check("  标的跌 10%", short.ret_pct, -10.0)
check("  基准涨 2%", short.bench_pct, 2.0)
check("  超额 = -12%", short.alpha_pct, -12.0)
check("★★ 判定为**正确**（这正是 v0.5.2 判错的那个场景）", short.correct, True)

# 反面：看空但股价大涨 → 错
short2 = mk(direction=L.BEAR, entry_price=100.0, bench_price=4000.0)
L.settle_one(short2, exit_price=120.0, bench_exit=4000.0)
check("★ 看空 + 标的涨 20% → 错误", short2.correct, False)

print()
print("═" * 74)
print("C · ★★ 两组指标必须分开：direction_accuracy vs up_rate")
print("═" * 74)
# ★ 构造一个**两指标必然不同**的场景：
#   标的全部**上涨**（up_rate = 100%），但**跑输**基准 → 看多判断全错
#   → up_rate=1.0 而 direction_accuracy=0.0
decs = []
for i in range(6):
    d = mk(code=f"60000{i}", direction=L.BULL, entry_price=100.0, bench_price=4000.0)
    L.settle_one(d, exit_price=101.0, bench_exit=4400.0)   # 标的+1%，基准+10%
    decs.append(d)
st = L.summarize(decs)
check("  标的上涨比例 up_rate = 100%", st.up_rate, 1.0)
check("★ 标的涨了但**跑输**基准 → 看多判断**错**", st.direction_accuracy, 0.0)
check_true("★★ 两个指标截然不同（100% vs 0%）—— 证明它们衡量的不是同一件事",
           st.up_rate != st.direction_accuracy,
           f"up_rate={st.up_rate} vs acc={st.direction_accuracy}")
check("  参考指标：跑赢基准比例 = 0", st.outperform_rate, 0.0)

# 反向构造：看空 + 标的跌 + 基准跌更多 → 等等，那是跑赢 → 错
# 再来一组：看空 + 标的跌 + 基准跌更少 → 跑输 → 对
decs2 = []
for i in range(4):
    d = mk(code=f"00000{i}", direction=L.BEAR, entry_price=100.0, bench_price=4000.0)
    L.settle_one(d, exit_price=88.0, bench_exit=3960.0)   # 标的-12%，基准-1%
    decs2.append(d)
st2 = L.summarize(decs2)
check("★ 看空 + 大幅跑输基准 → 判断对", st2.direction_accuracy, 1.0)
check("  样本 4 条", st2.graded, 4)

print()
print("═" * 74)
print("D · ★★ 反例：样本不足时，不许下统计结论")
print("═" * 74)
rep_small = st2.report(min_sample=30)
check_true("★ 样本 4 条 < 30 → 明确说「统计意义不足」",
           "统计意义不足" in rep_small, rep_small.split("\n")[-1][:70])
# ★ 断言要写对：正确的行为是**明确提醒不能据此下结论**，
#   而不是"一个字都不许出现"（第一版断言写反了，被自己的测试绊倒）
check_true("★ 且明确提醒「不能据此下结论」",
           "不能据此" in rep_small, rep_small.replace("\n", " ")[-90:])
check_true("  且没有正面宣称自己准",
           "我们的判断准确" not in rep_small and "我们很准" not in rep_small)

# 造 40 条 → 够样本
decs3 = []
for i in range(40):
    d = mk(code=f"6001{i:02d}", direction=L.BULL if i % 2 == 0 else L.BEAR,
           entry_price=100.0, bench_price=4000.0, confidence=0.7)
    # 偶数看多且跑赢；奇数看空且跑输 → 全对
    if i % 2 == 0:
        L.settle_one(d, exit_price=105.0, bench_exit=4000.0)
    else:
        L.settle_one(d, exit_price=95.0, bench_exit=4000.0)
    decs3.append(d)
st3 = L.summarize(decs3)
check("  40 条全部判对", st3.direction_accuracy, 1.0)
rep_big = st3.report(min_sample=30)
check_true("样本够 → 不再说「不足」", "统计意义不足" not in rep_big, "")
check_true("★ 且给出了方向正确率", "方向正确率" in rep_big, rep_big.split("\n")[1][:70])
check_true("★ 且把 up_rate 标为「与判断对错无关」",
           "与判断对错无关" in rep_big, "")

print()
print("═" * 74)
print("E · 观望必须被排除在分母外（否则会稀释正确率）")
print("═" * 74)
mixed = []
for i in range(5):
    d = mk(code=f"3000{i}", direction=L.BULL, entry_price=100.0, bench_price=4000.0)
    L.settle_one(d, exit_price=108.0, bench_exit=4000.0)     # 对
    mixed.append(d)
for i in range(5):
    d = mk(code=f"3001{i}", direction=L.HOLD, entry_price=100.0, bench_price=4000.0)
    L.settle_one(d, exit_price=90.0, bench_exit=4000.0)      # 观望，不该计入
    mixed.append(d)
stm = L.summarize(mixed)
check("  已结算 10 条", stm.settled, 10)
check("★★ 参与评定只有 5 条（观望被排除）", stm.graded, 5)
check("★★ 方向正确率 = 100%（5/5），不是 50%",
      stm.direction_accuracy, 1.0)

print()
print("═" * 74)
print("F · 台账读写：追加式、不覆盖、能重算")
print("═" * 74)
with tempfile.TemporaryDirectory() as td:
    old_path, old_dir = L.LEDGER_PATH, L._LEDGER_DIR      # noqa: SLF001
    L._LEDGER_DIR = td                                    # noqa: SLF001
    L.LEDGER_PATH = os.path.join(td, "decisions.jsonl")
    try:
        d1 = mk(code="600519")
        d2 = mk(code="000001", direction=L.BEAR)
        L.record(d1)
        L.record(d2)
        back = L.load_all()
        check("  写入 2 条，读回 2 条", len(back), 2)
        check("  方向保住", back[1].direction, L.BEAR)
        check("  依据列表保住（空）", back[0].evidence_ids, [])

        # 再追加，不许覆盖
        L.record(mk(code="300417"))
        check("★ 追加不覆盖（变 3 条）", len(L.load_all()), 3)

        # 回填结算
        all_d = L.load_all()
        L.settle_one(all_d[0], exit_price=110.0, bench_exit=4000.0)
        L.rewrite(all_d)
        check("★ 结算回填后仍是 3 条", len(L.load_all()), 3)
        check("★ 结算结果保住", L.load_all()[0].correct, True)
        check("  未结算的仍是未结算", L.load_all()[1].settled, False)
    finally:
        L.LEDGER_PATH, L._LEDGER_DIR = old_path, old_dir  # noqa: SLF001

print()
print("═" * 74)
print("G · 到期日计算")
print("═" * 74)
d = mk(made_on="2026-01-05", horizon_days=60)
check("★ 到期日 = 判断日 + 期限", str(d.due_on), "2026-03-06")
check("  边界：0 天 → 当天到期", str(mk(made_on="2026-01-05", horizon_days=0).due_on),
      "2026-01-05")

print()
print("═" * 74)
print("H · 校准曲线：自报把握 vs 实际命中")
print("═" * 74)
cal_decs = []
# 自报 0.9 的 10 条里 9 条对（校准好）；自报 0.5 的 10 条里 5 条对
for i in range(10):
    x = mk(code=f"8000{i}", direction=L.BULL, confidence=0.9,
           entry_price=100.0, bench_price=4000.0)
    L.settle_one(x, exit_price=105.0 if i < 9 else 95.0, bench_exit=4000.0)
    cal_decs.append(x)
for i in range(10):
    x = mk(code=f"8001{i}", direction=L.BULL, confidence=0.5,
           entry_price=100.0, bench_price=4000.0)
    L.settle_one(x, exit_price=105.0 if i < 5 else 95.0, bench_exit=4000.0)
    cal_decs.append(x)
cal = L.calibration(cal_decs, bins=5)
check_true("★ 产出校准曲线", len(cal) >= 2, f"{len(cal)} 个区间")
for row in cal:
    print(f"      自报 {row['自报均值']:.0%} → 实际 {row['实际命中率']:.0%}  "
          f"（{row['条数']} 条，区间 {row['置信度区间']}）")
hi = [r for r in cal if r["自报均值"] > 0.8]
lo = [r for r in cal if r["自报均值"] < 0.6]
check_true("★ 高置信组命中率高于低置信组（校准方向正确）",
           bool(hi and lo and hi[0]["实际命中率"] > lo[0]["实际命中率"]),
           f"高={hi[0]['实际命中率'] if hi else '-'} 低={lo[0]['实际命中率'] if lo else '-'}")

print()
print("═" * 74)
print("I · 缺入场价时不硬结算")
print("═" * 74)
nop = mk(entry_price=None)
L.settle_one(nop, exit_price=110.0, bench_exit=4000.0)
check("★ 缺入场价 → 不标记为已结算", nop.settled, False)
check_true("  且写明了原因", "无法结算" in (nop.note or ""), nop.note[:50])

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
