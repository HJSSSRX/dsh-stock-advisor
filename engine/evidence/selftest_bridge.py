# -*- coding: utf-8 -*-
"""取数→证据 桥的**效能验收** —— 不只看"能不能跑"，看"拦得住多少、冤枉了多少"。

跑：python -m evidence.selftest_bridge   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 这个文件为什么和别人不一样
═══════════════════════════════════════════════════════════════════
用户的原话：

    「每一个部分，请验证器效能，是否达到预期。我们本身就是防范 AI 幻觉，
      但是，不能我们自己构建的项目内容都是 AI 幻觉」

这句话指到了根上：**一个验证器，光测"它能说对"是没意义的。**

    永远返回 True 的验证器  → 通过所有正例
    永远返回 False 的验证器 → 通过所有反例

→ 所以本文件给每个验证器**两个数字**：

    漏检率（假阴性） 假的东西混过去了多少   ← 不为 0 说明形同虚设
    误杀率（假阳性） 真的东西被冤枉了多少   ← 不为 0 说明没法用

**只测一个方向的"验证"，就是自己给自己盖章。**

═══════════════════════════════════════════════════════════════════
★★ 三条"外部标准答案"（不是我写的，也不能是我写的）
═══════════════════════════════════════════════════════════════════
最危险的自欺是「用我写的测试，证明我写的代码是对的」。所以取证往外找：

    ① 交易所公告原文     素材/保真测试/mt2026h1.pdf（茅台 2026 半年报，110 页）
    ② 第二个独立数据源   新浪整理 vs 东财数据中心 —— 两家互不相干
    ③ 真实取数历史包     _cache/ 里 85 个包（真跑出来的，不是造的）

语料**离线**：读盘即可，不碰网络 → 结果可重复，不会"这次过下次挂"。
"""
from __future__ import annotations

import copy
import glob
import json
import os
import re
import sys
from dataclasses import replace

sys.path.insert(0, ".")

from data.result import Result                              # noqa: E402
from evidence import bridge as B                            # noqa: E402
from evidence.pdf_source import canon, extract, verify_number   # noqa: E402
from evidence.record import Evidence                        # noqa: E402
from judge import claim as C                                # noqa: E402
from judge.ledger import BULL                               # noqa: E402

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


_HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(_HERE), "_cache")
_CANDS = [os.path.normpath(os.path.join(_HERE, *([".."] * n), "素材", "保真测试", "mt2026h1.pdf"))
          for n in (2, 3, 4)]
PDF = next((p for p in _CANDS if os.path.exists(p)), _CANDS[0])

NUMISH = re.compile(r"^[-+]?\d[\d,]*(?:\.\d+)?$")
# 明显不是金额的字段名（日期/代码/名称…）—— 排除掉，避免把噪音算成"误杀"
_SKIP_WORDS = ("date", "日期", "时间", "报告期", "code", "代码", "名称", "name",
               "id", "市场", "状态", "类型", "小数位", "标签")

FIN = "44516880421.86"           # 茅台 2026H1 归母净利润（三处独立来源一致的那个数）
DOUBAO_NUM = "28012000000"       # 豆包实际回答的 280.12 亿


def is_amount_path(p: str) -> bool:
    low = p.lower()
    return not any(s.lower() in low for s in _SKIP_WORDS)


def collect_fields(data, prefix=""):
    """把一个 payload 里所有「数字型叶子字段」挖出来。"""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from collect_fields(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, (list, tuple)):
        for i, v in enumerate(data):
            yield from collect_fields(v, f"{prefix}.{i}" if prefix else str(i))
    elif isinstance(data, bool):
        return
    elif isinstance(data, (int, float)):
        yield prefix, data
    elif isinstance(data, str) and NUMISH.match(data.strip()):
        yield prefix, data


def set_path(data, path: str, value) -> None:
    """原地改 payload 里某个路径的值（用于伪造"被篡改的原始包"）。"""
    parts = str(path).split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, (list, tuple)) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def result_from_blob(blob) -> Result:
    """把缓存里的原始包还原成 Result（离线、可重复）。"""
    return Result(ok=True, kind=blob["kind"], code=blob["params"].get("code", ""),
                  data=blob["payload"], source=blob["source"],
                  as_of=blob.get("as_of", ""), caliber=blob.get("caliber", ""),
                  cached=True)


# ══════════════════════════════════════════════════════════════════
print("═" * 74)
print("0 · 语料：真实取数历史包（离线，可重复）")
print("═" * 74)
blobs = []
for f in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
    if os.path.basename(f).startswith("_"):
        continue
    try:
        with open(f, encoding="utf-8") as fh:
            b = json.load(fh)
    except Exception:                                       # noqa: BLE001
        continue
    if isinstance(b, dict) and "kind" in b and "payload" in b:
        blobs.append(b)
