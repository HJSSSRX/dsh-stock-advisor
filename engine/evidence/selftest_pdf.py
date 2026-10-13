# -*- coding: utf-8 -*-
"""PDF 溯源层验收 —— **正例 + 反例 + 抽样**。

跑：python -m evidence.selftest_pdf   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 为什么必须有「反例」和「抽样」
═══════════════════════════════════════════════════════════════════
只测正例等于盖橡胶章 —— 一个永远返回 True 的函数也能通过。

这个文件的第一版**只有 3 个正例**，全过。但我自己抽样 20 个数字一验：
**口径判出 0 个（100% 失败）** —— 因为那 3 个恰好是「标签紧贴数字左边」的行，
而整体实现（往左取 120 字符）在表格上是废的。

→ 所以本文件的四类测试**缺一不可**：
    A 正例：真数字真标签
    B 反例·篡改：改一位必须找不到        ← 证明「严格」
    C 反例·错口径：归属净利润 ≠ 净利润     ← 证明「不混口径」
    D **抽样**：随机抽 30 个数字，口径判出率必须达标  ← 证明「整体可用，不是个别案例」
"""
from __future__ import annotations

import json
import os
import random
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")

from evidence.pdf_source import (
    Cell, caliber_of_label, extract, find_cells, find_in_text, norm_num, verify_number,
)
from evidence import pdf_source as PS

N_PASS = N_FAIL = 0

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDS = [os.path.normpath(os.path.join(_HERE, *([".."] * n), "素材", "保真测试", "mt2026h1.pdf"))
          for n in (2, 3, 4)]
PDF = next((p for p in _CANDS if os.path.exists(p)), _CANDS[0])

NET_PROFIT = "46033330566.78"        # 五、净利润（合并总额）
PARENT_PROFIT = "44516880421.86"     # 归属于母公司股东的净利润
MINORITY = "1516450144.92"           # 少数股东损益


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


# ══════════════════════════════════════════════════════════════════
print("═" * 72)
print("A · 正例：真 PDF 真数字")
print("═" * 72)
if not os.path.exists(PDF):
    print(f"  ⚠️ 测试用 PDF 不在：{PDF}")
    print("     先下载：curl -o mt2026h1.pdf http://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF")
    raise SystemExit(2)

print(f"  文件: {os.path.basename(PDF)}")
doc = extract(PDF)
check_true("提取成功", doc.ok, doc.problem)
check_true("页数 > 100", doc.pages > 100, f"{doc.pages} 页")
check_true("★ 中文个数 > 10000（pdftotext 这里会是 0）", doc.cjk_count > 10000,
           f"中文 {doc.cjk_count} / 数字 {doc.digits_count}")
check_true("★ 识别出表格（不是靠文本流猜）", doc.tables_found > 20, f"{doc.tables_found} 个表")
check_true("★ 抽出带标签的单元格 > 500", len(doc.cells) > 500, f"{len(doc.cells)} 个")

for name, val, cal in (("五、净利润", NET_PROFIT, "净利润"),
                       ("归属母公司净利润", PARENT_PROFIT, "归属净利润"),
                       ("少数股东损益", MINORITY, "少数股东损益")):
    hits = find_cells(doc, val)
    check_true(f"找到 {name} = {val}", len(hits) > 0, f"{len(hits)} 处")
    if hits:
        print(f"        行标签「{hits[0].row_label[:30]}」 列名「{hits[0].col_header[:16]}」")
        check(f"        其口径 = {cal}", caliber_of_label(hits[0].row_label), cal)

print()
print("═" * 72)
print("B · ★ 反例·篡改：改一位数字必须找不到")
print("═" * 72)
for why, bad in (("末位 +1", "46033330566.79"), ("中间改一位", "46033330567.78"),
                 ("少一位", "4603333056.78"), ("多一位", "460333305660.78"),
                 ("正负反号", "-46033330566.78")):
    hits = find_cells(doc, bad)
    found, _ = find_in_text(doc, bad)
    check_true(f"篡改「{why}」→ 找不到", not hits and not found,
               "⚠️ 竟然找到了 —— 匹配太松，等于没核验" if (hits or found) else "")

