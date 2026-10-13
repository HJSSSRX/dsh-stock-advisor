# -*- coding: utf-8 -*-
"""法定看空触发器 —— **效能验收：漏检率 + 误杀率，每个边界值都测**。

跑：python -m judge.selftest_bear   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
为什么这个文件的形状和别人不一样
═══════════════════════════════════════════════════════════════════
用户原话：
    「每一个部分，请验证器效能，是否达到预期。我们本身就是防范 AI 幻觉，
      但是**不能我们自己构建的项目内容都是 AI 幻觉**」

一个"验证器"如果只测"它能说对"，是没有意义的：
    永远返回 HIT 的扫描器   → 通过所有正例
    永远返回 CLEAR 的扫描器 → 通过所有反例

→ 所以本文件给**两个数字**，并且**逐条触发器的边界值都要测**：

    漏检率  该命中的没命中（假阴性）
    误杀率  不该命中的误命中（假阳性）

★ 边界值是重点：`净利润 = 0` 到底算不算"为负"？`回撤 = 20.0` 算不算"≥20"？
  这类边界写错**不会报错**，只会安静地报错答案。所以每个触发器都测
  「刚好在阈值上」与「刚好在阈值外」两侧。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

sys.path.insert(0, ".")

from judge import bear as B                                  # noqa: E402

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


@dataclass
class F:
    """最小号的事实替身（触发器只要求 label/value/eid/kind）。"""
    label: str
    value: str
    eid: str = ""
    kind: str = ""


def facts(**kw) -> dict:
    """按 metric=值 造一组事实，自动给 eid。"""
    return {k: F(label=k, value=str(v), eid=f"eid_{k}", kind=kw.get("_kind", "quote"))
            for k, v in kw.items() if not k.startswith("_")}


def scan(f: dict, kinds) -> B.BearScan:
    return B.scan(f, set(kinds))


ALL = {"quote", "kline", "financial_sina", "st_list"}


print("═" * 74)
print("A · ★★★ 三态：数据没取到必须是 UNKNOWN，**绝不能算 CLEAR**")
print("═" * 74)
s = scan(facts(), set())                     # 什么数据都没有
check_true("零数据 → 全部 UNKNOWN", s.n_unknown == s.n_scanned,
           f"{s.n_unknown}/{s.n_scanned}")
check("  且一条都没命中", len(s.hits), 0)
check_true("★ 有 UNKNOWN 时 complete = False", s.complete is False, "")
rep = s.report()
# ⚠️ 第一版这里写的是 `"无风险" not in rep` —— **测试写错了**：
#    报告里那句恰恰是「≠「无风险」」，短语出现在它自己的否定式里。
#    （血泪清单第 9 条：测试挂了先怀疑测试。）
#    正确的断言是"必须带否定式免责声明 + 必须报出覆盖度"。
check_true("★★ 措辞纪律：必须带「这不等于没有风险」的否定式说明",
           "不等于" in rep, rep.split("\n")[-2][:56])
check_true("★★ 且必须报出覆盖度（覆盖 K/N），不许含糊",
           "覆盖" in rep, "")
check_true("★★ 且不许说「没有看空理由」", "没有看空理由" not in rep, "")
# 负对照：数据齐时 complete 必须为 True
s_full = scan(facts(归属于母公司所有者的净利润="1", 营业收入="1e9", 总市值="100",
                    市净率="2", MA60="1", 收盘价="2", 距250日高点回撤="1",
                    最新价="1", 跌停价="0.5", ST状态="否"), ALL)
check_true("★ 负对照：数据齐 → complete = True", s_full.complete is True,
           f"unknown={s_full.n_unknown}")

print()
print("═" * 74)
print("B · ★★ 逐条边界值：刚好在阈值上 / 刚好在阈值外")
print("═" * 74)
# 每条：(触发器 id, 需要的其他事实, 边界内值, 边界外值)
CASES = [
    ("DELIST-FIN-LOSS", "归属于母公司所有者的净利润", "-1", "0", "financial_sina"),
    # ★ 阈值按**现行**法规：营收 3 亿（不是旧规的 1 亿）+ 须与「利润孰低为负」同时成立
    ("DELIST-FIN-REV", "营业收入", "299999999", "300000000", "financial_sina"),
    ("DELIST-MKT-CAP", "总市值", "4.99", "5", "quote"),
    ("PB-BELOW-1", "市净率", "0.99", "1", "quote"),
    ("DRAWDOWN-20", "距250日高点回撤%", "20", "19.99", "kline"),
    ("NEAR-LIMIT-DOWN", "最新价", "101", "101.01", "quote"),
]
for tid, label, inside, outside, kind in CASES:
    base = {"最新价": "100", "跌停价": "1.0", "MA60": "1", "收盘价": "2",
            "归属于母公司所有者的净利润": "1", "营业收入": "1e9", "总市值": "100",
            "市净率": "2", "距250日高点回撤%": "1", "ST状态": "否"}
    if tid == "NEAR-LIMIT-DOWN":
        base["跌停价"] = "100"          # 阈值 = 跌停价 × 1.01 = 101
    if tid == "DELIST-FIN-REV":
        base["归属于母公司所有者的净利润"] = "-1e6"    # ★ 合取条件：利润也得为负
    k = ALL
    base[label] = inside
    r1 = scan(facts(**base), k).coverage.get(tid)
    base[label] = outside
    r2 = scan(facts(**base), k).coverage.get(tid)
    check(f"  {tid}：阈值内 {inside} → HIT", r1, B.HIT)
    check(f"  {tid}：阈值外 {outside} → CLEAR（边界不许沾边）", r2, B.CLEAR)

# MA60 与 ST 单独测（比较型 / 字符串型）
low = {"收盘价": "99", "MA60": "100"}
check("  BELOW-MA60：现价 < 均线 → HIT",
      scan(facts(**low), ALL).coverage.get("BELOW-MA60"), B.HIT)
eq = {"收盘价": "100", "MA60": "100"}
check("  BELOW-MA60：现价 = 均线 → CLEAR（相等不算「下方」）",
      scan(facts(**eq), ALL).coverage.get("BELOW-MA60"), B.CLEAR)
check("  ST-TAG：是 → HIT",
      scan(facts(ST状态="是（在风险警示名单中）"), ALL).coverage.get("ST-TAG"), B.HIT)
check("  ST-TAG：否 → CLEAR",
      scan(facts(ST状态="否（不在名单中）"), ALL).coverage.get("ST-TAG"), B.CLEAR)

print()
print("═" * 74)
print("C · ★★★ 效能双向指标：漏检率 与 误杀率")
print("═" * 74)
# 造 12 组「必须命中」的输入与 12 组「必须不命中」的输入，各覆盖不同触发器
SHOULD_HIT = [
    {"归属于母公司所有者的净利润": "-5e8"}, {"营业收入": "5e7", "归属于母公司所有者的净利润": "-1e6"},
    {"总市值": "1.5"}, {"市净率": "0.6"}, {"距250日高点回撤%": "35"},
    {"最新价": "100.5", "跌停价": "100"}, {"收盘价": "90", "MA60": "100"},
    {"ST状态": "是"}, {"归属于母公司所有者的净利润": "-1", "营业收入": "1"},
    {"市净率": "0.999"}, {"距250日高点回撤%": "20"}, {"总市值": "0.01"},
]
BASE_SAFE = {"归属于母公司所有者的净利润": "1", "营业收入": "1e10", "总市值": "1000",
             "市净率": "3", "距250日高点回撤%": "1", "最新价": "100", "跌停价": "50",
             "收盘价": "200", "MA60": "100", "ST状态": "否"}

miss = []
for i, override in enumerate(SHOULD_HIT):
    f = dict(BASE_SAFE); f.update(override)
    sc = scan(facts(**f), ALL)
    if not sc.hits:
        miss.append(f"#{i} {override} 应当命中却一条都没命中")

SHOULD_CLEAR = [
    {}, {"归属于母公司所有者的净利润": "0"}, {"营业收入": "1e10"}, {"营业收入": "9e7", "归属于母公司所有者的净利润": "2e8"},
    {"总市值": "1000"}, {"市净率": "1"}, {"距250日高点回撤%": "19.99"},
    {"最新价": "101.1", "跌停价": "100"}, {"收盘价": "100", "MA60": "100"},
    {"ST状态": "否"}, {"归属于母公司所有者的净利润": "1e9", "营业收入": "1e10"},
    {"市净率": "1.0001"}, {"距250日高点回撤%": "0"},
]
wrong = []
for i, override in enumerate(SHOULD_CLEAR):
    f = dict(BASE_SAFE); f.update(override)
    sc = scan(facts(**f), ALL)
    if sc.hits:
        wrong.append(f"#{i} {override} 不该命中却命中了 {[h.trigger_id for h in sc.hits]}")

print(f"    应当命中 {len(SHOULD_HIT)} 组 → 漏检 {len(miss)} 组")
print(f"    应当不命中 {len(SHOULD_CLEAR)} 组 → 误杀 {len(wrong)} 组")
print(f"    ★ 漏检率 = {pct(len(miss), len(SHOULD_HIT))}")
print(f"    ★ 误杀率 = {pct(len(wrong), len(SHOULD_CLEAR))}")
check_true("★★ 漏检率 = 0（该抓的假信号一个都不许漏）", not miss, str(miss[:3]))
check_true("★★ 误杀率 = 0（真数据一个都不许被冤枉）", not wrong, str(wrong[:3]))

print()
print("═" * 74)
print("D · ★ 命中必须引**该条触发器实际读的**证据，不许把所有事实都算成依据")
print("═" * 74)
f_hit = dict(BASE_SAFE); f_hit["市净率"] = "0.5"
sc = scan(facts(**f_hit), ALL)
pb = [h for h in sc.hits if h.trigger_id == "PB-BELOW-1"]
check("  破净命中", len(pb), 1)
if pb:
    check("★★ 只引「市净率」这一条依据（不是全部 10 条事实）", list(pb[0].eids), ["eid_市净率"])
# 负对照：另一条触发器的依据必须是它自己读的那个
f_hit2 = dict(BASE_SAFE); f_hit2["距250日高点回撤%"] = "50"
sc2 = scan(facts(**f_hit2), ALL)
dd = [h for h in sc2.hits if h.trigger_id == "DRAWDOWN-20"]
check("★ 负对照：回撤命中只引「距250日高点回撤%」", list(dd[0].eids) if dd else [],
      ["eid_距250日高点回撤%"])

print()
print("═" * 74)
print("E · ★ 每条触发器都要能追溯到法条（理据不许是空的）")
print("═" * 74)
check_true("★ 全部触发器都有非空 law 字段",
           all(t.law.strip() for t in B.TRIGGERS),
           f"{len(B.TRIGGERS)} 条")
check_true("★ 全部触发器都有非空 uses（否则引不到依据）",
           all(t.uses for t in B.TRIGGERS), "")
check_true("★ 全部触发器都有非空 need（否则判不出三态）",
           all(t.need for t in B.TRIGGERS), "")
bad = [t.id for t in B.TRIGGERS if not t.law.strip() or not t.uses or not t.need]
check_true("  没有一条残缺", not bad, str(bad))
# 已知局限必须写在报告里（设计的天花板要如实说）
check_true("★ 报告里写明了「覆盖不了基本面质量判断」这个天花板",
           "覆盖不了" in scan(facts(), set()).report(), "")
# 比法条宽松/会误报的已知偏差，必须写在触发器自己身上
notes = {t.id: t.note for t in B.TRIGGERS}
check_true("★ DELIST-FIN-LOSS 标注了“不是三者孰低”这个偏差",
           "三者孰低" in notes.get("DELIST-FIN-LOSS", ""), notes.get("DELIST-FIN-LOSS", ""))

print()
print("═" * 74)
print("G · ★★★ 法条 vs 经验阈值必须分开（独立验证点名的借法定权威）")
print("═" * 74)
law = [t.id for t in B.TRIGGERS if t.basis == "LAW"]
heu = [t.id for t in B.TRIGGERS if t.basis != "LAW"]
check("★ 有法规依据的 4 条", len(law), 4)
check("★ 经验/技术阈值的 4 条", len(heu), 4)
check_true("★ 两类都写明了 basis", all(t.basis in ("LAW", "HEURISTIC") for t in B.TRIGGERS), "")
bad_law = [t.id for t in B.TRIGGERS
           if t.basis != "LAW" and ("规则" in t.law or "第" in t.law or "证监会" in t.law)]
check_true("★★ 经验阈值的 law 字段不许出现法条式措辞", not bad_law, str(bad_law))
sc = B.scan(facts(**{"市净率": "0.5"}), ALL)
rep = sc.report()
check_true("★★ 报告里把两类分别计数", "有法规依据" in rep and "经验或技术阈值" in rep,
           rep.split("\n")[0][:70])
check_true("★★ 且明说经验阈值不许当成法条", "不许当成" in rep, "")
check_true("★ 命中项自己也带标记", "经验阈值" in rep, "")
false_alarm = B.scan(facts(**{"归属于母公司所有者的净利润": "2e8", "营业收入": "9e7",
                              "总市值": "100", "市净率": "2", "距250日高点回撒%": "1",
                              "最新价": "100", "跌停价": "50", "收盘价": "200",
                              "MA60": "100", "ST状态": "否"}), ALL)
check_true("★★ 独立验证抓到的误报已修：盈利 2 亿 + 营收 9000 万 → 不命中退市",
           not any(h.trigger_id.startswith("DELIST-FIN") for h in false_alarm.hits),
           str([h.trigger_id for h in false_alarm.hits]))
check_true("★ DELIST-MKT-CAP 标注了「没有验证连续 20 个交易日，会误报」",
           "会误报" in notes.get("DELIST-MKT-CAP", ""), notes.get("DELIST-MKT-CAP", ""))

print()
print("═" * 74)
print("F · ★★ uses 与 check 必须一致（实测漏检就是这里来的）")
print("═" * 74)
# 原理：把 `uses` 里声明的每条标签**逐条去掉**，该触发器必须变成 CLEAR。
# 如果去掉某条标签后它照样命中 —— 说明 check 根本没读它，`uses` 在说谎；
# 反过来，如果 check 读了 `uses` 里没声明的标签，去掉声明的那条它仍会 CLEAR，
# 但把**它真正读的那条**去掉却会…… 测不出来 —— 所以还配一条正向：单独喂 uses 全字段必须命中。
FULL = {"归属于母公司所有者的净利润": "-1", "营业收入": "1", "总市值": "1",
        "市净率": "0.5", "距250日高点回撤%": "50", "最新价": "100", "跌停价": "100",
        "收盘价": "1", "MA60": "100", "ST状态": "是"}
inconsistent: list[str] = []
for t in B.TRIGGERS:
    # ① 单独喂它声明的 uses → 必须 HIT（证明 uses 够用）
    only = {l: FULL[l] for l in t.uses if l in FULL}
    if len(only) != len(t.uses) or scan(facts(**only), ALL).coverage.get(t.id) != B.HIT:
        inconsistent.append(f"{t.id}: 只喂 uses 声明的那几条 → 没命中（uses 不足）")
        continue
    # ② 把 uses 里每一条单独抽掉 → 必须 CLEAR（证明 check 真的读了它）
    for drop in t.uses:
        sub = {k: v for k, v in only.items() if k != drop}
        if scan(facts(**sub), ALL).coverage.get(t.id) != B.CLEAR:
            inconsistent.append(f"{t.id}: 抽掉「{drop}」后仍然命中 → check 没读它")
check_true("★★ 每条触发器的 uses 声明与实际读取完全一致", not inconsistent,
           str(inconsistent[:3]))
print(f"    检查了 {len(B.TRIGGERS)} 条触发器 × 每条 uses 全字段抽掉一次")

print()
print("═" * 74)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 74)
sys.exit(1 if N_FAIL else 0)
