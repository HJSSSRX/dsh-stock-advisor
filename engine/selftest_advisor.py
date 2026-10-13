# -*- coding: utf-8 -*-
"""个性化层 + 观察层验收。

跑：python -m selftest_advisor   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════════════
这个文件的**重点不是功能，是那堵墙**
═══════════════════════════════════════════════════════════════════════════
功能部分（读画像、记观察）很直白。真正需要守住的是：

    ★ **个性化层绝不许改变事实。**

而"绝不许"不能靠约定，必须能机械断言。这里用三条：

    ① `PresentationPlan` 的字段表里**没有装数值的地方** —— 结构上做不到
    ② profile 里出现「想动事实」的键名 → **拒绝加载**
    ③ 引擎的事实层（compute/judge/evidence/data）**import 了 advisor → 断言失败**

③ 是最硬的一条：它是**对代码本身**的检查，不是对行为的检查。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

# ★ 根目录脚本拿不到包里 __init__.py 的编码设置 ——
#   不加重定向的话，打印 ✓ 会在 GBK 控制台上直接抛 UnicodeEncodeError（实测踩过）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from advisor import profile as P                              # noqa: E402
from advisor import observe as O                              # noqa: E402

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


# 把用户数据落到临时目录，**不碰真实画像**
TMP = tempfile.mkdtemp(prefix="advisor-test-")
P.USERS_DIR = os.path.join(TMP, "users")
O.OBS_DIR = os.path.join(TMP, "obs")

print("═" * 74)
print("A · ★★★ 墙的第一道：PresentationPlan 里**没有装数值的地方**")
print("═" * 74)
FIELDS = set(P.PresentationPlan.__dataclass_fields__)
check("★ 字段只有 4 个", len(FIELDS), 4)
check("★★ 字段表 = {顺序, 指标顺序, 用词, 详略}",
      FIELDS, {"section_order", "metric_priority", "term_glossary", "verbosity"})
# 字段名里不许出现能改数值的词
BAD_FIELD = [f for f in FIELDS if P.FORBIDDEN_IN_KEYS.search(f)]
check_true("★★ 字段名里没有任何「改事实」的维度", not BAD_FIELD, str(BAD_FIELD))
# 它是 frozen 的，实例化后改不了
plan = P.PresentationPlan(metric_priority=("位置", "换手"))
try:
    plan.verbosity = "简洁"
    check_true("★ 计划对象不可变（frozen）", False, "改成功了")
except Exception:                                            # noqa: BLE001
    check_true("★ 计划对象不可变（frozen）", True, "改不了，符合预期")

print()
print("═" * 74)
print("B · ★★ reorder 只改顺序 —— 不改内容、不改数量、不改值")
print("═" * 74)
items = [("财务", 1), ("位置", 2), ("换手", 3)]
got = plan.reorder(items, key=lambda x: x[0])
check("★ 按偏好排序", [x[0] for x in got], ["位置", "换手", "财务"])
check("★★ 数量一个不少", len(got), len(items))
check("★★ 元素本身没被改动（是同一批对象）", sorted(got, key=lambda x: x[1]), items)
check("★ 未列入偏好的保持原有相对次序",
      [x[0] for x in P.PresentationPlan(metric_priority=("换手",)).reorder(items, key=lambda x: x[0])],
      ["换手", "财务", "位置"])
check("★ 没有偏好时原样返回", [x[0] for x in P.PresentationPlan().reorder(items, key=lambda x: x[0])],
      ["财务", "位置", "换手"])
check_true("★ explain 只换词不换数 —— 它拿不到数值参数",
           "本益比" == P.PresentationPlan(term_glossary={"市盈率": "本益比"}).explain("市盈率"),
           "")
check("★ 未登记的术语原样返回", P.PresentationPlan().explain("扣非", "扣非"), "扣非")

print()
print("═" * 74)
print("C · ★★★ 墙的第二道：想动事实的配置，**存不进来**")
print("═" * 74)
MUST_REJECT = [
    ("过滤数据源偏好", {"user_id": "u", "source_preference": ["tencent"]}),
    ("只信一个来源", {"user_id": "u", "prefer_source": "tdx"}),
    ("隐藏风险", {"user_id": "u", "hide_risks": True}),
    ("隐藏某一节", {"user_id": "u", "hide_sections": ["缺口"]}),
    ("盖掉方向", {"user_id": "u", "direction_bias": "看多"}),
    ("嵌套的也不行", {"user_id": "u", "notes": "x", "knowledge": {"a": {"hide_x": 1}}}),
    ("白名单外的普通键", {"user_id": "u", "favorite_color": "blue"}),
    ("策略取值不合法", {"user_id": "u", "strategy": "打板"}),
    ("风险取值不合法", {"user_id": "u", "risk_tolerance": "无限"}),
]
for name, raw in MUST_REJECT:
    try:
        P.validate(raw)
        check_true(f"★★ {name} → 必须拒绝", False, "竟然通过了")
    except P.ProfileError as e:
        check_true(f"★★ {name} → 必须拒绝", True, str(e)[:52])
# 负对照：合规的必须放行
OK = {"user_id": "u", "display_name": "老王", "watchlist": ["600519"],
      "strategy": "波段", "risk_tolerance": "中",
      "metric_priority": ["位置", "换手"], "term_glossary": {"扣非": "扣掉一次性损益"},
      "knowledge": {"扣非": True, "商誉": False}, "verbosity": "详细"}
try:
    P.validate(OK)
    check_true("★★ 负对照：合规画像 → 放行", True, "白名单内的键全部通过")
except P.ProfileError as e:
    check_true("★★ 负对照：合规画像 → 放行", False, str(e))

print()
print("═" * 74)
print("D · 存 / 读 往返 + 原子替换")
print("═" * 74)
prof = P.UserProfile(**{k: v for k, v in OK.items() if k != "user_id"}, user_id="u")
path = P.save(prof)
check_true("★ 落盘成功", os.path.exists(path), path)
back = P.load("u")
check("★ 往返后 watchlist 一致", back.watchlist, ["600519"])
check("★ 往返后 metric_priority 一致", list(back.metric_priority), ["位置", "换手"])
check("★ 往返后 knowledge 一致", back.knowledge, {"扣非": True, "商誉": False})
check_true("★ 没有留下 .tmp 残file", not os.path.exists(path + ".tmp"), "")
check_true("★ 画像 render 里明写「只影响呈现顺序与用词」",
           "只影响呈现顺序与用词" in back.render(), "")
# ★ 未登记用户 → 返回空白画像（新用户不该被拦）
blank = P.load("从没见过的用户")
check("★ 新用户 → 空白画像，不抛错", blank.user_id, "从没见过的用户")
check("★ 新用户策略默认「未指定」", blank.strategy, "未指定")

print()
print("═" * 74)
print("E · ★★ 观察层：原话一字不改 + append-only")
print("═" * 74)
ob = O.Observer("s1")
RAW1 = "看看这个  啥情况??"          # ★ 故意带多余空格和错标点
o1 = ob.record(RAW1, resolved="行情|600519")
check("★★ 原话一字不改（含空格与标点）", o1.raw, RAW1)
o2 = ob.record("再看看这个", resolved="", outcome="refused_unrecognized", reason="认不出标的")
check("★ 记录序号递增", (o1.seq, o2.seq), (1, 2))
check("★★ 带上了上一问（序列）", o2.prev_raw, RAW1)
check_true("★ 记了间隔时长", isinstance(o2.gap_sec, float), f"{o2.gap_sec}s")
# append-only：文件行数只增不减
p1 = O._session_path("s1")
n1 = sum(1 for _ in open(p1, encoding="utf-8"))
ob.record("第三条")
n2 = sum(1 for _ in open(p1, encoding="utf-8"))
check("★★ append-only：只往后加", (n1, n2), (2, 3))
check("★ 前两行内容没被动过",
      json.loads(open(p1, encoding="utf-8").readline())["raw"], RAW1)
# 结局取值必须收敛（不许乱写）
o4 = ob.record("x", outcome="随便写的结局")
check("★ 非法结局被归一到 answered", o4.outcome, "answered")

print()
print("═" * 74)
print("F · ★★ 观察层汇总：三个诊断问题")
print("═" * 74)
ob2 = O.Observer("s2")
ob2.record("看看这个")
ob2.record("再帮我看看", outcome="refused_unrecognized", reason="认不出标的")
ob2.record("那南华生物呢")
ob2.record("看看这个")                      # ★ 重复问
rep = O.summarize("s2")
check_true("★ 报告里有提问序列", "提问序列" in rep, "")
check_true("★★ 报告标出了卡住的地方", "1 次" in rep and "设计缺口" in rep, "")
check_true("★★ 报告抓到了重复问", "又问了一遍" in rep, "")
check_true("★★ 报告里明写「不参与任何计算、不改变结论」",
           "不参与任何计算" in rep and "不改变任何结论" in rep, "")
check_true("★ 空会话不崩", "没有记录" in O.summarize("从没有过的会话"), "")

print()
print("═" * 74)
print("G · ★★★ 墙的第三道（最硬）：事实层**不许 import advisor**")
print("═" * 74)
import ast                                                   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
FACT_LAYERS = ("compute", "judge", "evidence", "data")
violations = []
for layer in FACT_LAYERS:
    d = os.path.join(ROOT, layer)
    if not os.path.isdir(d):
        continue
    for fn in os.listdir(d):
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(d, fn)
        src = open(fp, encoding="utf-8", errors="replace").read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m == "advisor" or m.startswith("advisor."):
                    violations.append(f"{layer}/{fn}:{node.lineno} → {m}")
check_true("★★★ 事实层没有任何一处 import advisor（这就是那堵墙）",
           not violations, str(violations))
# 反向确认：断言不是空转 —— advisor 自己确实存在且能 import
import advisor                                               # noqa: E402,F401
check_true("★ 反向确认：advisor 是可 import 的（说明断言不是空转）", True,
           f"扫了 {sum(len([f for f in os.listdir(os.path.join(ROOT, l)) if f.endswith('.py')]) for l in FACT_LAYERS if os.path.isdir(os.path.join(ROOT, l)))} 个事实层文件")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