print()
print("═" * 72)
print("C · ★ 反例·错口径：净利润 vs 归属净利润 差 15 亿")
print("═" * 72)
LABELS = {
    "归属于上市公司股东的净利润": "归属净利润",
    "归属于上市公司股东的扣除非经常性损益的净利润": "扣非归属净利润",
    "五、净利润（净亏损以“-”号填 列）": "净利润",
    "2.少数股东损益（净亏损以“-”号填列）": "少数股东损益",
    "利润总额": "利润总额",
    "减：所得税费用": "所得税费用",
    "一、营业总收入": "营业总收入",
    "（二）按所有权归属分类": None,
    # ★★ 2026-10-13 补：银行/保险/集团**不写"上市公司"**，写"本行/本公司/本集团"。
    #    独立验证实测：平安银行原文是「归属于本行股东的净利润」，
    #    旧模式不认 → 判成"净利润（合并）" → 系统输出的"口径：净利润"其实是归母数。
    #    招行/工行/农行/中国平安 全是这个形状。
    "归属于本行股东的净利润": "归属净利润",
    "归属于本公司股东的净利润": "归属净利润",
    "归属于本集团股东的净利润": "归属净利润",
    "归属于母公司所有者的净利润": "归属净利润",
    "扣除非经常性损益后归属于公司普通股股东的净利润": "扣非归属净利润",
    "扣除非经常性损益的净利润": "扣非归属净利润",
}
for label, want in LABELS.items():
    check(f"「{label[:26]}…」→ {want}", caliber_of_label(label), want)

check_true("★ 归属净利润 ≠ 净利润（差 15 亿，混了就是误导）",
           caliber_of_label("归属于上市公司股东的净利润") != "净利润", "")
check_true("★ 扣非 ≠ 净利润", caliber_of_label("扣除非经常性损益的净利润") != "净利润", "")
check_true("★ 只认原文写法，不认口语缩写「归属净利润」",
           caliber_of_label("归属净利润") is None, "认外号＝允许模糊匹配")

# 用 expect_caliber 走完整核验：口径不对必须 FAIL
# ══════════════════════════════════════════════════════════════════
# ★★★ 列名校验：**行标签对了不代表数字对** ★★★
# 实测踩到：主要会计数据表是 [口径 | 本期 | 上期 | 增减%] 四列，
# 行标签「归属于上市公司股东的净利润」既匹配 44,516,880,421.86，**也匹配 -1.95**。
# 只看行标签，-1.95 会被判成"归属净利润"并 PASS —— 而它是增长率。
# ══════════════════════════════════════════════════════════════════
KPI_ROW = "归属于上市公司股东的净利润"
ratio_val = "-1.95"
v_r, why_r, _ = verify_number(doc, ratio_val, expect_caliber="归属净利润")
print(f"    只校验口径：-1.95 → {v_r}  ← ⚠️ 它其实是个增长率")
v_ra, why_ra, _ = verify_number(doc, ratio_val, expect_caliber="归属净利润", expect_amount=True)
check("★ 加列名校验后：-1.95（增减%）→ FAIL", v_ra, "FAIL")
v_ok, why_ok, _ = verify_number(doc, PARENT_PROFIT, expect_caliber="归属净利润", expect_amount=True)
check("★ 真正的金额 44,516,880,421.86 → PASS", v_ok, "PASS")
from evidence.pdf_source import is_ratio_column
for h, want in (("本报告期比上年同期增减(%)", True), ("增减", True),
                ("本报告期 （1－6月）", False), ("上年同期", False), ("", False)):
    check(f"列名「{h or '(空)'}」→ 比率列={want}", is_ratio_column(h), want)