check_true(f"读到 {len(blobs)} 个真实包（≥ 60 才算够）", len(blobs) >= 60, str(len(blobs)))
kinds = sorted({b["kind"] for b in blobs})
print(f"    涵盖 {len(kinds)} 类：{'、'.join(kinds[:13])}{'…' if len(kinds) > 13 else ''}")

CORPUS: list[tuple[Result, str, object]] = []
for b in blobs:
    r = result_from_blob(b)
    for path, raw in collect_fields(b["payload"]):
        if path and is_amount_path(path):
            CORPUS.append((r, path, raw))
stride = max(1, len(CORPUS) // 3000)
SAMPLE = CORPUS[::stride][:3000]
check_true(f"抽出 {len(CORPUS)} 个数字字段，取 {len(SAMPLE)} 个做效能测量",
           len(SAMPLE) >= 500, f"全量 {len(CORPUS)} / 抽样 {len(SAMPLE)}")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("A · ★★ 误杀率：真数字被冤枉的比例（必须 0）")
print("═" * 74)
wronged: list[str] = []
built: list[tuple[Result, str, object]] = []
for r, path, raw in SAMPLE:
    try:
        ev = B.to_evidence(r, metric=path, path=path)
    except B.BridgeError as e:
        wronged.append(f"{path}: {e}")
        continue
    ok, why = B.trace(ev, r)
    if not ok:
        wronged.append(f"{path}: {why}")
        continue
    built.append((r, path, ev))

print(f"    真数字 {len(SAMPLE)} 个 → 成功 {len(built)}，被冤枉 {len(wronged)}")
print(f"    ★ 误杀率 = {pct(len(wronged), len(SAMPLE))}")
check_true("★★ 误杀率 = 0（真数据一个都不许被拦）", not wronged, str(wronged[:4]))

# ── ★ 实测发现：六元组并不齐 —— 单位基本拿不到 ──
n_complete = sum(1 for _, _, e in built if e.is_complete)
miss: dict[str, int] = {}
for _, _, e in built:
    for f in e.missing_fields:
        miss[f] = miss.get(f, 0) + 1
print(f"    六元组完整 {n_complete}/{len(built)} = {n_complete/len(built)*100:.1f}%")
for k, c in sorted(miss.items(), key=lambda kv: -kv[1]):
    print(f"      缺 {k}：{c} 个（{c/len(built)*100:.1f}%）")
check_true("★ 诚实报告：接口原始数据**大部分说不出单位**（这是发现，不是缺陷）",
           miss.get("unit", 0) > 0,
           "单位不是桥能猜的 —— 分析代码必须自己声明（见下面 A2）")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("A2 · ★★ 不完整的证据，下游**必须拦得住**（否则就是静默放行）")
print("═" * 74)
# 取一个缺单位的真证据（不是造的），看它能不能撑起一条结论
inc = next((e for _r, _p, e in built if not e.is_complete), None)
check_true("拿到一条「缺单位」的真证据", inc is not None,
           ("缺 " + "、".join(inc.missing_fields)) if inc else "")
if inc is not None:
    try:
        bad_claim = C.build(code="600519", as_of="2026-10-13", direction=BULL,
                            text="从接口原始数据直接下的结论", caliber="未复权",
                            citations=[C.Citation(evidence=inc, kind="quote")],
                            confidence=0.6)
        check_true("★★ 它被标成 [依据不足]（不完整 = 不许算依据）",
                   C.F_INSUFFICIENT in bad_claim.flags,
                   str(bad_claim.flags.get(C.F_INSUFFICIENT, "")))
        print(f"        {str(bad_claim.unusable[0][1])[:70] if bad_claim.unusable else ''}")
    except C.NoEvidenceError as e:
        check_true("★★ 它被标成 [依据不足]（不完整 = 不许算依据）", True,
                   "更严：整条结论构造不出来 → " + str(e)[:40])
# 负对照：补上单位后，同一条结论必须站得住
# ⚠️ 必须**重新构造** Evidence，不能用 dataclasses.replace ——
#    后者不会重算 eid，会造出一个"引用号在说谎"的证据（实测踩到）。
def rebuild(ev, **kw):
    f = dict(company=ev.company, period=ev.period, metric=ev.metric, value=ev.value,
             unit=ev.unit, source=ev.source, caliber=ev.caliber,
             origin_eid=ev.origin_eid, origin_path=ev.origin_path, kind=ev.kind)
    f.update(kw)
    return Evidence(**f)                     # 不传 eid → 按内容重算


if inc is not None:
    try:
        ok_claim = C.build(code="600519", as_of="2026-10-13", direction=BULL,
                           text="补上单位后的结论", caliber="未复权",
                           citations=[
                               C.Citation(evidence=rebuild(inc, unit="元"), kind="quote"),
                               C.Citation(evidence=rebuild(inc, unit="元",
                                                           metric=inc.metric + "（另一指标）"),
                                          kind="quote")],
                           confidence=0.6)
        check_true("★ 负对照：补上单位后不再标 [依据不足]（不是一律拦）",
                   C.F_INSUFFICIENT not in ok_claim.flags, str(list(ok_claim.flags)))
    except C.NoEvidenceError as e:
        check_true("★ 负对照：补上单位后不再标 [依据不足]", False, str(e)[:70])

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("B · ★★★ 漏检率：假数字混过去的比例（必须 0）")
print("═" * 74)
print("    三种攻击分别打 trace 的三道检查：改证据的值 / 改原始包 / 谎报来源")
miss_a: list[str] = []
miss_b: list[str] = []
miss_c: list[str] = []
probe = built[:: max(1, len(built) // 300)][:300]
for r, path, ev in probe:
    found, raw, _ = B.dig(r.data, path)

    # ── 攻击 A：改「证据里的数」，原始包不动 → 值比对必须抓到 ──
    fake_val = (raw + 1) if isinstance(raw, (int, float)) else str(raw) + "1"
    ev_a = replace(ev, value=canon(fake_val))
    if ev_a.value != ev.value:
        if B.trace(ev_a, r)[0]:
            miss_a.append(f"{path}: 把 {ev.value} 改成 {ev_a.value} 竟然通过")

    # ── 攻击 B：改「原始包里的数」，证据不动 → 包的内容指纹必须变 ──
    if found:
        p2 = copy.deepcopy(r.data)
        set_path(p2, path, fake_val)
        r_b = Result(ok=True, kind=r.kind, code=r.code, data=p2, source=r.source,
                     as_of=r.as_of, caliber=r.caliber)
        if B.trace(ev, r_b)[0]:
            miss_b.append(f"{path}: 原始包被改竟然通过")

    # ── 攻击 C：谎报来源（说这个数来自另一包）→ 必须抓到 ──
    ev_c = replace(ev, origin_eid="0000000000000000")
    if B.trace(ev_c, r)[0]:
        miss_c.append(f"{path}: 谎报原始包竟然通过")

tot_attack = len(probe) * 3
tot_missed = len(miss_a) + len(miss_b) + len(miss_c)
print(f"    攻击 A（改证据里的数）  试探 {len(probe)} → 漏检 {len(miss_a)}")
print(f"    攻击 B（改原始包）      试探 {len(probe)} → 漏检 {len(miss_b)}")
print(f"    攻击 C（谎报来源）      试探 {len(probe)} → 漏检 {len(miss_c)}")
print(f"    ★ 漏检率 = {pct(tot_missed, tot_attack)}")
check_true("★★ 漏检率 = 0（篡改一个都不许混过去）", tot_missed == 0,
           str((miss_a + miss_b + miss_c)[:4]))
# ★ 负对照：不被篡改的必须全部通过 —— 否则「一律判失败」也能得 0 漏检
n_ok = sum(1 for r, _p, e in probe if B.trace(e, r)[0])
check("★ 负对照：未篡改的证据全部通过（证明不是「一律判失败」蒙混）",
      n_ok, len(probe))

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("C · ★★★ 三方对质：两个独立源 + 交易所公告原文")
print("═" * 74)
DOC = extract(PDF) if os.path.exists(PDF) else None
if DOC is None:
    check_true(f"公告 PDF 存在（{PDF}）", False, "缺外部标准答案，无法对质")


def find_blob(kind: str):
    for b in blobs:
        if b["kind"] == kind and "44516880421" in json.dumps(b["payload"], ensure_ascii=False):
            return b
    return None


def pick_path(r):
    """在包里找出「值等于 FIN」的那个字段。

    ⚠️ 第一版是 `str(v).replace(".","")` 直接比 —— **漏了尾随零**，
    新浪的 "44516880421.860000" 就匹配不上。**这正是本模块要防的那个坑，
    结果在测试里先犯了一次**。所以必须走 canon 比。
    """
    for p, v in collect_fields(r.data):
        if canon(v) == canon(FIN):
            return p
    return None


b_sina, b_em = find_blob("financial_sina"), find_blob("financial_income")
check_true("找到新浪的财务包", b_sina is not None, b_sina["source"] if b_sina else "")
check_true("找到东财的财务包", b_em is not None, b_em["source"] if b_em else "")

ev_sina = ev_em = None
if b_sina and b_em:
    r_sina, r_em = result_from_blob(b_sina), result_from_blob(b_em)
    p1, p2 = pick_path(r_sina), pick_path(r_em)
    print(f"    新浪：source={r_sina.source}｜as_of={r_sina.as_of}｜字段 {p1}")
    print(f"    东财：source={r_em.source}｜as_of={r_em.as_of}｜字段 {p2}")
    check_true("两个源都能定位到同一个数字", bool(p1 and p2), "")
    if p1 and p2:
        # ⚠️ 必须带 unit —— 不带的话证据「六元组不全」，
        #    下面 E 段的 build() 会抛 NoEvidenceError（**这个模块崩过一次**，
        #    而且因为没人按退出码统计，它在好几轮"全绿"里被静默漏掉）。
        ev_sina = B.to_evidence(r_sina, metric="归母净利润", path=p1, unit="元",
                                caliber="net_profit_parent")
        ev_em = B.to_evidence(r_em, metric="归母净利润", path=p2, unit="元",
                              caliber="net_profit_parent")
        check_true("★ 两家给出的**数值完全一致**（归一后）",
                   ev_sina.value == ev_em.value,
                   f"新浪 {ev_sina.value} ｜ 东财 {ev_em.value}")
        check_true("★ 它们是**两条独立证据**（证据号不同）",
                   ev_sina.eid != ev_em.eid, f"{ev_sina.eid} vs {ev_em.eid}")
        if DOC is not None:
            v, why, _ = verify_number(DOC, ev_sina.value,
                                      expect_caliber="归属净利润", expect_amount=True)
            check("★★★ 这个数字在**交易所公告原文**里 = PASS", v, "PASS")
            print(f"        {why}")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("D · ★★ 拿豆包的错答案喂我们的验证器")
print("═" * 74)
if DOC is not None:
    v, why, _ = verify_number(DOC, DOUBAO_NUM,
                              expect_caliber="归属净利润", expect_amount=True)
    check("★★ 豆包答的 280.12 亿 → 公告原文里**找不到** → FAIL", v, "FAIL")
    print(f"        {why[:86]}")
    v2, why2, _ = verify_number(DOC, FIN, expect_caliber="归属净利润", expect_amount=True)
    check("★ 负对照：真值 445.17 亿 → PASS（不是「一律判失败」蒙混）", v2, "PASS")
    print(f"        {why2[:86]}")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("E · ★ 归一化：同一个数、两种写法，必须合成同一个值")
print("═" * 74)
raw_a, raw_b = "44516880421.860000", 44516880421.86
check_true("★ 陷阱是真的：两种写法的原始字符串**不一样**",
           str(raw_a) != str(raw_b), f"{raw_a!r} vs {raw_b!r}")
check("★★ 归一后**完全相同**（否则同一数字会变成两条「不一致的证据」）",
      canon(raw_a), canon(raw_b))
check_true("  ★ 归一**不做数值近似**：改一位仍是另一个数",
           canon(raw_a) != canon("44516880421.87"), "")
# ★★★ 非有限值（NaN / Infinity）**不是数**，归一必须安全放过而不是崩掉。
#     实测踩到：`Decimal("nan").as_tuple()` 的 exponent 是**字符串 'n'**，
#     下一行 `exp >= 0` 抛 TypeError，而 except 只抓了 InvalidOperation/ValueError
#     → **整个 selftest_bridge 模块崩掉**。
#     而且它是**潜伏**的：加了落盘缓存、新数据包含 NaN 之后才被引爆。
for _v, _desc in ((float("nan"), "NaN"), (float("inf"), "+Inf"),
                  (float("-inf"), "-Inf"), ("NaN", "字符串 NaN")):
    try:
        _r = canon(_v)
        check_true(f"★★★ 归一遇到 {_desc} 不许崩", True, f"→ {_r!r}")
    except Exception as _e:  # noqa: BLE001
        check_true(f"★★★ 归一遇到 {_desc} 不许崩", False,
                   f"{type(_e).__name__}: {_e}")
if ev_sina is not None and ev_em is not None:
    c = C.build(code="600519", as_of="2026-06-30", direction=BULL,
                text="上半年归母净利润 445.17 亿", caliber="合并报表｜未复权",
                citations=[C.Citation(evidence=ev_sina, kind="financial_sina"),
                           C.Citation(evidence=ev_em, kind="financial_income")],
                confidence=0.6)
    check_true("★★ 两个源对上了 → **不许**误报 [证据冲突]",
               C.F_CONFLICT not in c.flags,
               str(c.flags.get(C.F_CONFLICT, ""))[:70])
    check_true("  且结论站得住（无误报标记）", c.sufficient, str(list(c.flags)))

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("F · ★★ 静默失败防线：取不到 ≠ 0（血泪清单第 6 条）")
print("═" * 74)
fake = Result(ok=True, kind="quote", code="600519",
              data={"净利润": 0, "空值": "", "没有": None},
              source="测试", as_of="2026-10-13")
for label, path in (("字段不存在", "根本不存在的键"), ("空字符串", "空值"), ("None", "没有")):
    try:
        B.to_evidence(fake, metric="净利润", path=path)
        check_true(f"★ {label} → 必须抛 BridgeError", False, "竟然返回了一个值")
    except B.BridgeError as e:
        check_true(f"★ {label} → 抛 BridgeError", True, str(e)[:54] + "…")
# ★★ 负对照：真的 0 必须能出来（0 是合法数据，不是缺失）
try:
    ev0 = B.to_evidence(fake, metric="净利润", path="净利润", unit="元")
    check("★★ 负对照：值真的是 0 → 正常给出 0（没被当成缺失）", ev0.value, "0")
    check_true("  且带单位后是一条完整证据", ev0.is_complete, "")
    # ★ 负对照 2：**不传 unit 时它缺的是 unit，不是 value**
    #   （0 是合法数值；缺的是单位。两件事必须分得清。）
    ev0u = B.to_evidence(fake, metric="净利润", path="净利润")
    check_true("★ 不传 unit → 缺的是 unit（**不是**把 0 当成缺失）",
               (not ev0u.is_complete) and ("unit" in ev0u.missing_fields)
               and ("value" not in ev0u.missing_fields),
               f"missing={ev0u.missing_fields}")
except B.BridgeError as e:
    check_true("★★ 负对照：值真的是 0 → 正常给出 0", False, str(e)[:60])
fail_res = Result(ok=False, kind="quote", code="600519", error="三个源全挂了",
                  error_kind="source_error")
try:
    B.to_evidence(fail_res, metric="收盘价", path="price")
    check_true("★ 取数失败 → 必须抛（失败的东西不能当证据）", False, "竟然返回了值")
except B.BridgeError as e:
    check_true("★ 取数失败 → 抛 BridgeError", True, str(e)[:54] + "…")

# ══════════════════════════════════════════════════════════════════
print()
print("═" * 74)
print("G · ★ 单位/口径/时点 不许在转换中丢")
print("═" * 74)
# ★★★ 这一段原来找的是「payload 里带 `现价_单位` 的行情包」，**这种包根本不存在** ——
#   实测两条路都没有这个约定：
#     ① vendor 的 tencent_quote → 英文键 + 后缀表单位（amount_wan / mcap_yi / turnover_pct）
#     ② 我们的 tencent/sina/eastmoney 源 → 中文字段名，单位在 data/caliber.py 的表里
#   于是那段断言永远满足不了（**不是代码坏了，是断言瞄准了一个不存在的约定**），
#   连带 bridge.py 里的 `_unit_from_siblings` 一起清了。
#   现在改成：拿**真实行情包**验「口径 / 时点 / 指纹 / 单位不许猜」四件事。
qres = None
for b in blobs:
    if b["kind"] == "quote" and b["payload"]:
        qres = result_from_blob(b)
        break
if qres is not None:
    qpath = "0.price" if isinstance(qres.data, list) else "price"
    ev = B.to_evidence(qres, metric="现价", path=qpath)
    check_true("★ 口径不许丢（Result.caliber 带过来了）", bool(ev.caliber), ev.caliber[:40])
    check_true("★ 数据时点不许丢", bool(ev.period), ev.period)
    check_true("★ 原始包指纹记下了（能回溯）",
               bool(ev.origin_eid) and bool(ev.origin_path),
               f"{ev.origin_eid} / {ev.origin_path}")
    check_true("★★ 没人声明单位 → **留空**，不许看字段名猜",
               not (ev.unit or ""), repr(ev.unit))
    check_true("★★ 且因此被标成「不完整」（不完整的信息不许撑结论）",
               not ev.is_complete, "缺 " + "、".join(ev.missing_fields))
    ev_u = B.to_evidence(qres, metric="现价", path=qpath, unit="元")
    check("★ 调用方声明了单位 → 原样带过来（分析代码自己声明是唯一正路）",
          ev_u.unit, "元")
else:
    check_true("找到真实的行情包", False, "语料里没有行情包")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
