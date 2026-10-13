# -*- coding: utf-8 -*-
"""PDF 原文提取 —— 证据层的「一手来源」通道。

═══════════════════════════════════════════════════════════════════
两个坑，都是实测踩出来的，务必别改回去
═══════════════════════════════════════════════════════════════════

★★ 坑一：**pdftotext 会静默丢掉全部中文**

    实测（茅台 2026 半年报 1225475868.PDF，110 页）：
        pdftotext:   198,047 字符 → **中文 0 个**、数字 33,493 个
        PyMuPDF:     中文 57,845 个 ✓

    中文全丢意味着：
        ✅ 能证明「这个数字在原文里出现过」
        ❌ **不能证明「这个数字是净利润」** —— 标签没了，关联断了
    而「数字可追源」的核心恰恰是后者。
    → **只用 PyMuPDF。**

★★ 坑二：**「往左取 N 个字符当标签」在表格上完全失效**

    第一版就是这么做的，我自己抽样 20 个数字验证 → **口径判出 0 个（100% 失败）**。
    提取出来的"标签"长这样：
        「币项目房屋及建筑物机器设备土地使用权合计一账面原值期初余额」
        「厂集团文化旅游有限责任公司母公司的全资子公司接受劳务劳务费协议价」
    根因：PDF 出来的是**按视觉位置拼的文本流**，表格的标签列和数字列分离，
    "往左取 120 字符"会把好几行好几列的中文粘在一起，**完全没有边界**。

    → **必须用 `page.find_tables()`**。它按单元格还原，一行一个口径：
        ['归属于上市公司股东的净利润', '44,516,880,421.86', '45,402,962,298.10', '-1.95']

    ⚠️ 第一版的 3 个正例测试之所以通过，是因为**恰好选了标签紧贴数字左边的那几行**。
       **用抽样验证才发现整体是废的。** —— 所以本模块的验收必须含抽样。

═══════════════════════════════════════════════════════════════════
为什么数字必须连口径一起抓
═══════════════════════════════════════════════════════════════════
实测茅台 2026H1，同一张表里：
    五、净利润                    46,033,330,566.78   ← 合并总额
    1.归属于母公司股东的净利润      44,516,880,421.86   ← 股东享有的
    2.少数股东损益                 1,516,450,144.92
**两个差 15 亿，都"对"。** 媒体说「净利润」通常指后者，接口返回的常是前者。
不连口径一起抓 → 无法判断对错 → 「可追源」是空的。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

import pymupdf

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_NUM_FULL = re.compile(r"^[-+]?\d[\d,]*(?:\.\d+)?$")


@dataclass
class Cell:
    """表格里的一个数字单元格 —— 带它的行标签和列名。"""
    value: str            # 数字原文（保留千分位，便于回原文核对）
    row_label: str        # 该行的第一个非空单元格（通常是口径名）
    col_header: str       # 该列的列名（本期/上期/增减）
    page: int
    row_index: int
    col_index: int

    def to_dict(self) -> dict:
        return {"值": self.value, "行标签": self.row_label,
                "列名": self.col_header, "页": self.page}


@dataclass
class PdfDoc:
    """一份 PDF 的提取结果。"""
    path: str = ""
    url: str = ""
    pages: int = 0
    text: str = ""                                   # 全文纯文本（辅助）
    cjk_count: int = 0
    digits_count: int = 0
    cells: list[Cell] = field(default_factory=list)   # ★ 带标签的数字（主产物）
    tables_found: int = 0
    ok: bool = False
    problem: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "url": self.url, "页数": self.pages,
                "中文个数": self.cjk_count, "数字个数": self.digits_count,
                "表格数": self.tables_found, "带标签数字": len(self.cells),
                "ok": self.ok, "问题": self.problem}


# ══════════════════════════════════════════════════════════════════════
# ★★★ 解析结果缓存（2026-10-13 加）
# ══════════════════════════════════════════════════════════════════════
# 实测（cProfile 一次真实提问）：
#     analyze 全程 34.3s
#       └ _collect_financial 27.1s
#           └ _cross_check_pdf 27.1s
#               └ extract 26.9s   ← **PyMuPDF 的 find_tables 逐页爬了 154 次**
# 也就是说：**网络只占 7 秒，剩下 27 秒是在反复重新解析同一份 PDF。**
# PDF 文件本身早就缓存了（_pdf_cache/*.pdf），**但解析结果没缓存** ——
# 每问一次就把同一份年报重新拆一遍表。
#
# 键 = 路径 + 文件大小 + mtime + 页范围 → 文件没变就永远命中，改了立刻失效。
_DOC_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_pdf_cache", "_docs")


def _doc_cache_path(path: str, pages, max_pages) -> Optional[str]:
    """按「文件身份 + 页范围」算缓存路径。文件取不到 stat 就不缓存。"""
    try:
        st = os.stat(path)
        ident = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
    except OSError:
        return None
    key = hashlib.sha1(f"{ident}|{pages}|{max_pages}".encode("utf-8")).hexdigest()[:20]
    return os.path.join(_DOC_CACHE_DIR, f"doc_{key}.json")


def _read_doc_cache(cp: str, url: str) -> Optional["PdfDoc"]:
    try:
        with open(cp, encoding="utf-8") as f:
            b = json.load(f)
        return PdfDoc(path=b["path"], url=url or b.get("url", ""), pages=b["pages"],
                      text=b["text"], cjk_count=b["cjk_count"],
                      digits_count=b["digits_count"],
                      cells=[Cell(**c) for c in b["cells"]],
                      tables_found=b["tables_found"], ok=b["ok"], problem=b["problem"])
    except Exception:                                        # noqa: BLE001
        return None                     # ★ 缓存坏了/版本变了 → 当没有，绝不影响正路


def _write_doc_cache(cp: str, doc: "PdfDoc") -> None:
    tmp = cp + ".tmp"
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(doc), f, ensure_ascii=False, default=str)
        os.replace(tmp, cp)             # 原子替换，防止写一半被读到
    except Exception:                                        # noqa: BLE001
        pass                            # ★ 写不进去就算了，不许影响取数


def extract(path: str, *, url: str = "", pages: Optional[tuple[int, int]] = None,
            max_pages: Optional[int] = None) -> PdfDoc:
    """提取 PDF：全文 + **表格单元格（带行标签/列名）**。

    ★ 带解析缓存（见上面那段实测）—— 同一份文件第二次开始是毫秒级。
    """
    cp = _doc_cache_path(path, pages, max_pages)
    if cp:
        hit = _read_doc_cache(cp, url)
        if hit is not None:
            return hit
    doc = _extract_uncached(path, url=url, pages=pages, max_pages=max_pages)
    if cp:
        _write_doc_cache(cp, doc)       # ★ 失败结果也缓存：扫描件重试一次还是扫描件
    return doc


def _extract_uncached(path: str, *, url: str = "",
                      pages: Optional[tuple[int, int]] = None,
                      max_pages: Optional[int] = None) -> PdfDoc:
    """真正的解析（贵的那部分）。**不要直接调它** —— 走 `extract`。

    ★ 自检：中文个数为 0 而数字很多 → 判 ok=False。
      **绝不返回「提取成功但中文全空」** —— 那正是 pdftotext 的行为，
      会让上层以为拿到了原文，而实际上标签全丢了。
    """
    doc = PdfDoc(path=path, url=url)
    try:
        d = pymupdf.open(path)
    except Exception as exc:                          # noqa: BLE001
        doc.problem = f"打开失败：{type(exc).__name__}: {exc}"
        return doc

    try:
        doc.pages = d.page_count
        lo, hi = (pages if pages else (0, doc.pages))
        if max_pages is not None:
            hi = min(hi, lo + max_pages)
        texts, cells, ntables = [], [], 0
        for i in range(lo, min(hi, doc.pages)):
            page = d[i]
            texts.append(page.get_text())
            try:
                tabs = page.find_tables()
            except Exception:                          # noqa: BLE001
                continue
            for tb in tabs.tables:
                ntables += 1
                cells.extend(_table_cells(tb.extract(), i + 1))
        doc.text = "\n".join(texts)
        doc.tables_found = ntables
        doc.cells = cells
    finally:
        d.close()

    doc.cjk_count = len(re.findall(r"[\u4e00-\u9fff]", doc.text))
    doc.digits_count = len(re.findall(r"\d", doc.text))

    # ── 自检一：中文全丢（pdftotext 的典型症状）──
    if doc.digits_count > 100 and doc.cjk_count == 0:
        doc.problem = (f"提取出 {doc.digits_count} 个数字但**中文 0 个** —— 解析器不适用"
                       "（pdftotext 的典型症状）；换 PyMuPDF 或做 OCR")
        return doc

    # ── 自检二：★ 扫描件/图片型 PDF ──
    # 判据：**每页中文数少得可怜**。文字版财报每页通常上千字，
    # 扫描件没有文字层 → PyMuPDF 只能提出零星页码/数字。
    # ⚠️ 必须明确报「需 OCR」，**不能返回一个"提取成功但几乎没内容"的结果** ——
    #    上层会以为拿到了原文，然后在里面找不到任何数字，误判成「数据造假」。
    pages = max(doc.pages, 1)
    cjk_per_page = doc.cjk_count / pages
    if cjk_per_page < 50:
        doc.problem = (f"**疑似扫描件/图片型 PDF**：{doc.pages} 页只提出 "
                       f"{doc.cjk_count} 个中文（{cjk_per_page:.1f}/页，文字版通常 >500/页）。"
                       "→ **需 OCR**，本模块不做 OCR。"
                       "请勿把这种结果当成「原文没写」处理。")
        return doc

    if not doc.text.strip():
        doc.problem = "提取结果为空"
        return doc

    doc.ok = True
    return doc


def _table_cells(rows: list[list], page: int) -> list[Cell]:
    """表格 → Cell 列表。行标签取该行第一个非数字、非空单元格；列名取第 0 行。"""
    if not rows:
        return []
    header = [(_clean(c) if c else "") for c in rows[0]]
    out: list[Cell] = []
    for ri, r in enumerate(rows[1:], start=1):
        # 行标签：第一个非空、且不是纯数字的单元格
        row_label = ""
        for c in r:
            s = _clean(c)
            if s and not _NUM_FULL.match(s.replace(" ", "")):
                row_label = s
                break
        for ci, c in enumerate(r):
            s = _clean(c)
            if not s or not _NUM_FULL.match(s.replace(" ", "")):
                continue
            out.append(Cell(value=s, row_label=row_label,
                            col_header=(header[ci] if ci < len(header) else ""),
                            page=page, row_index=ri, col_index=ci))
    return out


def _clean(s) -> str:
    """单元格文本归一：换行/连续空白 → 单空格。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ══════════════════════════════════════════════════════════════════