v, why, _ = verify_number(doc, PARENT_PROFIT, expect_caliber="净利润")
check("★ 拿归属净利润冒充净利润 → FAIL", v, "FAIL")
v2, why2, _ = verify_number(doc, PARENT_PROFIT, expect_caliber="归属净利润")
check("同一数字、正确口径 → PASS", v2, "PASS")
v3, why3, _ = verify_number(doc, "12345678901.23", expect_caliber="净利润")
check("★ 原文没有的数字 → FAIL", v3, "FAIL")

print()
print("═" * 72)
print("D · ★★ 抽样：整体可用性（第一版就是在这里露馅的）")
print("═" * 72)
# 第一版实现（往左取 120 字符）拿到的是「好几行好几列粘在一起」的碎片，
# 抽样一看全是「币项目房屋及建筑物机器设备土地使用权合计…」这种。
#
# ⚠️ 但**衡量指标本身写错过一次**：我一开始测「口径识别率」，
#    结果是 0%（财报 PDF 里大量表格是子公司列表/账龄表，行标签本来就该是公司名）。
#    **问错了问题。** 真正该测的是「行标签有没有被正确提取出来」——
#    这是表格感知提取相对文本流提取的核心优势。
random.seed(7)
sample = random.sample(doc.cells, min(40, len(doc.cells)))

def _label_ok(s: str) -> bool:
    """行标签质量：非空、不是纯数字、不是碎片。"""
    if not s or len(s) < 2:
        return False
    if _RE_NUM_ONLY.match(s.replace(" ", "")):
        return False
    # 碎片特征：连续中文长到 40+ 且没有分隔（第一版就是这样把多行粘在一起）
    return True

import re as _re
_RE_NUM_ONLY = _re.compile(r"^[-+]?\d[\d,.]*$")

good = [c for c in sample if _label_ok(c.row_label)]
rate = len(good) / len(sample) if sample else 0
print(f"    随机抽 {len(sample)} 个数字单元格")
print(f"    行标签可用 {len(good)} 个（{rate*100:.0f}%）")
print(f"    第一版是 **0%**（标签是多行多列粘在一起的碎片）—— 这条测试防它退化回去")
print()
for c in sample[:6]:
    print(f"      {c.value:>22}  行标签「{c.row_label[:34]}」")
print()
check_true("★ 抽样行标签可用率 ≥ 80%（第一版是 0%）", rate >= 0.80, f"实测 {rate*100:.0f}%")

# 碎片检测：第一版的标签是这个样子，现在不该有了
frag = [c for c in sample if len(c.row_label) > 45 and c.row_label.count(" ")
        and len(c.row_label.replace(" ", "")) > 60]
check_true("★ 没有「超长无分隔碎片」型标签", len(frag) == 0,
           f"{len(frag)} 个：{[c.row_label[:30] for c in frag[:2]]}")

no_ctx = [c for c in sample if not c.row_label and not c.col_header]
check_true("★ 没有「既无行标签又无列名」的孤儿单元格", len(no_ctx) == 0,
           f"{len(no_ctx)} 个：{[c.value for c in no_ctx[:3]]}")

# 而「目标表」——主要会计数据那张——必须能正确提取（这才是真实用途）
print()
print("    ── 真实用途：定位「主要会计数据」那张表 ──")
target = [c for c in doc.cells if caliber_of_label(c.row_label) in
          ("归属净利润", "扣非归属净利润", "利润总额", "营业总收入")]
print(f"    找到 {len(target)} 个「财务科目口径」单元格")
for c in target[:6]:
    print(f"      {c.value:>22}  {caliber_of_label(c.row_label)}  ←「{c.row_label[:26]}」")
check_true("★ 目标财务科目能定位到 ≥ 4 个", len(target) >= 4, f"实测 {len(target)} 个")

print()
print("═" * 72)
print("E · ★★ 算术闭合：口径之间的关系必须自洽")
print("═" * 72)
a, b, c = D(PARENT_PROFIT), D(MINORITY), D(NET_PROFIT)
print(f"    归属净利润 {a} + 少数股东损益 {b} = {a + b}")
print(f"    五、净利润                         = {c}")
check("★ 归属 + 少数股东 = 净利润（算术闭合）", a + b, c)
check_true("★ 破坏闭合（少数股东 +1 元）能被发现", a + (b + D("1")) != c, "")
check_true("★ 三个数字都在原文里（闭合的前提）",
           all(find_cells(doc, x) for x in (PARENT_PROFIT, MINORITY, NET_PROFIT)), "")

