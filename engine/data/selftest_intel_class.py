# -*- coding: utf-8 -*-
"""情报三类分档验收 —— **核心是"③不可核验"进不了结论**。

跑：python -m data.selftest_intel_class   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
这一层要守住的三件事
═══════════════════════════════════════════════════════════════════════════
  ① **分档是纯规则、可复现** —— 同一输入两次跑逐位一致，没有模型参与
  ② **"独立报道"与"转载"必须分开** ——
     3 家转同一篇通稿 ≠ 3 个独立来源。把它们算成一样，就是制造假信号。
  ③ **三类全部不参与方向** —— 这条要用结构断言守，不能靠自觉。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from data import intel as IN                                  # noqa: E402

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


def item(**kw) -> dict:
    """造一条已加工过的情报代表。"""
    base = {"标题": "某公司发布公告", "媒体分级": "S1", "发布时间": "2026-10-13 09:00",
            "_claim_key": "fact", "声明类型": "事实类", "簇内条数": 1,
            "独立性说明": "独家", "相关性": "强", "疑似批量稿": False}
    base.update(kw)
    return base


print("═" * 74)
print("A · ★ 三类的判据（纯规则）")
print("═" * 74)
check("★ 权威源 + 事实类 → ①可核验",
      IN.to_class(item(媒体分级="S1", _claim_key="fact")), IN.CLASS_I)
check("★ 权威源 + 规则类 → ①可核验",
      IN.to_class(item(媒体分级="S0", _claim_key="rule")), IN.CLASS_I)
check("★★ 但 S2 源的事实类**不算**可核验（源不够权威）→ ②或③",
      IN.to_class(item(媒体分级="S2", _claim_key="fact")), IN.CLASS_III)
check("★★ 权威源的**传闻类**也不算 ①（来源权威 ≠ 内容可靠）",
      IN.to_class(item(媒体分级="S1", _claim_key="rumor")), IN.CLASS_III)
check("★ 多家独立报道 → ②可计数",
      IN.to_class(item(媒体分级="S3", _claim_key="fact",
                       **{"簇内条数": 3, "独立性说明": "独立报道×3"})), IN.CLASS_II)
check("★ 自媒体一条 → ③不可核验",
      IN.to_class(item(媒体分级="S4", _claim_key="opinion")), IN.CLASS_III)

print()
print("═" * 74)
print("B · ★★★ 「独立报道」与「转载」必须分开（这是最容易制造假信号的地方）")
print("═" * 74)
indep = item(媒体分级="S3", **{"簇内条数": 3, "独立性说明": "独立报道×3"})
repost = item(媒体分级="S3", **{"簇内条数": 3, "独立性说明": "疑似转载×3"})
check("★ 独立报道×3 → ②可计数", IN.to_class(indep), IN.CLASS_II)
check("★★★ 疑似转载×3 → **不算②**（3 家转同一篇通稿 ≠ 3 个独立来源）",
      IN.to_class(repost), IN.CLASS_III)
# 负对照：只有 1 条也不算"多家"
solo = item(媒体分级="S3", **{"簇内条数": 1, "独立性说明": "独家"})
check("★ 只有 1 条 → 不算 ②（「多家」至少 2）", IN.to_class(solo), IN.CLASS_III)

print()
print("═" * 74)
print("C · ★★ 可复现：同一输入两次跑，逐位一致")
print("═" * 74)
x = item(媒体分级="S3", **{"簇内条数": 2, "独立性说明": "独立报道×2"})
check("★ 两次分类一致", (IN.to_class(x), IN.to_class(x)),
      (IN.CLASS_II, IN.CLASS_II))
b1 = IN.classify_all([dict(x)])
b2 = IN.classify_all([dict(x)])
check("★ 分档结果一致",
      {k: len(v) for k, v in b1.items()}, {k: len(v) for k, v in b2.items()})

print()
print("═" * 74)
print("D · ★ 分档区输出：必须自带免责，且明写「不参与方向判断」")
print("═" * 74)
reps = [
    item(标题="公司发布年度报告", 媒体分级="S1", _claim_key="fact"),
    item(标题="多家媒体报道公司中标", 媒体分级="S3",
         **{"簇内条数": 3, "独立性说明": "独立报道×3"}),
    item(标题="网传公司将被收购", 媒体分级="S4", _claim_key="rumor",
         必须标注="未获公司公告证实"),
    item(标题="股吧讨论：这票要起飞", 媒体分级="S4", _claim_key="opinion"),
]
sec = IN.build_class_section(reps)
check_true("★ 三个类都出现在输出里",
           all(c in sec for c in IN.CLASSES), sec.split("\n")[0])
check_true("★★ 明写「不参与方向判断」", "不参与方向判断" in sec, "")
check_true("★★ 明写「与上面的结论分开看」", "分开看" in sec, "")
check_true("★★ 对 ③ 明说「有人在说 ≠ 有这回事」",
           "有人在说" in sec and "有这回事" in sec, "")
check_true("★ 陈列里带上了独立报道标记", "独立报道×3" in sec, "")
check_true("★ 陈列里带上了「未获公司公告证实」", "未获公司公告证实" in sec, "")
check_true("★ ② 的说明里明写「不说明消息真假」", "不说明消息真假" in sec, "")
check("★ 空输入 → 空输出，不崩", IN.build_class_section([]), "")


print()
print("═" * 74)
print("F · ★★★ 两票否决：不相关 / 批量稿 → 一律 ③（防①变成垃圾桶）")
print("═" * 74)
# 实测踩到的：S1 官方媒体 + 事实类，但内容是**行业/大盘统计稿**
irrelevant = item(标题="食品饮料行业今日涨1.68%，主力资金净流入12.91亿元",
                  媒体分级="S1", _claim_key="fact", 相关性="弱")
check_true("★★★ 行业统计稿（相关性弱）→ 绝不进 ①", IN.to_class(irrelevant) == IN.CLASS_III,
           f"实际 {IN.to_class(irrelevant)}")
bulk = item(标题="610只个股流通市值不足20亿元", 媒体分级="S1", _claim_key="fact",
            疑似批量稿=True)
check_true("★★★ 批量稿 → 绝不进 ①", IN.to_class(bulk) == IN.CLASS_III,
           f"实际 {IN.to_class(bulk)}")
# 负对照：相关 + 非批量 → 该进①的还是要进
check("★★ 负对照：相关且非批量 → 仍然进 ①",
      IN.to_class(item(媒体分级="S1", _claim_key="fact", 相关性="强", 疑似批量稿=False)),
      IN.CLASS_I)
# ② 也要过这两关
check_true("★★ 批量稿也不许进 ②",
           IN.to_class(item(媒体分级="S3", **{"簇内条数": 3, "独立性说明": "独立报道×3",
                                               "疑似批量稿": True})) == IN.CLASS_III, "")

print()
print("═" * 74)
print("E · ★★★ 结构断言：三类**都不参与方向**")
print("═" * 74)
import ast                                                    # noqa: E402
import os                                                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 取材：ask.py 里给方向投票的地方（_su_rule）不许出现情报变量
ask_src = open(os.path.join(ROOT, "ask.py"), encoding="utf-8", errors="replace").read()
tree = ast.parse(ask_src)
su = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_su_rule":
        su = node
check_true("★ 找得到方向判定函数 _su_rule", su is not None, "")
if su is not None:
    body_src = ast.get_source_segment(ask_src, su) or ""
    leak = [w for w in ("intel", "intel_reps", "三类", "情报", "必标注")
            if w in body_src]
    check_true("★★★ _su_rule 里**没有一处**出现情报变量（三类都不进方向）",
               not leak, str(leak))
# 反向确认断言不是空转
# ── 反向确认：断言不是空转 ──
#    ⚠️ 2026-10-13 `_su_rule` 被重写（方向改由确定性打分器定），
#       原来这里检查 "BULL" 字面量，重写后就没有了 —— **断言过时了，不是代码错了**。
#       改检查它**确实在产出方向**：调打分器 / 返回 direction。
_seg = ast.get_source_segment(ask_src, su) or ""
check_true("★ 反向确认：_su_rule 确实在产出方向（调打分器 + return direction）",
           "decide(" in _seg and "return" in _seg, _seg[-120:].replace(chr(10), " "))

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