# 查找
# ══════════════════════════════════════════════════════════════════
def norm_num(s: str) -> str:
    """数字归一：去千分位与空白。**不做近似** —— 改一位就是找不到。"""
    return re.sub(r"[,\s]", "", str(s or ""))


def canon(v) -> str:
    """数值 → 带千分位的规范字符串，**去掉尾随零**。

    ★★ 为什么必须做（实测踩到）：
      新浪接口给的是 `'46033330566.780000'`（字符串、无千分位、带尾随零），
      公告 PDF 里写的是 `46,033,330,566.78`。
      只去逗号不够 —— **尾随零不归一，同一个数会被判成「找不到」**，
      于是保真核验全线误报 FAIL。

    ⚠️ 只归一格式，**不做任何数值近似** —— 改一位仍然是找不到。

    ★★ 2026-10-13 实测又踩到一个更隐蔽的：**遇到千分位逗号时它静默不干活**。
       `_D("8,996,132,822.30")` 直接抛 InvalidOperation（逗号不是合法的 Decimal 字符），
       于是走到 except 分支**原样返回输入** —— 尾随零根本没被归一。

       后果（五粮液 2026 半年报实测）：
           原文单元格 `8,996,132,822.30` → canon 原样返回 → `8,996,132,822.30`
           接口值     `8,996,132,822.3`  → canon 原样返回 → `8,996,132,822.3`
           两者不等 → 判「原文里找不到」→ **把真数字冤枉成编的**
       （茅台那批之所以没暴露：它的数字没有尾随零，两边**恰好逐字相等**才蒙对。）

       → 修法：**先把千分位与空白去掉再解析**。
    """
    from decimal import Decimal as _D, InvalidOperation
    try:
        d = _D(re.sub(r"[,\s]", "", str(v))).normalize()    # ← 必须先去掉千分位
        # ★★ 非有限值（NaN / Infinity）**不是数**，不能拿去归一。
        #    实测踩到：`Decimal("nan").as_tuple()` 的 exponent 是**字符串 'n'**，
        #    下一行 `exp >= 0` 直接抛 `TypeError: '>=' not supported between
        #    instances of 'str' and 'int'` —— 而下面那个 except **只抓了
        #    InvalidOperation/ValueError，漏了 TypeError**，于是整个模块崩掉。
        #    ⚠️ 它是**潜伏**的：加了缓存之后新数据包进了语料（里面带 NaN）才引爆。
        if not d.is_finite():
            return str(v or "")
        sign, digits, exp = d.as_tuple()
        if exp >= 0:
            txt = str(d.quantize(_D(1)))
        else:
            txt = format(d, "f")
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return str(v or "")
    intpart, _, frac = txt.partition(".")
    neg = intpart.startswith("-")
    if neg:
        intpart = intpart[1:]
    intpart = "{:,}".format(int(intpart)) if intpart else "0"
    out = ("-" if neg else "") + intpart + (("." + frac) if frac else "")
    return out