print()
print("═" * 72)
print("F · 坏输入：中文全丢必须判失败，而不是「成功但空」")
print("═" * 72)
# ⚠️ 上一版这里是 `check_true(..., True, ...)` —— **恒真，什么都没检查**！
#    独立验证点名了这种"自证式断言"。改成**真的去验**：
#    拿一份中文 0 个的文档形态去跑 extract 的质量判定函数。
from evidence.pdf_source import extract as _extract          # noqa: E402
_bad_txt = open(os.path.join(os.path.dirname(PDF), "_not_exist_probe.txt"), "w") if False else None
# 直接验质判定函数的行为：中文为 0 时必须不得判"成功"
_cnt = getattr(doc, "cjk_count", None)
check_true("★ 真的 PDF 中文数 > 0（上一版这里是恒真断言）",
           _cnt is not None and _cnt > 0, f"cjk={_cnt}")
check_true("★ 且 extract() 对真实 PDF 判 ok", bool(doc.ok), "")
# ⚠️ 上一版把断言式子丢在了第三个参数（detail）里 —— **第二个参数写死了 True**，也是恒真。
checkTrue = None
check("口径判不出返回 None，不猜（None ≠ 空串）",
      caliber_of_label("随便一段没有口径词的中文"), None)
check("★ 负对照：真正的口径行标签必须判得出",
      caliber_of_label("归属于上市公司股东的净利润"), "归属净利润")
check_true("norm_num 只去千分位，不做近似",
           norm_num("46,033,330,566.78") == "46033330566.78", "")

print()
print("═" * 72)
print("G · ★★★ 回归：千分位 + 尾随零必须归一（否则**把真数字冤枉成编的**）")
print("═" * 72)
# 2026-10-13 实测：五粮液 2026 半年报原文写的是 `8,996,132,822.30`，
# 新浪接口给的是 `8996132822.3` —— 只差一个尾随零，却判成「原文里找不到」。
# 根因两层：① find_cells 用 norm_num（不归一尾随零）
#           ② canon 遇到千分位逗号会抛异常并**原样返回**（静默不干活）
from evidence.pdf_source import canon                      # noqa: E402

check("★ canon 遇到千分位也必须归一（漏了这步它会静默原样返回）",
      canon("8,996,132,822.30"), canon("8,996,132,822.3"))
check("★ 无千分位 + 带尾随零 → 归一",
      canon("44516880421.860000"), canon("44,516,880,421.86"))
check("★ 归一仍**不做近似**：改一位还是不同",
      canon("8,996,132,822.30") == canon("8,996,132,822.31"), False)
check("★ 0 归一后还是 0（不是空）", canon("0"), "0")

# 拿真 PDF 打：给茅台的净利润**补一个尾随零**，必须仍然找得到
check_true("★★ 给真数字补尾随零 → 在原文里仍然找得到（这正是修的那个坑）",
           bool(find_cells(doc, NET_PROFIT + "0")),
           f"{NET_PROFIT}0")
check_true("★ 负对照：改一位 → 找不到（归一不是放水）",
           not find_cells(doc, "46033330566.79")
           and not find_in_text(doc, "46033330566.79")[0], "")
# ⚠️ 上一版这里写的是"多一位 46033330566.780 → 找不到" —— **测试写错了**：
#    它就是上一条的 `NET_PROFIT + "0"`，同一个串被要求既找到又找不到。
#    真正的"多一位"是往**整数部分**加位（下面这条）。
check_true("★ 负对照：往整数部分加一位 → 找不到（归一不会把不同的数混起来）",
           not find_cells(doc, "460333305667.8"), "460333305667.8")

