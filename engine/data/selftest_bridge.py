# -*- coding: utf-8 -*-
"""取数层自测 —— 重点是「两个独立实现对拍」。

跑：python -m data.selftest_bridge   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 为什么这个测试比普通单测值钱
═══════════════════════════════════════════════════════════════════
普通单测是「我认为它对」。对拍是「**两个不认识彼此的实现，给出了同一个数**」。

现在我们有两条完全独立的取数路径：
    vendor 路径 → a-stock-data → **腾讯财经** qt.gtimg.cn
    legacy 路径 → 我们自己的 → **东方财富** push2his.eastmoney.com

它们**不同的公司、不同的接口、不同的字段名、不同的复权实现**。
如果同一只票同一天的收盘价能对上，那这个数就极可能是对的。
**对不上就必须查清是谁错** —— 这正是我们做「数字可追源」的目的。

⚠️ 但要诚实：两家**前复权算法不同**（腾讯是等差口径，东财是按比例），
   所以复权价**本来就该有差异**。真正该对齐的是**不复权价**。
   → 对拍用 `adjust='none'`，复权价只做「量级一致性」检查。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

import polars as pl
from compute.load import LoadError, load_index_kline, load_kline, load_quote
from data.vendor_bridge import _default_source, bound_kinds, volume_unit

N_PASS = N_FAIL = 0


def check(name: str, got, want) -> None:
    global N_PASS, N_FAIL
    if got == want:
        N_PASS += 1
        print(f"  ✓ {name}")
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      期望 {want!r}，实际 {got!r}")


def check_true(name: str, cond: bool, detail: str = "") -> None:
    global N_PASS, N_FAIL
    if cond:
        N_PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        N_FAIL += 1
        print(f"  ✗ {name}\n      {detail}")


CODE = "300417"      # 南华仪器（创业板，有分红，能暴露复权差异）


# ══════════════════════════════════════════════════════════════════
print("═" * 72)
print("一、单路径可用性")
print("═" * 72)
df_v, meta_v = load_kline(CODE, bars=300, adjust="none", source="vendor")
check_true("vendor 路径取到数据", len(df_v) > 200, f"{len(df_v)} 行")
check_true("规范列齐全", set(["date", "open", "high", "low", "close"]).issubset(df_v.columns),
           str(df_v.columns))
check("source 记录了真实来源", meta_v["source"], "tencent")
check_true("as_of = 数据最后一天", meta_v["as_of"] == df_v["date"][-1],
           f"as_of={meta_v['as_of']} last={df_v['date'][-1]}")
check_true("eid 指纹非空", bool(meta_v["eid"]), meta_v["eid"][:20])
check_true("日期升序", df_v["date"].to_list() == sorted(df_v["date"].to_list()))

print()
print("═" * 72)
print("二、★★★ 对拍：腾讯（vendor） vs 东财（legacy），**不复权价**")
print("═" * 72)
df_l, meta_l = load_kline(CODE, bars=300, adjust="none", source="legacy")
print(f"  vendor: {len(df_v)} 行  {df_v['date'][0]} ~ {df_v['date'][-1]}  (source={meta_v['source']})")
print(f"  legacy: {len(df_l)} 行  {df_l['date'][0]} ~ {df_l['date'][-1]}  (source={meta_l['source']})")

common = sorted(set(df_v["date"].to_list()) & set(df_l["date"].to_list()))
check_true("两个源有共同交易日", len(common) > 150, f"{len(common)} 天")

if common:
    va = df_v.filter(pl.col("date").is_in(common)).sort("date")
    la = df_l.filter(pl.col("date").is_in(common)).sort("date")
    diffs = []
    for i in range(len(common)):
        a, b = float(va["close"][i]), float(la["close"][i])
        if a > 0 and b > 0:
            diffs.append(abs(a - b) / a)
    mean_d = sum(diffs) / len(diffs) if diffs else 1.0
    max_d = max(diffs) if diffs else 1.0
    print(f"  共同交易日 {len(common)} 天，收盘价相对差：均值 {mean_d*100:.4f}%  最大 {max_d*100:.4f}%")
    check_true("★ 两源收盘价均值差 < 0.5%", mean_d < 0.005,
               f"{mean_d*100:.4f}%（不同公司不同接口，能对上说明数据可信）")
    check_true("★ 最大差 < 3%", max_d < 0.03, f"{max_d*100:.4f}%")

    # 复权价只做量级检查（两家算法本来就不同）
    df_vq, _ = load_kline(CODE, bars=300, adjust="qfq", source="vendor")
    df_lq, _ = load_kline(CODE, bars=300, adjust="qfq", source="legacy")
    rv = float(df_vq["close"][-1]); rl = float(df_lq["close"][-1])
    print(f"  前复权最新收盘：vendor={rv}  legacy={rl}  差 {abs(rv-rl)/rv*100:.3f}%")
    check_true("前复权量级一致（< 20%，算法不同属正常）", abs(rv - rl) / rv < 0.20,
               "⚠️ 腾讯是等差口径、东财是按比例口径 —— 有差异是**预期内**的")

print()
print("═" * 72)
print("三、★★ 成交量单位（我们原先完全没有的概念）")
print("═" * 72)
cases = [("688017", "股", "科创板按股"), ("600519", "手", "沪深主板按手"),
         ("300417", "手", "创业板按手")]
for code, want, why in cases:
    unit, factor = volume_unit(code)
    ok = unit.startswith(want)
    check_true(f"{code} → {want}（{why}）", ok, f"实测 {unit}，折算系数 {factor}")

print()
print("═" * 72)
print("四、拒答与错误分类（不许静默退化）")
print("═" * 72)
try:
    load_kline("999999", bars=50)
    check_true("不存在的代码必须抛错", False, "居然没抛")
except LoadError as e:
    check_true("不存在的代码抛 LoadError", True, f"error_kind={e.error_kind}")

from data.vendor_bridge import fetch_via_vendor
r = fetch_via_vendor("完全不存在的类型xyz", CODE)
check("未绑定 kind 返回 unbound", r.error_kind, "unbound")
check_true("未绑定 kind 不返回数据", not r.ok)
check_true("错误信息说明已绑定数量", "未绑定" in (r.error or ""), (r.error or "")[:60])

print()
print("═" * 72)
print("五、源名推导（不编假源名）")
print("═" * 72)
for fn, want in (("tencent_quote", "tencent"), ("eastmoney_datacenter", "eastmoney"),
                 ("baostock_valuation_history", "baostock"), ("cls_telegraph", "cls"),
                 ("某个不认识的函数", "a-stock-data")):
    check(f"{fn:32s} → {want}", _default_source(fn), want)

print()
print("═" * 72)
print("六、快照（含僵尸报价检测）")
print("═" * 72)
q, mq = load_quote("600519")
check_true("快照取到", bool(q.get("name")), f"{q.get('name')} 价={q.get('price')}")
check_true("有 is_stale 字段（僵尸检测）", "is_stale" in q, f"is_stale={q.get('is_stale')}")
check_true("as_of 非空（快照用抓取时刻）", bool(mq["as_of"]), mq["as_of"])
check_true("单位写进字段名", "mcap_yi" in q and "amount_wan" in q,
           f"mcap_yi={q.get('mcap_yi')}亿元 amount_wan={q.get('amount_wan')}万元")

print()
print("═" * 72)
print("七、指数防取错（000001 是平安银行还是上证指数）")
print("═" * 72)
di, mi = load_index_kline("000001", bars=100)
check_true("★ 上证指数收盘在千点量级（没取成平安银行）", float(di["close"][-1]) > 1000,
           f"收盘={di['close'][-1]}")
print(f"      对照：kline('000001') 会取到平安银行（约 11 元）—— 两者差 300 倍且不报错")

print()
print("═" * 72)
print("八、★ 源优先级：腾讯优先、东财降为备胎（防静默回退）")
print("═" * 72)
# 背景：我们原来 24 个 kind 里 23 个首选东财，而**东财是唯一会封 IP 的源**。
# 实测关缓存：腾讯 458ms / 东财 1256ms。这条测试防的是「有人把顺序改回去」。
from data.registry import ROUTES

for kind in ("kline", "quote"):
    routes = [r[0] for r in ROUTES.get(kind, [])]
    check_true(f"{kind} 首选 vendor（腾讯）", routes[:1] == ["vendor"],
               " → ".join(routes))
    check_true(f"{kind} 保留东财为备胎（不删，它有独有数据）",
               "eastmoney" in routes, " → ".join(routes))

import data.registry as _R
_R.reset_health()
r_v = _R.fetch("kline", CODE, {"freq": "daily", "limit": 30, "fqt": 0}, use_cache=False)
check_true("默认路径实际走的是 vendor", (r_v.source or "").startswith("vendor"),
           f"实际源={r_v.source}")

# 降级能力：vendor 挂了必须能落到东财，不能直接失败
_orig = list(_R.ROUTES["kline"])
_R.ROUTES["kline"] = [x for x in _orig if x[0] != "vendor"]
try:
    r_fb = _R.fetch("kline", CODE, {"freq": "daily", "limit": 30, "fqt": 0}, use_cache=False)
    check_true("vendor 不可用时能回落到东财", r_fb.ok and "eastmoney" in (r_fb.source or ""),
               f"实际源={r_fb.source}")
finally:
    _R.ROUTES["kline"] = _orig

print()
print("═" * 72)
print("九、★★ 接线完整性 + index_kline 的坑（防再踩）")
print("═" * 72)
from data.registry import ROUTES as _ROUTES
from data.vendor_bridge import BINDINGS as _B

wired = [k for k, v in _ROUTES.items() if v and v[0][0] == "vendor"]
check_true("registry 认得的 kind 数 ≥ 50", len(_ROUTES) >= 50, f"{len(_ROUTES)} 个")
check_true("其中首选 vendor 的 ≥ 30", len(wired) >= 30, f"{len(wired)} 个")

# ⛔ 这条是踩出来的：把 index_kline 接给 vendor 会取到**平安银行**（11.76）而不是
#    上证指数（3800）—— 他们 tencent_kline 的 SH_INDEX 白名单里**没有 000001**，
#    于是落到 sz000001。而且**不报错**。我们那条「指数收盘必须在千点量级」
#    的断言当场抓住了它。这条测试防的是有人又想「顺手把指数也接过去」。
check_true("⛔ index_kline 不接 vendor（接了会取成平安银行）",
           _ROUTES["index_kline"][0][0] != "vendor",
           " → ".join(r[0] for r in _ROUTES["index_kline"]))

from compute.load import load_index_kline as _lik
_di, _ = _lik("000001", bars=10)
check_true("★ 上证指数收盘在千点量级（不是平安银行的 11 元）",
           float(_di["close"][-1]) > 1000, f"收盘={_di['close'][-1]}")

not_wired = sorted(set(_B) - set(_ROUTES))
print(f"     桥里绑定 {len(_B)} 个，已接线 {len(wired)} 个，待接线 {len(not_wired)} 个")
if not_wired:
    print(f"     待接线: {', '.join(not_wired[:10])}{' …' if len(not_wired) > 10 else ''}")

print()
print("═" * 72)
print("十、★★ lint：计算层不许直接读中文键（这个坑踩过两次）")
print("═" * 72)
# 背景：取数层从东财换成 a-stock-data 后，字段名从「收盘」变成「close」。
#   直接 x.get("收盘") 会**静默拿到 None → float(None or 0) → 全变 0**，
#   测试会一边显示「通过」一边在验算 0。
#   已咬过两次：selftest_risk（波动率全 0）、selftest_position（找不到位）。
#   → 计算层必须走 compute/load.py 的规范列。
import os as _os
import re as _re
# 匹配 .get("中文键") / .get('中文键')
_CN_KEYS = "日期|开盘|收盘|最高|最低|成交量_手|成交额_元|涨跌幅_%|名称|最新价"
_CN = _re.compile(r"\.get\(\s*[\"'](?:" + _CN_KEYS + r")[\"']")
_allowed = {
    _os.path.normpath("data/vendor_bridge.py"),        # 兼容双语，故意
    _os.path.normpath("data/sources/eastmoney.py"),    # 源本身，返回的就是中文键
    _os.path.normpath("compute/load.py"),              # 归一器，本来就干这个
}
_bad = []
for _root, _dirs, _files in _os.walk("."):
    if any(x in _root for x in ("__pycache__", "vendor", "_cache", ".git")):
        continue
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = _os.path.join(_root, _f)
        _rel = _os.path.normpath(_os.path.relpath(_p))
        if _rel in _allowed:
            continue
        try:
            _src = io.open(_p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for _i, _ln in enumerate(_src.splitlines(), 1):
            if _CN.search(_ln):
                _bad.append(_rel + ":" + str(_i))

check_true("★ 无违规读中文键的地方", len(_bad) == 0,
           ("违规：" + ", ".join(_bad[:6])) if _bad else "扫描通过")
print("     允许的例外（%d 个）：%s" % (len(_allowed), ", ".join(sorted(_allowed))))

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