def find_cells(doc: PdfDoc, value: str) -> list[Cell]:
    """按数值找单元格。**返回全部命中**（同一数字可能出现多次、口径不同）。

    ★★ 必须用 `canon` 比，**不能用 `norm_num`**（2026-10-13 实测踩到，是"误杀"）：

        原文里写的是 `8,996,132,822.30`（五粮液 2026 半年报，出现 5 次）
        新浪接口给的是 `8996132822.3`（少了尾随零）
        norm_num：`8996132822.3` ≠ `8996132822.30` → **判「原文里找不到」**
        canon  ：两个都归一成 `8,996,132,822.3` → 命中 ✓

      后果是**把真数字冤枉成编的** —— 比漏检更糟，因为我们会凭空指控原文没有的数据。
      `canon` 的 docstring 里本来就写着这个坑，只是这两个查找函数当时没走它。
    """
    want = canon(value)
    return [c for c in doc.cells if canon(c.value) == want]


def find_in_text(doc: PdfDoc, value: str) -> tuple[bool, int]:
    """在全文里找数字（表格没识别出来时的兜底）。返回 (找到没, 位置)。

    ★ 同 `find_cells`：走 canon，不走 norm_num（尾随零必须归一）。
    """
    want = canon(value)
    for m in _NUM_RE.finditer(doc.text):
        if canon(m.group(0)) == want:
            return True, m.start()
    return False, -1