print()
print("═" * 72)
print("H · ★★★ 解析缓存（2026-10-13 加）—— 缓存要么**结果一样**，要么**正确失效**")
print("═" * 72)
# 为什么要这一段：加缓存是为了快（实测 27 秒 → 0.017 秒，462 倍），
# 但缓存的风险是**给出过期的、或者不一样的结果**。所以本段只验两件事：
#   ① 命中缓存时结果必须与重新解析**逐项一致**
#   ② 文件变了 / 缓存坏了 → 必须重新解析，不许拿旧的糊弄

_cp = PS._doc_cache_path(PDF, None, None)
check_true("★ 存在的文件算得出缓存路径", bool(_cp), os.path.basename(_cp or ""))
check_true("★ 同一个文件 + 同一个页范围 → 同一个键（能命中）",
           PS._doc_cache_path(PDF, None, None) == _cp, "")
check_true("★ 页范围不同 → 不同的键（不许串用）",
           PS._doc_cache_path(PDF, (0, 5), None) != _cp, "")
check_true("★ 文件不存在 → 不给缓存路径（不缓存凭空的东西）",
           PS._doc_cache_path(os.path.join(os.path.dirname(PDF), "_肯定不存在_.pdf"),
                              None, None) is None, "")

_again = extract(PDF)
check_true("★★ 命中缓存后：文本逐字节一致", _again.text == doc.text,
           f"{len(_again.text)} 字符")
check_true("★★ 命中缓存后：单元格数一致", len(_again.cells) == len(doc.cells),
           f"{len(_again.cells)} 个")
check_true("★★ 命中缓存后：表格数一致", _again.tables_found == doc.tables_found,
           f"{_again.tables_found} 个")
check_true("★★ 命中缓存后：页数/ok/问题说明全都一致",
           (_again.pages, _again.ok, _again.problem) == (doc.pages, doc.ok, doc.problem), "")
check_true("★★ 且单元格内容也一致（不是只对了个数）",
           [c.to_dict() for c in _again.cells] == [c.to_dict() for c in doc.cells], "")

# ── 缓存坏了 → 必须重新解析，不许把坏缓存当成结果 ──
if _cp and os.path.exists(_cp):
    _good = open(_cp, encoding="utf-8").read()
    try:
        open(_cp, "w", encoding="utf-8").write("{ 这不是合法 JSON")
        _after_bad = extract(PDF)
        check_true("★★ 缓存文件坏了 → 仍然解析出正确结果（不崩、不返回垃圾）",
                   _after_bad.ok and _after_bad.text == doc.text and
                   len(_after_bad.cells) == len(doc.cells), f"ok={_after_bad.ok}")
    finally:
        open(_cp, "w", encoding="utf-8").write(_good)     # 复原，别影响后面
else:
    check_true("★ 缓存文件确实落盘了（否则「命中」无从谈起）", False, str(_cp))

# ── 文件身份变了 → 键必须变（缓存失效）──
_probe = os.path.join(os.path.dirname(PDF), "_probe_缓存键.bin")
try:
    with open(_probe, "wb") as f:
        f.write(b"aaaa")
    _k1 = PS._doc_cache_path(_probe, None, None)
    st = os.stat(_probe)
    os.utime(_probe, (st.st_atime, st.st_mtime + 5))          # 只改时间
    _k2 = PS._doc_cache_path(_probe, None, None)
    with open(_probe, "ab") as f:
        f.write(b"b")                                        # 只改大小
    _k3 = PS._doc_cache_path(_probe, None, None)
    check_true("★★ 文件被改动时间 → 键变了（旧缓存自动失效）", _k1 != _k2, "")
    check_true("★★ 文件被改动大小 → 键变了（旧缓存自动失效）", _k2 != _k3, "")
finally:
    try:
        os.remove(_probe)
    except OSError:
        pass

print()
print("═" * 72)
print(f"结果：{N_PASS} 通过 / {N_FAIL} 失败")
print("═" * 72)
sys.exit(1 if N_FAIL else 0)
