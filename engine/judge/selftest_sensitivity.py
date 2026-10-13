# -*- coding: utf-8 -*-
"""权重敏感性验收 —— **核心是"闭式解"和"暴力扫描"必须对得上**。

跑：python -m judge.selftest_sensitivity   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
为什么这个模块能被机械验证
═══════════════════════════════════════════════════════════════════════════
S(t) 是 t 的**线性函数**，所以"标签不变区间"有精确解。
既然有精确解，就能拿**暴力扫描**去对：

    闭式解说 t ∈ [lo, hi] 标签不变
    → 在 [0,1] 上撒 10000 个点，凡是标签不变的**必须**落在 [lo, hi] 里，
      凡是落在 [lo, hi] 里的**必须**标签不变。

**这是双向对拍，不是自证。** 任一边对不上就是错。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from judge import sensitivity as S                          # noqa: E402

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


print("═" * 74)
print("A · 标签函数本身（判方向的唯一出口）")
print("═" * 74)
check("S = +δ 边界 → 看多（含等号）", S.label_of(0.20, 0.20), "看多")
check("S = −δ 边界 → 看空（含等号）", S.label_of(-0.20, 0.20), "看空")
check("S = δ−1e-9 → 观望", S.label_of(0.20 - 1e-9, 0.20), "观望")
check("S = −δ+1e-9 → 观望", S.label_of(-0.20 + 1e-9, 0.20), "观望")
check("S = 0 → 观望", S.label_of(0.0, 0.20), "观望")

print()
print("═" * 74)
print("B · ★★★ 闭式解 ↔ 暴力扫描，双向对拍（这是本模块的存在理由）")
print("═" * 74)

W = {"EP": 0.30, "FSCORE": 0.20, "REV20": 0.25, "TURN20": 0.25}
CASES = [
    ("典型多空混合", {"EP": 0.8, "FSCORE": 0.5, "REV20": -0.6, "TURN20": -0.3}),
    ("全正", {"EP": 0.9, "FSCORE": 0.7, "REV20": 0.4, "TURN20": 0.2}),
    ("全负", {"EP": -0.9, "FSCORE": -0.7, "REV20": -0.4, "TURN20": -0.2}),
    ("卡在阈值上", {"EP": 0.20, "FSCORE": 0.0, "REV20": 0.0, "TURN20": 0.0}),
    ("几乎相等", {"EP": 0.5, "FSCORE": -0.5, "REV20": 0.1, "TURN20": -0.1}),
]
bad_cases = []
for cname, sc in CASES:
    for key in W:
        st = S.stability_interval(W, sc, key, delta=0.20)
        # ★ 双向对拍：撒点，看"标签不变"的集合是否恰好等于 [lo, hi]
        #   ⚠️ 端点处 S(t) 正好等于 ±δ，浮点比较会翻脸 —— 端点邻域**跳过**
        #      （这是浮点边界测试的标准做法，不是放水：区间内部照样逐点验）
        EPS = 1e-6
        wrong_in = wrong_out = 0
        N = 4000
        for i in range(N + 1):
            t = i / N
            if abs(t - st.lo) < EPS or abs(t - st.hi) < EPS:
                continue
            w2 = dict(W)
            w2[key] = t
            for k in W:
                if k != key:
                    w2[k] = W[k] * (1 - t) / (1 - W[key])
            s = sum(w2[k] * sc[k] for k in w2)
            same = (S.label_of(s, 0.20) == st.base_label)
            inside = (st.lo <= t <= st.hi)
            if same and not inside:
                wrong_in += 1
            if inside and not same:
                wrong_out += 1
        if wrong_in or wrong_out:
            bad_cases.append(f"{cname}/{key}: 区间外但同标签 {wrong_in} 个、区间内但异标签 {wrong_out} 个")
        # 同时和暴力扫描的端点对一下
        blo, bhi = S.brute_force_interval(W, sc, key, 0.20, step=1e-3)
        if abs(blo - st.lo) > 2e-3 or abs(bhi - st.hi) > 2e-3:
            bad_cases.append(f"{cname}/{key}: 闭式[{st.lo:.4f},{st.hi:.4f}] vs 扫描[{blo:.4f},{bhi:.4f}]")

print(f"    对拍了 {len(CASES)} × {len(W)} = {len(CASES)*len(W)} 组，每组撒 4001 个点")
check_true("★★★ 双向对拍全部一致（区间内必同标签、区间外必异标签）", not bad_cases,
           str(bad_cases[:3]))
print("    示例：", S.stability_interval(W, CASES[0][1], "EP", 0.20))

print()
print("═" * 74)
print("C · ★ 权重退化与边界（§9.5 点名的必测矩阵）")
print("═" * 74)
# 权重退化：某一项权重=1，其余=0
W_DEG = {"EP": 1.0, "FSCORE": 0.0, "REV20": 0.0, "TURN20": 0.0}
st = S.stability_interval(W_DEG, {"EP": 0.9, "FSCORE": 0.0, "REV20": 0.0, "TURN20": 0.0},
                          "EP", 0.20)
check_true("★ 权重=1 时不崩，且标为退化", st.degenerate, str(st))
check("★ S 恒等于 a_k = 0.9 → 看多", S.label_of(0.9, 0.20), "看多")

# 权重和不为 1 → 必须拒绝
try:
    S.base_score({"EP": 0.5, "FSCORE": 0.5, "REV20": 0.5}, {"EP": 1, "FSCORE": 1, "REV20": 1})
    check_true("★★ 权重和不为 1 → 必须抛错（不许静默归一化）", False, "没抛错")
except ValueError as e:
    check_true("★★ 权重和不为 1 → 必须抛错（不许静默归一化）", "不为 1" in str(e), str(e)[:60])

# 全部分数为 0 → 横截面无区分度 → 必须观望
check("★ 全部分数 = 0 → 观望（无区分度）",
      S.label_of(S.base_score(W, {k: 0.0 for k in W}), 0.20), "观望")

# 只有 1 项有数据
W_ONE = {"EP": 0.5, "FSCORE": 0.5}       # manifest 和为 1 ✓
avail, excl = S.resolve_weights(W_ONE, {"EP": 0.8})
check("★ 缺数据项被剔除", list(avail), ["EP"])
check_true("★★ 且剔除原因被**声明出来**（不许静默）",
           any(k == "FSCORE" for k, _ in excl), str(excl))
check_true("★★ 重归一化这件事本身也被声明", any(k == "__renormalized__" for k, _ in excl),
           str(excl))
s_one = S.base_score(W_ONE, {"EP": 0.8})
check("★ 重归一化后 EP 权重=1 → S = 0.8", s_one, 0.8)
st_one = S.stability_interval(W_ONE, {"EP": 0.8}, "EP", 0.20)
check_true("★ 且稳定性区间算得出（退化为 a_k 常量）", 0.0 <= st_one.lo <= st_one.hi <= 1.0,
           str(st_one))
# 完全没数据 → 抛错
try:
    S.base_score({"EP": 1.0}, {"XX": 1.0})
    check_true("★ 权重与分数完全不相交 → 抛错", False, "没抛错")
except ValueError as e:
    check_true("★ 权重与分数完全不相交 → 抛错", "没有任何一项" in str(e), str(e)[:50])

print()
print("═" * 74)
print("D · ★ 可复现：同一个输入必须给同一个结果")
print("═" * 74)
sc = CASES[0][1]
f1 = S.flip_rate(W, sc, 0.20, M=500)
f2 = S.flip_rate(W, sc, 0.20, M=500)
check("★★ flip_rate 同 seed → 逐位一致（不许每次不一样）", f1, f2)
check_true("★ flip_rate ∈ [0,1]", 0.0 <= f1 <= 1.0, f"{f1:.4f}")
# 扰动幅度 κ 越大 → 翻转越少（单调性）
big = S.flip_rate(W, sc, 0.20, M=800, kappa=200.0)
small = S.flip_rate(W, sc, 0.20, M=800, kappa=3.0)
check_true("★ κ 越大扰动越小 → 翻转率不增", big <= small + 1e-9,
           f"κ=200: {big:.4f} ｜ κ=3: {small:.4f}")

print()
print("═" * 74)
print("E · δ 扫描（δ 是我们定的策略常数，不是实证结论，所以必须扫）")
print("═" * 74)
scan = S.delta_scan(W, sc)
check("★ 网格正好 5 个点", len(scan), 5)
check_true("★ 返回 {δ: 标签}", all(isinstance(v, str) for v in scan.values()),
           str(scan))
changed = len(set(scan.values())) > 1
print(f"    δ∈{list(scan)} → 标签 {'发生变化（须降级）' if changed else '稳定'}")
# 造一个必然随 δ 变化的输入：让它落在 δ 网格中间
#   W["EP"]=0.30，其余三项分数为 0 → S = 0.30 × EP
#   取 EP=2/3 → S = 0.20，正好压在 δ 网格的中间档上
border = {"EP": 2 / 3, "FSCORE": 0.0, "REV20": 0.0, "TURN20": 0.0}
_s_border = S.base_score(W, border)
check("★ 构造出来的 S 正好 ≈ 0.20", round(_s_border, 6), 0.2)
scan2 = S.delta_scan(W, border)
check_true("★★ 卡在阈值上的输入 → δ 扫描必然捕捉到变化（这证明扫描有用）",
           len(set(scan2.values())) > 1, str(scan2))

print()
print("═" * 74)
print("F · ★ 硬性降级阈值必须写死在模块里（不许调用方随手改）")
print("═" * 74)
check("★ flip_rate 上限 = 5%", S.FLIP_RATE_MAX, 0.05)
check("★ 稳定区间宽度下限 = 0.10", S.STABILITY_WIDTH_MIN, 0.10)
check("★ δ 网格 5 档", len(S.DELTA_GRID), 5)

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