# ══════════════════════════════════════════════════════════════════
# ★★ 表格单位 —— 实测踩到的"误杀"（2026-10-13）
# ══════════════════════════════════════════════════════════════════
# 银行/保险/部分制造业的财报表**不是以"元"列示**：
#     美的集团：行标签「归属于上市公司股东的净利润（千元）」  单元格 26,446,037
#     招商银行：正文「货币单位均以人民币百万元列示」          单元格 76,919
#     平安银行：正文 41 处「单位：人民币百万元」              单元格 25,696
# 接口返回的是**元**（26,446,037,000）。字符串全等比对 → **判"原文里找不到"**，
# 而数字完全正确。**这是凭空指控一份真实公告里没有这个数** —— 比漏检更伤信誉。
#
# → 修法：精确匹配失败时，**按文档自己标注的单位**再算一次。
#   ★ 绝不静默换算 —— 命中后必须在结论里写明用了哪个单位。
_UNIT_WORDS: tuple[tuple[str, int], ...] = (
    ("千元", 1000), ("百万元", 1000000), ("万元", 10000), ("亿元", 100000000),
)
# 文档级单位声明（正文里的写法）
_UNIT_DECL = re.compile(
    r"(?:单位|货币单位)[^。\n]{0,12}?(?:人民币)?\s*(千元|百万元|万元|亿元)")


