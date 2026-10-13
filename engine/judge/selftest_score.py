# -*- coding: utf-8 -*-
"""确定性打分器验收 —— **按 §9.5 的整张矩阵，不是补一两个用例**。

跑：python -m judge.selftest_score   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
为什么这里能用"矩阵"而不是"想到几个测几个"
═══════════════════════════════════════════════════════════════════════════
竞品 `TradingAgents-astock` 的评级边界**修了三轮** —— 因为它每轮只补了
手边那一两个用例（`Buyer interest`→Buy、`Sell-off` 漏网、`Buy（…）`反判 Hold）。

**教训：参数化的矩阵式回归，而不是"想到一个补一个"。**
本文件的矩阵维度覆盖 §9.5 点名的全部五项。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from judge import score as SC                                # noqa: E402
from judge.score import Provenance, ProvenanceError, WeightSpec  # noqa: E402

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


ALL8 = ("EP", "BP", "IVOL", "TURN20", "ILLIQ", "REV20", "RESMOM", "FSCORE")


def per(kind: str) -> dict:
    """造一组分数。"""
    if kind == "bull":
        return {"EP": 0.9, "BP": 0.8, "IVOL": -0.7, "TURN20": -0.6,
                "ILLIQ": 0.5, "REV20": -0.4, "RESMOM": 0.7, "FSCORE": 0.8}
    if kind == "bear":
        return {"EP": -0.9, "BP": -0.8, "IVOL": 0.7, "TURN20": 0.6,
                "ILLIQ": -0.5, "REV20": 0.4, "RESMOM": -0.7, "FSCORE": -0.8}
    if kind == "flat":
        return {k: 0.0 for k in ALL8}
    # 卡在死区刀口上：等权 8 项，要让 S 正好 = δ
    #   δ=0.20，8 项等权 → 每项 0.125；让 2 项为 +0.8、其余 0 → S = 0.2
    if kind == "edge":
        d = {k: 0.0 for k in ALL8}
        d["EP"] = 0.8
        d["FSCORE"] = 0.8
        return d
    raise ValueError(kind)


# ══════════════════════════════════════════════════════════════════
print("═" * 74)
print("A · ★★★ 方向必须确定性：同一输入两次跑，逐字节一致")
print("═" * 74)
r1 = SC.decide("600519", "2026-10-13", per("bear"))
r2 = SC.decide("600519", "2026-10-13", per("bear"))
check("★★ spec_hash 两次一致", r1.spec_hash, r2.spec_hash)
check("★★ direction 两次一致", r1.direction, r2.direction)
check("★★ score 两次一致（浮点也要逐位）", r1.score, r2.score)
check("★★ contributions 两次一致", r1.contributions, r2.contributions)
check("★★ flags 两次一致", r1.flags, r2.flags)
# 规格哈希必须对"改了任何一项"敏感
#   ⚠️ 第一次写这处时我填的还是 0.125（跟原来一样），所以哈希没变、
#      测试却断言"必须变" —— **测试自己错了**。改符号才是真的改了一项。
m2 = SC.default_manifest()
m2[0] = WeightSpec("EP", m2[0].weight, -m2[0].sign, m2[0].provenance, m2[0].description,
                   citation=m2[0].citation, validated_on=m2[0].validated_on)
check_true("★★ 改符号 → 规格哈希必须变", SC.spec_hash(m2, 0.20, SC.HARD_FILTERS) != r1.spec_hash)
# 改权重（其余按比例缩，保持和为 1）
m3 = [WeightSpec("EP", 0.20, +1, Provenance.EQUAL, "x",
                 citation="DOI 10.1093/rfs/hhm075", validated_on="y")]
m3 += [WeightSpec(s.key, (1.0 - 0.20) / 7, s.sign, Provenance.EQUAL, s.description,
                  citation="DOI 10.1093/rfs/hhm075", validated_on="y")
       for s in SC.default_manifest()[1:]]
SC.assert_provenance_honest(m3)          # 先确认它本身合规
check_true("★★ 改权重（和为 1）→ 规格哈希必须变",
           SC.spec_hash(m3, 0.20, SC.HARD_FILTERS) != r1.spec_hash)
check_true("★★ 改 δ → 规格哈希必须变", SC.spec_hash(SC.default_manifest(), 0.25, SC.HARD_FILTERS) != r1.spec_hash)

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("B · ★★★ 权重来源说谎 → **构造不出来**")
print("═" * 74)
bad = [
    ("标 published 却没引用", WeightSpec("X", 1.0, +1, Provenance.PUBLISHED, validated_on="美股")),
    ("标 published 却没写验证范围", WeightSpec("X", 1.0, +1, Provenance.PUBLISHED, citation="DOI x")),
    ("标 estimated 却没试验次数", WeightSpec("X", 1.0, +1, Provenance.ESTIMATED, oos_result=0.1)),
    ("标 estimated 却没样本外结果", WeightSpec("X", 1.0, +1, Provenance.ESTIMATED, trials=3)),
    ("标 expert 却没稳定性区间", WeightSpec("X", 1.0, +1, Provenance.EXPERT)),
    ("符号不是 ±1", WeightSpec("X", 1.0, 0, Provenance.EQUAL)),
    ("权重和不为 1", WeightSpec("X", 0.7, +1, Provenance.EQUAL)),
]
for nm, spec in bad:
    try:
        SC.assert_provenance_honest([spec])
        check_true(f"★ {nm} → 必须抛 ProvenanceError", False, "没抛错")
    except ProvenanceError as e:
        check_true(f"★ {nm} → 必须抛 ProvenanceError", True, str(e)[:58])
# 负对照：合规的必须放行
try:
    SC.assert_provenance_honest(SC.default_manifest())
    check_true("★ 负对照：默认权重表合规 → 放行", True, "8 项等权，全部登记")
except ProvenanceError as e:
    check_true("★ 负对照：默认权重表合规 → 放行", False, str(e))

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("C · ★★★ 硬过滤命中 → 不给方向，**且必须被声明（不许静默吞掉）**")
print("═" * 74)
r = SC.decide("600519", "2026-10-13", per("bull"),
              blocked=[("DELIST_RISK", "该股在 ST 名单里")])
check("★ 命中硬过滤 → 方向为观望", r.direction, "观望")
check_true("★★ excluded 里有它、且带原因（不静默）",
           any("DELIST_RISK" == k and "ST" in why for k, why in r.excluded), str(r.excluded))
check_true("★★ flags 里明说是因为硬过滤", "硬过滤" in r.flags, str(r.flags))
check_true("★ 且即使打分本身是看多也不给方向（过滤优先）", r.score == 0.0, f"S={r.score}")
# 负对照：没命中过滤的必须正常给方向
r0 = SC.decide("600519", "2026-10-13", per("bull"))
check("★ 负对照：无过滤 → 正常给看多", r0.direction, "看多")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("D · ★★★ 后置自检：权重敏感 / 阈值敏感 → **必须降级为观望**")
print("═" * 74)
# 卡在刀口上：S 正好 = δ = 0.20 → δ 扫描必然发现标签变化 → 降级
edge = SC.decide("600519", "2026-10-13", per("edge"), delta=0.20)
check("★★ 卡在 δ 刀口 → 降级为观望", edge.direction, "观望")
check("★★ 且自报的原方向被保留（可审计）", edge.raw_direction, "看多")
check_true("★★ flags 里有「阈值敏感」", "阈值敏感" in edge.flags, str(edge.flags)[:70])
check_true("★★ δ 扫描结果被打进输出", len(edge.delta_scan) == 5, str(edge.delta_scan))
# 强信号不该被降级（负对照）
strong = SC.decide("600519", "2026-10-13", per("bull"), delta=0.20)
check("★★ 负对照：强信号 S=+0.675 → 不降级", strong.direction, "看多")
check_true("★ 且没有被误打敏感标记",
           "权重敏感" not in strong.flags and "阈值敏感" not in strong.flags,
           str(strong.flags))
check_true("★ 强信号仍给出逐项稳定区间", len(strong.stability) == 8, f"{len(strong.stability)} 项")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("E · ★★ 矩阵回归（§9.5 点名的五个维度）")
print("═" * 74)
# 维度 1：全部分数为 0 → 无区分度 → 必须观望
r = SC.decide("X", "2026-10-13", per("flat"))
check("★ 维度1 全部分数为 0 → 观望", r.direction, "观望")
check("★ 　且 S = 0", r.score, 0.0)

# 维度 2：只有 1 项有数据
r = SC.decide("X", "2026-10-13", {"EP": 0.9})
check_true("★ 维度2 只有 1 项 → 仍能算", isinstance(r.score, float), f"S={r.score}")
check_true("★★ 　且缺的 7 项被**逐个声明**",
           sum(1 for k, _ in r.excluded if k in ALL8) == 7,
           f"声明了 {sum(1 for k, _ in r.excluded if k in ALL8)} 项")
check_true("★★ 　且重归一化被声明",
           any(k == "__renormalized__" for k, _ in r.excluded), str(r.excluded[-1]))

# 维度 3：没有任何一项有数据
r = SC.decide("X", "2026-10-13", {})
check("★ 维度3 零指标 → 观望", r.direction, "观望")
check_true("★★ 　且明说「依据不足」", "依据不足" in r.flags, str(r.flags))

# 维度 4：权重和不为 1 → 必须拒绝
try:
    SC.assert_provenance_honest([WeightSpec("A", 0.5, +1, Provenance.EQUAL),
                                 WeightSpec("B", 0.3, +1, Provenance.EQUAL)])
    check_true("★ 维度4 权重和 ≠ 1 → 抛错", False, "没抛错")
except ProvenanceError as e:
    check_true("★ 维度4 权重和 ≠ 1 → 抛错", "不为 1" in str(e), str(e)[:50])

# 维度 5：±δ 边界两侧都要有例
CASES = [(0.20, "看多"), (0.20 - 1e-9, "观望"), (-0.20, "看空"), (-0.20 + 1e-9, "观望")]
for s, want in CASES:
    from judge.sensitivity import label_of
    check(f"★ 维度5 S={s!r} → {want}", label_of(s, 0.20), want)

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("F · ★★ 权重来源必须**逐字打进输出**（不是藏在日志里）")
print("═" * 74)
txt = SC.decide("600519", "2026-10-13", per("bull")).render()
check_true("★★ 输出里有权重来源计数", "权重来源：8 项中" in txt,
           txt.split("\n")[2][:60])
check_true("★★ 每一项的 DOI 都在输出里",
           txt.count("DOI 10.") >= 4, f"出现 {txt.count('DOI 10.')} 个 DOI")
check_true("★★ FSCORE 的「A 股未验证」警告在输出里",
           "A 股未验证" in txt, "")
check_true("★★ 明说技术指标不进打分", "技术指标" in txt and "不进打分" in txt, "")
check_true("★★ 样本不足时**不给置信度数字**（不编一个 0.7）",
           "样本不足" in txt and "置信度：**样本不足" in txt, "")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("G · ★ 默认权重表的来源声明必须站得住")
print("═" * 74)
mf = SC.default_manifest()
check("★ 8 项", len(mf), 8)
check("★ 权重和 = 1", round(sum(s.weight for s in mf), 10), 1.0)
check("★ 每项权重都是 1/8（等权）", len({round(s.weight, 10) for s in mf}), 1)
check_true("★★ 唯一标 published 的那项写了「A 股未验证」的 caveat",
           any(s.provenance is Provenance.PUBLISHED and "A 股未验证" in s.caveat for s in mf),
           "")
check_true("★ 每项都有 citation", all(s.citation for s in mf), "")
check_true("★ 每项都有 description", all(s.description for s in mf), "")
# 符号方向必须与文献一致（不许随便翻）
SIGNS = {"EP": +1, "BP": +1, "IVOL": -1, "TURN20": -1,
         "ILLIQ": +1, "REV20": -1, "RESMOM": +1, "FSCORE": +1}
bad_sign = [s.key for s in mf if s.sign != SIGNS[s.key]]
check_true("★★ 符号与文献一致（低波为负、换手为负、非流动性为正…）",
           not bad_sign, str(bad_sign))

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("H · ★ 横截面百分位（无参数、抗极值）")
print("═" * 74)
peers = list(range(100))
# ★ 中位秩法：value=50 时 below=50、equal=1 → (50+0.5)/100 = 0.505
#   （第一次我期望 0.5，那是把"中位秩"当成"严格小于个数占比"了 —— 测试写错，不是代码错）
check("★ 中位数 → 百分位 = 0.505（中位秩法）",
      round(SC.cross_section_percentile(50, peers), 3), 0.505)
check_true("★ 最大值的百分位 < 1（开区间，不给满分）",
           SC.cross_section_percentile(99, peers) < 1.0,
           str(SC.cross_section_percentile(99, peers)))
check_true("★ 最小值的百分位 > 0（开区间，不给零分）",
           SC.cross_section_percentile(0, peers) > 0.0,
           str(SC.cross_section_percentile(0, peers)))
check("★★ 同类样本不足 → None（不编一个 0.5）",
      SC.cross_section_percentile(5, [1, 2, 3]), None)
check("★★ 空 peers → None", SC.cross_section_percentile(5, []), None)
check("★ None 值 → None", SC.cross_section_percentile(None, peers), None)
# 抗极值：把一个极端值塞进去，中位数的百分位不该被拽飞
peers2 = list(range(100)) + [10 ** 12]
p1 = SC.cross_section_percentile(50, peers)
p2 = SC.cross_section_percentile(50, peers2)
check_true("★ 抗极值：塞一个 1e12 进去，中位数的百分位变动 < 0.01",
           abs(p1 - p2) < 0.01, f"{p1:.4f} vs {p2:.4f}")
check("★ to_z 映射到 [-1,1]", (round(SC.to_z(0.0), 6), round(SC.to_z(1.0), 6)), (-1.0, 1.0))

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
