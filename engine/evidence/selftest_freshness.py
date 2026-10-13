# -*- coding: utf-8 -*-
"""时效贯穿验收 —— **证明过期数据不会冒充实时数据。**

跑：python -m evidence.selftest_freshness   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 这一层拦的是最危险的一类错
═══════════════════════════════════════════════════════════════════
「取不到」是**看得见**的失败 —— 客户知道没数据。
但「拿到了 20 小时前的数据、当成实时报出去」是**看不见**的失败 ——
**它看起来完全成功。**

实测场景（我们真做过）：
    所有源被限流 → 缓存兜底拿出 20 小时前的指数 → 回答「上证 3797.24」
    客户以为这是现在的点位。

→ 所以本层的验收重点全是**反例**：
    回答里没写时效 → 必须被拦下来（blocker）
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from evidence.freshness import (          # noqa: E402
    FRESH, OLD, RECENT, VERY_OLD, Freshness, annotate, from_result, grade,
    must_say, validate_output,
)

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
print("A · ★★ 反例：过期数据必须被写进回答，否则拦下来")
print("═" * 74)

stale_quote = Freshness(as_of="2026-10-11", age_hours=20.0, stale=True,
                        source="akshare（缓存）", kind="quote")

# 反例 1：回答里完全没提时效
bad_text = "茅台现在 1279.99 元。最近估值处于历史中位。"
issues = validate_output(bad_text, [stale_quote])
check_true("★★ 回答没写时效 → 必须是 blocker", 
           any(i.severity == "blocker" for i in issues), f"{len(issues)} 条")
check_true("  且指出了最旧的那项", "20.0" in issues[0].problem if issues else False,
           issues[0].problem[:60] if issues else "")
check_true("  且给了该怎么写", bool(issues and issues[0].required_fix))

# 反例 2：写了时效 → 放行
good_text = "茅台 1279.99 元（**这是 20.0 小时前的数据，不是现在的**）。"
issues2 = validate_output(good_text, [stale_quote])
check_true("★ 写了时效 → 放行", len(issues2) == 0,
           f"{[i.problem[:40] for i in issues2]}")

# 反例 3：新鲜数据不要求写时效
fresh = Freshness(as_of="2026-10-12", age_hours=0.05, stale=False, kind="quote")
issues3 = validate_output("茅台现在 1288 元。", [fresh])
check_true("新鲜数据不强制写时效（避免噪音）", len(issues3) == 0)

print()
print("═" * 74)
print("B · ★★ 反例：守卫说必需数据缺失，回答必须说明")
print("═" * 74)
note = "🚫 **【必需数据缺失】实时价格(quote)** —— 本次不能给出完整结论。"
issues4 = validate_output("茅台的基本面还不错。", [], note)
check_true("★★ 缺失必需数据但回答不提 → blocker",
           any(i.severity == "blocker" for i in issues4), f"{len(issues4)} 条")
issues5 = validate_output("价格数据现在取不到，下面是能查到的部分。", [], note)
check_true("★ 说了「取不到」→ 放行", len(issues5) == 0,
           f"{[i.problem[:40] for i in issues5]}")

print()
print("═" * 74)
print("C · 时效分级要按**数据自己的更新频率**，不能一刀切")
print("═" * 74)
# 财报：季度更新，30 天内都算新
lv_f, _ = grade(24 * 10, "financial_sina")
check("财报 10 天前 → 算「实时/最新一期」", lv_f, FRESH)
# 行情：2 小时前就不叫"现在"了
lv_q, txt_q = grade(2.5, "quote")
check_true("★ 行情 2.5 小时前 → 已经算「陈旧」", lv_q == OLD, lv_q)
check_true("  且话里点明「不是现在的」", "不是现在" in txt_q, txt_q)
lv_q2, txt_q2 = grade(0.1, "quote")
check("行情 6 分钟前 → 实时", lv_q2, FRESH)
lv_q3, _ = grade(24 * 3, "quote")
check("行情 3 天前 → 很旧", lv_q3, VERY_OLD)
lv_f2, _ = grade(24 * 200, "financial_sina")
check("财报 200 天前 → 陈旧", lv_f2, OLD)

print()
print("═" * 74)
print("D · 渲染：过期标签必须在数字**前面**")
print("═" * 74)
s = annotate("1279.99", stale_quote, " 元")
check_true("★ 标签在数字前面（客户先看到时效再看到数）",
           s.index("［") < s.index("1279.99"), s)
check_true("  且带上了 as_of", "2026-10-11" in s, s)
s2 = annotate("1288.00", fresh, " 元")
check_true("新鲜数据不加标签（避免噪音）", s2 == "1288.00 元", s2)

print()
print("═" * 74)
print("E · must_say：判断层不能省的那句话")
print("═" * 74)
ms = must_say([stale_quote], "")
check_true("★ 有过期数据 → 给出必须说的话", len(ms) >= 1, ms[0][:50] if ms else "")
check_true("  且那句话含时效信息", "小时前" in ms[0] if ms else False)
ms2 = must_say([fresh], "")
check_true("新鲜数据 → 不给硬性要求", len(ms2) == 0, f"{ms2}")
ms3 = must_say([], note)
check_true("★ 必需数据缺失 → 也给出一句", len(ms3) >= 1, ms3[0][:50] if ms3 else "")

print()
print("═" * 74)
print("F · from_result：能不能从取数层正确提取")
print("═" * 74)


class _FakeResult:
    as_of = "2026-10-11"
    age_seconds = 72000.0
    stale = True
    source = "akshare（缓存）"
    kind = "quote"


f = from_result(_FakeResult())
check("age_hours 换算正确", f.age_hours, 20.0)
check("stale 透传", f.stale, True)
check("as_of 透传", f.as_of, "2026-10-11")
check("kind 透传（决定分级阈值）", f.kind, "quote")

print()
print("═" * 74)
print("G · 真实联动：取数层的 stale 能不能一路传到输出校验")
print("═" * 74)
import json
import os
import time

sys.path.insert(0, ".")
import data.registry as R                    # noqa: E402
from data import cache as C                  # noqa: E402

R.reset_health()
# ★ 测试构造注意：registry 会把参数**补全**（freq/fqt/limit）再算缓存键，
#   所以必须**用同一个调用两次**（第一次写缓存、第二次读兜底），
#   不能自己手搓参数去对键 —— 对不上（第一版就是栽在这）。
PARAMS = {"limit": 10}
# ⚠️ 第一次**必须 use_cache=True** —— 否则不写缓存，后面就兜不到（第一版就栽在这）
r = R.fetch("index_kline", "000001", dict(PARAMS), use_cache=True)
if not (r.ok and isinstance(r.data, list) and len(r.data)):
    print("  - 跳过（取不到指数数据，无法构造 stale 场景）")
else:
    # 把所有 index_kline 缓存做旧
    import glob
    n_old = 0
    for fp in glob.glob(os.path.join(C.CACHE_DIR, "index_kline_*.json")):
        b = json.load(open(fp, encoding="utf-8"))
        b["ts"] = time.time() - 20 * 3600
        json.dump(b, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        n_old += 1
    check_true(f"把 {n_old} 个 index_kline 缓存做旧到 20 小时前", n_old > 0)

    # 用**同样的参数**再取一次，但把所有源弄挂
    orig = list(R.ROUTES["index_kline"])
    R.ROUTES["index_kline"] = [("eastmoney", "nope_not_a_function")]
    try:
        r2 = R.fetch("index_kline", "000001", dict(PARAMS), use_cache=False)
    finally:
        R.ROUTES["index_kline"] = orig

    fr = from_result(r2)
    check_true("★★ 全源失败时取到了 stale 数据", r2.ok and fr.stale,
               f"ok={r2.ok} stale={fr.stale}")
    check_true("★ 时效换算正确（约 20 小时）", 19 < fr.age_hours < 21,
               f"{fr.age_hours:.2f}h")
    if fr.stale:
        bad = validate_output("上证指数收在 3797.24 点。", [fr])
        check_true("★★ 拿它校验一段没写时效的话 → 被拦下",
                   any(i.severity == "blocker" for i in bad), f"{len(bad)} 条")
        ok_text = "上证指数收在 3797.24 点（" + fr.sentence() + "）。"
        good = validate_output(ok_text, [fr])
        check_true("★ 补上时效后 → 放行", len(good) == 0,
                   f"{[i.problem[:36] for i in good]}")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