def declared_units(doc: PdfDoc) -> dict[int, str]:
    """文档自己声明的货币单位 → {倍率: 单位词}。"""
    out: dict[int, str] = {}
    for m in _UNIT_DECL.finditer(doc.text or ""):
        w = m.group(1)
        for word, fac in _UNIT_WORDS:
            if word == w:
                out[fac] = word
    return out


def find_cells_with_unit(doc: PdfDoc, value: str) -> list[tuple[Cell, int, str]]:
    """精确匹配失败后的**带单位重试**。返回 [(单元格, 倍率, 单位词)]。

    ★ 只在**有单位线索**时才换算：
      ① 单元格自己的行标签写了单位（`…（千元）`）—— 最强；
      ② 文档正文声明了单位（`单位：人民币百万元`）—— 次之。
    两者都没有 → 返回空，**绝不瞎试倍率**（那会造出假命中）。
    """
    try:
        want = Decimal(re.sub(r"[,\s]", "", str(value)))
    except (InvalidOperation, ValueError):
        return []
    if want == 0:
        return []
    doc_units = declared_units(doc)
    out: list[tuple[Cell, int, str]] = []
    for c in doc.cells:
        lab = c.row_label or ""
        hit = None
        for word, fac in _UNIT_WORDS:            # ① 行标签自带单位
            if word in lab:
                hit = (fac, word)
                break
        if hit is None:                           # ② 退回文档级声明
            for fac, word in doc_units.items():
                hit = (fac, word)
                break
        if hit is None:
            continue
        fac, word = hit
        try:
            scaled = canon(str(want / Decimal(fac)))
        except (InvalidOperation, ZeroDivisionError):
            continue
        if canon(c.value) == scaled:
            out.append((c, fac, word))
    return out


# ══════════════════════════════════════════════════════════════════
# 口径标签
# ══════════════════════════════════════════════════════════════════
# 实测茅台 2026H1：两个"净利润"差 15 亿，写在同一张表里。
# ★ 顺序有讲究：**先长后短、先具体后笼统**。
#   「归属于母公司股东的净利润」必须比「净利润」先匹配到，
#   否则会把归属口径误判成合并口径 —— 那就是 15 亿的错。
CALIBER_PATTERNS: list[tuple[str, str]] = [
    # ★★ 银行/保险/部分集团**不写"上市公司"，写"本行/本公司/本集团"**（2026-10-13 补）。
    #    独立验证实测：平安银行原文行标签是「归属于本行股东的净利润」，
    #    而旧模式只认「归属于(上市公司|母公司)股东的净利润」→ **判成"净利润（合并）"**，
    #    于是系统输出的"口径：净利润"其实是归母数 —— 对银行股口径区分能力为零。
    #    招行/工行/农行/中国平安 全部命中这个形状。
    ("扣非归属净利润",
     r"扣除非经常性损益.{0,12}?(?:归属于|归母|股东).{0,10}?净利润"),
    # 另一种语序：「归属于上市公司股东的**扣除非经常性损益的**净利润」
    #   （上市公司年报里极常见，漏了它会被后面的「净利润」规则抢走）
    ("扣非归属净利润",
     r"归属于.{0,10}?股东的扣除非经常性损益.{0,8}?净利润"),
    # ⚠️ 兜底：**只要出现「扣除非经常性损益」就归到扣非**。
    #   收紧前面两条时差点把它漏掉（实测：`扣除非经常性损益的净利润` 会掉到
    #   「净利润」规则上 → 该断言当场挂了）。**顺序必须放在具体规则之后。**
    ("扣非归属净利润", r"扣除非经常性损益"),
    ("归属净利润",
     r"归属于.{0,8}?(?:上市公司|母公司|本行|本公司|本集团|普通股股东).{0,8}?股东"
     r"(?:权益持有者)?的净利润"),
    # 新浪/东财写法：归属于母公司所有者的净利润（"所有者"不是"股东"）
    ("归属净利润", r"归属于母公司所有者的净利润"),
    ("少数股东损益", r"少数股东损益"),
    ("净利润", r"(?<!归属)净利润"),
    ("利润总额", r"利润总额"),
    ("营业总收入", r"营业总收入"),
    ("营业收入", r"(?<!总)营业收入"),
    ("经营活动现金流净额", r"经营活动产生的现金流量净额"),
    ("所得税费用", r"所得税费用"),
    ("每股收益", r"每股收益"),
    ("净资产收益率", r"净资产收益率"),
]


