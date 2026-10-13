# -*- coding: utf-8 -*-
"""端到端入口的**效能验收** —— 重点在"两道拒答闸门"的漏检率与误杀率。

跑：python -m selftest_ask    （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
为什么这一层要单独验
═══════════════════════════════════════════════════════════════════
用户 7:30 会**随便问**。这一层是"用户那句话"进来的第一道门，它错了，
后面所有正确的取数/计算/校验**全部白做**。

★★ 本层最危险的两类错误（实测都发生过）：

  **① 答的不是问的**（比答错更糟）
     「茅台和五粮液哪个好」→ 第一版**只解析出五粮液，静默只答一只**
     「最近有什么消息」    → 第一版被判成**大盘**，返回一个空简报
     共同点：**看着像答了，其实答的是另一个问题**。

  **② 听不懂却硬答**
     「这和问的是不是同一只」→ 第一版落到大盘，返回**0 条事实的空简报**

→ 所以本文件的两个数字是**拒答闸门**的双向指标：

    漏检率  该拒的没拒（硬答了一个不知道的问题）
    误杀率  不该拒的被拒（正常问题被冤枉）

★ 只测一个方向没有意义：**永远拒答**的实现在"漏检率"上是满分。
"""
from __future__ import annotations

import os
import sys

# ★ 本文件在包**外面**（stockharness 根目录），享受不到包里那个编码兜底，
#   必须自己加 —— 否则在 cmd / PowerShell 5.1 下打印 ✓ 会 UnicodeEncodeError，
#   表面看是「整套测试崩了」，其实断言都跑过了（血泪清单第 7 条）。
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

sys.path.insert(0, ".")

from ask import AskError, Q_FINANCIAL, Q_MARKET, Q_PROFILE, Q_QUOTE, parse  # noqa: E402

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


def ok(q: str):
    """能解析 → 返回 Query；拒答 → 返回 None。"""
    try:
        return parse(q)
    except AskError:
        return None


print("═" * 74)
print("A · 正常问题**不许被误杀**（这是误杀率的分母）")
print("═" * 74)
MUST_PARSE = [
    ("茅台现在多少钱", Q_QUOTE, "600519"),
    ("贵州茅台股价", Q_QUOTE, "600519"),
    ("宁德时代现在多少钱", Q_QUOTE, "300750"),
    ("茅台上半年净利润多少", Q_FINANCIAL, "600519"),
    ("五粮液营收多少", Q_FINANCIAL, "000858"),
    ("茅台能不能买", Q_PROFILE, "600519"),
    ("比亚迪怎么样", Q_PROFILE, "002594"),
    ("招商银行贵不贵", Q_PROFILE, "600036"),
    ("600519现在多少钱", Q_QUOTE, "600519"),
    ("000858能不能买", Q_PROFILE, "000858"),
]
miss_kill: list[str] = []
for q, want_type, want_code in MUST_PARSE:
    r = ok(q)
    if r is None:
        miss_kill.append(f"{q}：被拒了")
        continue
    if r.code != want_code:
        miss_kill.append(f"{q}：代码解析成 {r.code}，应为 {want_code}")
print(f"    正常问题 {len(MUST_PARSE)} 条 → 被冤枉 {len(miss_kill)} 条")
print(f"    ★ 误杀率 = {pct(len(miss_kill), len(MUST_PARSE))}")
check_true("★★ 误杀率 = 0（正常问题一条都不许被拒）", not miss_kill, str(miss_kill[:3]))
check_true("  且类型判对",
           all((ok(q) or type("x", (), {"qtype": None})()).qtype == t
               for q, t, _c in MUST_PARSE),
           "")

print()
print("═" * 74)
print("B · ★★★ 该拒的**必须拒**（这是漏检率的分母）—— 答的不是问的，比答错更糟")
print("═" * 74)
MUST_REFUSE = [
    ("茅台和五粮液哪个好", "对比问题：静默只答一只是最坏的行为"),
    ("茅台 vs 五粮液", "同上"),
    ("中国平安和招商银行哪个好", "同上"),
    ("最近有什么消息", "消息题：情报通道没接线，不许返回空简报"),
    ("茅台有什么利好吗", "同上"),
    ("这和问的是不是同一只", "听不懂：不许落到大盘返回 0 条事实"),
    ("今天天气不错", "与股票无关：不许硬答"),
    ("一只不存在的股票能不能买", "认不出标的：不许猜代码"),
    ("随便说点什么", "听不懂"),
    ("", "空问题"),
]
miss_detect: list[str] = []
for q, why in MUST_REFUSE:
    if ok(q) is not None:
        miss_detect.append(f"「{q}」竟然解析成功了（{why}）")
print(f"    该拒的 {len(MUST_REFUSE)} 条 → 没拒 {len(miss_detect)} 条")
print(f"    ★ 漏检率 = {pct(len(miss_detect), len(MUST_REFUSE))}")
check_true("★★★ 漏检率 = 0（该拒的一条都不许放过去）", not miss_detect,
           str(miss_detect[:3]))

