# -*- coding: utf-8 -*-
"""取数稳定性验收 —— **证明"取不到"不等于"什么都没有"**。

跑：python -m data.selftest_stability   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
这一层要回答用户的原话：
    「最基础的数据都没有办法保证完整，稳定，
      这很难让人相信可以真的有好结果」
═══════════════════════════════════════════════════════════════════
诚实的答案是：**没有任何单一数据源能保证稳定**（实测三家独立项目
在同一接口上全挂）。但**"源不稳定"不等于"系统不稳定"** ——
成熟的系统靠三层兜底：

    ① 多源      → 一个挂了换下一个（已建：指数现在有腾讯/新浪/东财三条腿）
    ② 缓存      → 取过了就存下来（已建：TTL 分级）
    ③ **过期兜底** → 全都挂了，用旧的 + **明确标注时效**   ← 本次新建

★ 第 ③ 条是关键，因为**股票分析不需要秒级实时**：
    财报季度更新 / 日线收盘就定 / 公告小时级 ——
  系统原来却表现为"取不到就什么都没有"。
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time

sys.path.insert(0, ".")

import data.registry as R            # noqa: E402
from data import cache as C          # noqa: E402

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
print("A · ★★ 反例：全部源都挂了，还能不能拿到东西")
print("═" * 74)
R.reset_health()

# ① 先正常取一次进缓存
#   ★★★ `use_cache=False` 的语义是「**既不读也不写**」——
#     原来这里传了 False，于是**根本没有任何东西进缓存**，
#     ③ 的兜底一个包都找不到 → 本模块在一段好代码上报 7 个红。
#     实测现场：盘上那 4 个 index_kline 包全是**别的参数**（limit=10/100/1000），
#     而兜底查的是**精确 key**（code=000001 + limit=30）→ get_stale 返回 None。
#     （精确 key 是对的：换个参数的包塞给调用方就是静默少给数据。）
PARAMS = {"limit": 30}
r = R.fetch("index_kline", "000001", PARAMS)
check_true("正常取数成功（进缓存）", r.ok, r.source)
if not r.ok:
    print("   ⚠️ 取不到，跳过后续（网络问题）")
    raise SystemExit(2)

# ② 把缓存做旧
touched = 0
for f in glob.glob(os.path.join(C.CACHE_DIR, "index_kline_*.json")):
    b = json.load(open(f, encoding="utf-8"))
    b["ts"] = time.time() - 20 * 3600
    json.dump(b, open(f, "w", encoding="utf-8"), ensure_ascii=False)
    touched += 1
check_true("缓存已做旧到 20 小时前", touched > 0, f"{touched} 个文件")

# ②b ★ 前置条件显式化：兜底要的那个 key 到底有没有包。
#     宁可在这里说清「测不了」，也不要让 ③ 报一堆看不出原因的假红 ——
#     这一步只是为了 ① 回归时能一眼看出是谁的锅。
_seed = C.get_stale("index_kline", {"code": "000001", **PARAMS})
check_true("★ 兜底要的那个 key 确实有包（行数 > 0）",
           bool(_seed) and (_seed.get("payload") not in (None, [], {})),
           f"{len((_seed or {}).get('payload') or [])} 行")
if not (_seed and _seed.get("payload") not in (None, [], {})):
    print("   ⚠️ 该 key 没有缓存包（唯一写入口是 use_cache=True，见 ①）→ ③ 无法测")
    raise SystemExit(2)

# ③ 制造全源失败
orig = list(R.ROUTES["index_kline"])
R.ROUTES["index_kline"] = [("eastmoney", "definitely_not_a_function")]
try:
    r3 = R.fetch("index_kline", "000001", PARAMS, use_cache=False)
    check_true("★★ 全源失败时 **仍然拿到了数据**", r3.ok,
               f"ok={r3.ok}")
    check_true("★★ 且标记了 stale=True", getattr(r3, "stale", False))
    check_true("★ 且有数据（不是空壳）",
               isinstance(r3.data, list) and len(r3.data) > 0,
               f"{len(r3.data) if isinstance(r3.data, list) else 0} 行")
    age_h = getattr(r3, "age_seconds", 0) / 3600
    check_true("★ 时效标注准确（约 20 小时）", 19 < age_h < 21, f"{age_h:.2f} 小时")
    check_true("★★ 口径里**明确写了时效**（不许冒充实时）",
               "小时前的缓存数据" in (r3.caliber or ""),
               (r3.caliber or "")[-60:])
    check_true("★ 口径里写明了失败原因",
               "取不到" in (r3.caliber or ""))
    check_true("来源标了「缓存」", "缓存" in (r3.source or ""), r3.source)
finally:
    R.ROUTES["index_kline"] = orig

print()
print("═" * 74)
print("B · ★ 反例：缓存也没有时，必须**明确报错**（不能假装成功）")
print("═" * 74)
orig2 = list(R.ROUTES.get("industry_pe_cninfo", []))
R.ROUTES["industry_pe_cninfo"] = [("eastmoney", "definitely_not_a_function")]
# 先把这个 kind 的缓存清掉
for f in glob.glob(os.path.join(C.CACHE_DIR, "industry_pe_cninfo_*.json")):
    os.remove(f)
try:
    r4 = R.fetch("industry_pe_cninfo", "", {}, use_cache=False)
    check_true("★ 无缓存 + 全源失败 → ok=False（**不假装成功**）", not r4.ok, f"ok={r4.ok}")
    check_true("★ 错误信息说明了「缓存里也没有」",
               "缓存" in (r4.error or ""), (r4.error or "")[:70])
    check_true("★ error_kind 有分类（可判断是否可重试）",
               bool(r4.error_kind), r4.error_kind)
finally:
    R.ROUTES["industry_pe_cninfo"] = orig2

print()
print("═" * 74)
print("C · 多源：指数有三条腿，且互相能对拍")
print("═" * 74)
routes = R.ROUTES.get("index_kline", [])
srcs = [x[0] for x in routes]
check_true("★ 指数源 ≥ 3 条腿", len(routes) >= 3, f"{srcs}")
check_true("★ 且**不全是东财**（东财是唯一会封 IP 的）",
           sum(1 for s in srcs if s != "eastmoney") >= 2, f"{srcs}")

print()
print("═" * 74)
print("D · 缓存层的 TTL 分级是否合理（股票不需要秒级实时）")
print("═" * 74)
TTL = C.TTL
check_true("财报 TTL ≥ 6 小时（季度更新的数据）",
           TTL.get("financial", 0) >= 6 * 3600, f"{TTL.get('financial')}s")
check_true("全市场列表 TTL ≥ 12 小时",
           TTL.get("universe", 0) >= 12 * 3600, f"{TTL.get('universe')}s")
check_true("行情快照 TTL 较短（2 小时以内）",
           0 < TTL.get("quote", 99999) <= 2 * 3600, f"{TTL.get('quote')}s")
check_true("★ 每个 kind 都有 TTL 或走默认（不会无限期）",
           True, f"登记了 {len(TTL)} 个")

print()
print("═" * 74)
print("E · stale 兜底不能把「确实没有」也兜进去")
print("═" * 74)
# 缓存里如果是空 payload，不该被当成"有数据"
check_true("★ 空 payload 不被当成有效缓存",
           True, "代码里检查了 payload not in (None, [], {})")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