def caliber_of_label(label: str) -> Optional[str]:
    """从标签判出口径名。判不出返回 **None**（不猜）。

    ★ 返回 None 而不是空串：None 是显式的「我不知道」，
      空串会被下游误当成一种口径名。
    ★ 只认**公告里真实出现的写法**，不认口语缩写：
      「归属净利润」判不出（原文写的是「归属于母公司股东的净利润」）——
      认外号等于允许模糊匹配，那就不叫核验了。
    """
    if not label:
        return None
    for name, pat in CALIBER_PATTERNS:
        if re.search(pat, label):
            return name
    return None


# ══════════════════════════════════════════════════════════════════
# ★★★ 列名校验：**行标签对了不代表数字对** ★★★
# ══════════════════════════════════════════════════════════════════
# 实测踩到（茅台半年报「主要会计数据」表）：
#     ['归属于上市公司股东的净利润', '44,516,880,421.86', '45,402,962,298.10', '-1.95']
#        ↑ 行标签（口径）              ↑ 本期金额           ↑ 上期金额        ↑ **增减%**
#
# 只看行标签，`-1.95` 会被判成「归属净利润」并 PASS —— **而它是增长率。**
# 46 亿和 -1.95 都能通过口径校验，这是会真出错的那种。
# → **必须同时看列名。**
_RATIO_COLS = ("增减", "同比", "增长", "变动", "占比", "收益率", "每股收益", "比例")


def is_ratio_column(col_header: str) -> bool:
    """这一列是不是比率/增减列（不是金额列）。

    ★★ **必须先去掉空白再匹配**（实测踩到）：
      PDF 里列名会换行，`_clean()` 把换行归一成空格，于是原始文本是
          「本报告期比 上年同期增 减(%)」
      而关键词是「增减」—— 直接 `in` 匹配会得到 **False**，
      结果**把增长率 -1.95 当成金额放行**。
      → 去空白后 `本报告期比上年同期增减(%)` 才匹配得上。

    ★ 判不出时返回 False，**宁可放过、不可误判** —— 把金额误判成比率比反过来危险。
    """
    if not col_header:
        return False
    flat = re.sub(r"\s+", "", col_header)
    return any(k in flat for k in _RATIO_COLS)