print()
print("═" * 74)
print("C · 对比问题必须**把两只都点出来**（不能只说「有歧义」）")
print("═" * 74)
try:
    parse("茅台和五粮液哪个好")
    check_true("对比问题被拒", False, "竟然通过了")
except AskError as e:
    msg = str(e)
    check_true("★ 拒答信息里点出了两只标的",
               "600519" in msg and "000858" in msg, msg[:70])
    check_true("★ 且说清「不会偷偷只回答其中一只」", "只回答其中一只" in msg or "只支持单标的" in msg, "")
# 负对照：只提一只的**不许**被判成对比
r1 = ok("茅台能不能买")
check_true("★ 负对照：只提一只 → 不触发对比闸门", r1 is not None and r1.code == "600519", "")

print()
print("═" * 74)
print("D · ★ 名字解析：全市场名单 + 别名合并（第一版只查名单，漏了口语名）")
print("═" * 74)
check("★ 口语名「茅台」→ 600519", (ok("茅台现在多少钱") or None).code, "600519")
check("★ 全名「贵州茅台」→ 600519", (ok("贵州茅台现在多少钱") or None).code, "600519")
check("★ 名单里的名字「宁德时代」→ 300750",
      (ok("宁德时代现在多少钱") or None).code, "300750")
# 6 位代码直给
check("★ 直接给 6 位代码", (ok("300750多少钱") or None).code, "300750")
# ★ 代码不能从更长的数字串里被截出来（边界）
check_true("★ 「1234567」这种 7 位数不该被当成 6 位代码",
           ok("1234567多少钱") is None, "否则会解析出一个不存在的标的")

print()
print("═" * 74)
print("E · ★ 大盘题与个股题不许互相串")
print("═" * 74)
check("★ 「今天大盘怎么样」→ 大盘", (ok("今天大盘怎么样") or None).qtype, Q_MARKET)
check("★ 负对照：「茅台现在多少钱」→ 行情（不是大盘）",
      (ok("茅台现在多少钱") or None).qtype, Q_QUOTE)
check("★ 负对照：「茅台能不能买」→ 个股分析",
      (ok("茅台能不能买") or None).qtype, Q_PROFILE)


print()
print("=" * 74)
print("★★★ 时点差 vs 源分歧（实测踩到的一个静默失败）")
print("=" * 74)
# 第一版把「通达信昨收 vs 腾讯今实时」报成「两个来源的价不一致」——
# 把一件正常的事说成异常的事。且因为宽 except 吞了 NameError，改了也不生效。
from decimal import Decimal as _D
from ask import _is_time_gap as _g
check_true("★★★ 时点差（南华生物实况）→ 必须认出来",
           _g(_D("10.81"), _D("11.35"), _D("-4.76"), "2026-10-13", "2026-10-12"), "")
check_true("★★ 真源分歧（同一天不同价）→ 不能当时点差",
           not _g(_D("10.81"), _D("10.90"), _D("-4.76"), "2026-10-13", "2026-10-13"), "")
check_true("★★ 日期不同但差值与涨跌幅不符 → 仍算源分歧",
           not _g(_D("10.81"), _D("11.35"), _D("-1.00"), "2026-10-13", "2026-10-12"), "")
check_true("★ 缺值不崩", not _g(None, _D("11.35"), _D("-4.76"), "a", "b"), "")
check_true("★ 除零不崩", not _g(_D("1"), _D("0"), _D("0"), "a", "b"), "")

print()
print("═" * 74)

print()
print("=" * 74)
print("★★★ stockctl --json 的 stdout 必须是**纯 JSON**（实测踩到）")
print("=" * 74)
# 踩到的：baostock 往 stdout 打 `login success!`，于是 JSON 第一行不是 `{`
#          → **模型直接解析失败**。工具的 stdout 必须只有一个 payload。
import subprocess, json as _json
_r = subprocess.run([sys.executable, "-m", "stockctl", "ask", "600519多少钱", "--json"],
                    capture_output=True, cwd=os.path.dirname(os.path.abspath(__file__)))
_out = (_r.stdout or b"").decode("utf-8", "replace")
check_true("★★★ stdout 第一个非空字符就是 `{`", _out.lstrip().startswith("{"),
           repr(_out[:60]))
try:
    _d = _json.loads(_out)
    check_true("★★★ 整个 stdout 能 JSON 解析", True, f"顶层键 {list(_d.keys())}")
    check_true("★★ 且拿到了事实", len(_d.get("facts", [])) > 0,
               f"{len(_d.get('facts', []))} 条")
except Exception as _e:                                          # noqa: BLE001
    check_true("★★★ 整个 stdout 能 JSON 解析", False,
               f"{type(_e).__name__}: {_e}｜前 80 字符 {_out[:80]!r}")

print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)

sys.exit(1 if N_FAIL else 0)