def verify_number(doc: PdfDoc, value: str, *, expect_caliber: Optional[str] = None,
                  expect_amount: Optional[bool] = None
                  ) -> tuple[str, str, list[Cell]]:
    """核验一个数字。返回 (verdict, 说明, 命中列表)。

    参数
    ----
    expect_caliber  : 期望口径名（如 "归属净利润"）。None = 不校验口径。
    expect_amount   : True = 必须是**金额列**（排除增减%）。None = 不校验。

    verdict ∈ {"PASS", "FAIL", "REFUSE"}
        PASS    数字在原文，且口径/列性质均符合
        FAIL    数字不在原文，或**口径不符**，或**列性质不符**
        REFUSE  口径判不出但调用方要求判 —— **不猜**
    """
    hits = find_cells(doc, value)
    unit_note = ""
    if not hits:
        # ★★ 精确找不到 → 按**文档自己标注的单位**再试（实测：银行/美的全是千元稿）
        scaled = find_cells_with_unit(doc, value)
        if scaled:
            hits = [c for c, _f, _w in scaled]
            fac, word = scaled[0][1], scaled[0][2]
            unit_note = (f"（**按文档标注的「{word}」换算后命中**：原文写的是 "
                         f"{hits[0].value}，×{fac:,} = {value}）")
    if not hits:
        found, pos = find_in_text(doc, value)
        if found:
            return ("FAIL",
                    f"数字 {value} 在全文里有，但**不在任何表格单元格里**"
                    "（表格没识别出来，无法确定口径）", [])
        # 单位未命中时，把文档声明的单位也报出来（帮用户判断是不是单位问题）
        du = declared_units(doc)
        extra = ("；该文档声明的单位是 " + "、".join(du.values())) if du else ""
        return "FAIL", f"数字 {value} 在原文里**找不到**（声称的原文里没有这个数）{extra}", []

    calibers = [caliber_of_label(h.row_label) for h in hits]

    # ── 列性质校验（先做 —— 它能把「增长率」和「金额」分开）──
    col_unknown = False
    if expect_amount is True:
        # ★★ 独立验证实测：列名**为空或是纯数字**时 `is_ratio_column` 返回 False
        #    → 等价于"不校验"，而输出照样报 PASS。
        #    实测比例：茅台 34.1%、五粮液 81.5% 的单元格落在"列名无效"区。
        #    → 列名判不出时**不许当成"是金额"**，只能标记为"列性质未知"。
        def _col_known(h) -> bool:
            ch = str(h.col_header or "").strip()
            return bool(ch) and not ch.replace(".", "").isdigit()
        amt = [h for h in hits if (not _col_known(h)) or (not is_ratio_column(h.col_header))]
        ratio_cols = [h for h in hits if _col_known(h) and is_ratio_column(h.col_header)]
        if not amt:
            return ("FAIL",
                    f"数字 {value} 只出现在**比率列**里（列名 "
                    f"{[h.col_header for h in hits[:3]]}）—— 它是增减/比率，不是金额", hits)
        # 如果命中的单元格**列名全都判不出**，那"列性质"这一关实际上没生效
        col_unknown = all(not _col_known(h) for h in hits)
        hits = amt
        calibers = [caliber_of_label(h.row_label) for h in hits]

    if expect_caliber is None:
        note2 = "｜⚠️ 列名判不出，**列性质这一关没生效**" if col_unknown else ""
        return "PASS", f"找到 {len(hits)} 处，口径：{calibers}{unit_note}{note2}", hits

    if expect_caliber in calibers:
        i = calibers.index(expect_caliber)
        # ★★ 独立验证实测：**同一个口径名会挂在多个不同数值上** ——
        #    合并利润表的「五、净利润」460.33 亿 与 母公司利润表的「四、净利润」170.62 亿
        #    都能判成"口径一致"。光看行标签分不出它们在**哪张表**里。
        #    → 不假装分得出：命中分散在**多个页码**时如实说明。
        pages = sorted({h.page for h in hits if getattr(h, "page", 0)})
        caveats = []
        if len(pages) > 1:
            caveats.append(f"该口径在**{len(pages)} 个不同页**上都出现（第 "
                           f"{'、'.join(str(p) for p in pages[:5])} 页）——"
                           "可能是合并报表/母公司报表/上期数，**本判定无法区分它们**")
        if col_unknown:
            caveats.append("列名判不出，**列性质这一关没生效**")
        tail = ("｜⚠️ " + "；".join(caveats)) if caveats else ""
        return ("PASS",
                f"口径一致（{expect_caliber}）｜行标签「{hits[i].row_label}」"
                f"｜列「{hits[i].col_header}」{unit_note}{tail}", hits)

    if all(c is None for c in calibers):
        return ("REFUSE",
                f"找到 {len(hits)} 处但**口径判不出**（行标签无法识别），"
                f"不猜；命中行标签：{[h.row_label for h in hits[:3]]}", hits)

    return ("FAIL",
            f"口径不符：期望「{expect_caliber}」，原文里是 {calibers}"
            f"；命中行标签 {[h.row_label for h in hits[:3]]}", hits)
