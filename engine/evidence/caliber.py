# -*- coding: utf-8 -*-
"""口径层 —— **没有口径的数字，不许进结论。**

═══════════════════════════════════════════════════════════════════
为什么必须有这一层：一个真实踩到的 15 亿陷阱
═══════════════════════════════════════════════════════════════════
茅台 2026 半年报，同一张表里：

    五、净利润                          46,033,330,566.78
    1.归属于母公司股东的净利润            44,516,880,421.86
    2.少数股东损益                       1,516,450,144.92

**两个都叫"净利润"，差 15.16 亿，而且两个都"对"。**

· 合并报表口径 = 46,033,330,566.78（含少数股东那部分）
· 归属母公司股东口径 = 44,516,880,421.86（股东实际享有的）
· 少数股东损益 = 两者的差

**中国媒体说「净利润」通常指后者**，而接口/数据库返回的常是**前者**。
**不标口径就报"茅台净利润 460 亿" = 误导用户。**

→ 本层的核心规则：
    ① 每个数字必须带口径
    ② **不同口径不许加总、不许比较、不许互相替代**
    ③ 报给用户时必须把口径说出来

═══════════════════════════════════════════════════════════════════
★★★ 口径的层级关系（不是平级的！）
═══════════════════════════════════════════════════════════════════
```
五、净利润  (net_profit)
   ├─ 1.归属于母公司股东的净利润  (net_profit_parent)
   │        └─ 扣除非经常性损益后     (net_profit_parent_deducted)
   └─ 2.少数股东损益              (minority_interest)
```
· `net_profit` = `net_profit_parent` + `minority_interest`  ← **可以加总验证**
· `net_profit_parent_deducted` ≤ `net_profit_parent`          ← **不可加总，是子集关系**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# 口径定义
# ══════════════════════════════════════════════════════════════════
# kind: aggregate=汇总口径 / component=构成项 / subset=子集 / ratio=比率
CALIBERS: dict[str, dict] = {
    "net_profit": {
        "name": "净利润（合并）",
        "alias": "净利润",
        "kind": "aggregate",
        "means": "合并报表净利润，**包含少数股东享有的部分**",
        "children": ["net_profit_parent", "minority_interest"],
    },
    "net_profit_parent": {
        "name": "归属于母公司股东的净利润",
        "alias": "归属净利润",
        "kind": "component",
        "means": "**股东实际享有的那部分**；中国媒体说「净利润」通常指这个",
        "parent": "net_profit",
    },
    "net_profit_parent_deducted": {
        "name": "归属于母公司股东的扣除非经常性损益的净利润",
        "alias": "扣非净利润",
        "kind": "subset",
        "means": "归属净利润里剔掉一次性损益；比归属净利润更小或更小得多",
        "subset_of": "net_profit_parent",
    },
    "minority_interest": {
        "name": "少数股东损益",
        "alias": "少数股东损益",
        "kind": "component",
        "means": "子公司里不属于母公司的股东那部分",
        "parent": "net_profit",
    },
    "total_profit": {
        "name": "利润总额",
        "alias": "利润总额",
        "kind": "aggregate",
        "means": "所得税前利润",
    },
    "operating_revenue_total": {
        "name": "营业总收入",
        "alias": "营业总收入",
        "kind": "aggregate",
        "means": "含利息/手续费等其他业务收入",
    },
    "operating_revenue": {
        "name": "营业收入",
        "alias": "营业收入",
        "kind": "component",
        "means": "主营业务收入；**营业总收入 ≥ 营业收入**",
    },
    "operating_cost": {
        "name": "营业成本",
        "alias": "营业成本",
        "kind": "component",
        "means": "与营业收入对应",
    },
    "income_tax": {
        "name": "所得税费用",
        "alias": "所得税费用",
        "kind": "component",
        "means": "利润总额 → 净利润 的减项",
    },
    "eps": {
        "name": "基本每股收益",
        "alias": "每股收益",
        "kind": "ratio",
        "means": "元/股；**不可与总额类相加**",
    },
    "roe": {
        "name": "加权平均净资产收益率",
        "alias": "净资产收益率",
        "kind": "ratio",
        "means": "百分比；**不可与金额类比较**",
    },
}

# 互斥/可加总规则
_ADDITIVE_TRIPLES = [
    # (合计, 构成项列表) —— 合计 = 各项之和
    ("net_profit", ["net_profit_parent", "minority_interest"]),
]


def alias_of(caliber: str) -> str:
    return CALIBERS.get(caliber, {}).get("alias", caliber)


def name_of(caliber: str) -> str:
    return CALIBERS.get(caliber, {}).get("name", caliber)


def is_ratio(caliber: str) -> bool:
    return CALIBERS.get(caliber, {}).get("kind") == "ratio"


# ══════════════════════════════════════════════════════════════════
# 带口径的数字
# ══════════════════════════════════════════════════════════════════
@dataclass
class Calibered:
    """一个**带口径**的数字。没有 caliber 的实例会被 render() 拒绝。"""
    value: D
    caliber: str = ""            # CALIBERS 的键
    unit: str = "元"
    source: str = ""
    as_of: str = ""
    verify: str = "unverified"   # verified | unverified | unverifiable | refused
    raw_text: str = ""           # 原文里的写法（可回原文核对）

    def __post_init__(self) -> None:
        if isinstance(self.value, str):
            self.value = D(self.value)

    # ------------------------------------------------------------------
    def render(self, *, plain: bool = True) -> str:
        """给**用户**看的说法。**没有口径就直接抛，不给含糊的说法。**

        ★ 这是本层的立身之本：宁可报错，也不输出一个不带口径的金额。
        """
        if not self.caliber:
            raise ValueError(
                f"这个数字没有口径，不许报给用户：{self.value}（来源 {self.source}）"
            )
        meta = CALIBERS.get(self.caliber)
        if meta is None:
            raise ValueError(f"未知口径：{self.caliber}")
        # 千分位
        s = "{:,}".format(self.value) if self.value == self.value.to_integral_value() \
            else format(self.value.normalize(), "f")
        if not plain:
            return f"{s} {self.unit}（{meta['name']}｜{self.source}｜{self.as_of}）"
        return f"{s} {self.unit}（{meta['name']}）"

    def yin(self, wan: bool = True) -> str:
        """以「亿元」为单位的口语化说法（媒体常用）。"""
        if not self.caliber:
            raise ValueError("没有口径的数字不许报给用户")
        if self.unit != "元":
            return self.render()
        yi = self.value / D("1e8")
        return f"{yi:.2f} 亿元（{name_of(self.caliber)}）"


# ══════════════════════════════════════════════════════════════════
# 兼容性判定 —— 这是本层真正拦错的地方
# ══════════════════════════════════════════════════════════════════
@dataclass
class Conflict:
    ok: bool
    reason: str = ""
    hint: str = ""

    def __bool__(self) -> bool:
        return self.ok


def can_compare(a: Calibered, b: Calibered) -> Conflict:
    """两个数字能不能**直接比较大小 / 求差**。

    典型会出错的情形：拿「归属净利润」和「扣非净利润」比增长率 ——
    两者差着一次性损益，比出来的"增长"是假的。
    """
    if not a.caliber or not b.caliber:
        return Conflict(False, "有一方没有口径", "先给它标口径，否则无法比较")
    if is_ratio(a.caliber) != is_ratio(b.caliber):
        return Conflict(False,
                        f"类型不同：{name_of(a.caliber)} 是"
                        f"{'比率' if is_ratio(a.caliber) else '金额'}，"
                        f"{name_of(b.caliber)} 是"
                        f"{'比率' if is_ratio(b.caliber) else '金额'}",
                        "金额不能和比率比较")
    if a.unit != b.unit:
        return Conflict(False, f"单位不同：{a.unit} vs {b.unit}", "先统一单位")
    if a.caliber == b.caliber:
        return Conflict(True)
    # 父子/子集关系：可比较但要说明
    ka, kb = a.caliber, b.caliber
    if CALIBERS[ka].get("parent") == kb or CALIBERS[kb].get("parent") == ka:
        return Conflict(False, f"口径不同：{name_of(ka)} vs {name_of(kb)}",
                        "是**父子口径**（一个含另一个），求差有意义但要说清；"
                        "**不能当成同一个数互相替代**")
    if CALIBERS[ka].get("subset_of") == kb or CALIBERS[kb].get("subset_of") == ka:
        return Conflict(False, f"口径不同：{name_of(ka)} vs {name_of(kb)}",
                        "是**子集关系**（扣非是归属的一部分），"
                        "**不能比增长率** —— 差的是一次性损益")
    return Conflict(False, f"口径不同：{name_of(ka)} vs {name_of(kb)}",
                    "不同口径的数字**不能直接比较**")


def can_sum(items: list[Calibered]) -> Conflict:
    """一组数字能不能**加总**。只有构成项能加总成合计。"""
    if not items:
        return Conflict(False, "空集合")
    if any(not x.caliber for x in items):
        return Conflict(False, "有项目没有口径", "先补口径")
    if len({x.unit for x in items}) > 1:
        return Conflict(False, "单位不一致", "先统一单位")
    if len({x.as_of for x in items}) > 1:
        return Conflict(False, f"报告期不一致：{sorted({x.as_of for x in items})}",
                        "不同报告期的数不能相加")
    cals = [x.caliber for x in items]
    for total, parts in _ADDITIVE_TRIPLES:
        if sorted(cals) == sorted(parts):
            return Conflict(True, f"可加总为 {name_of(total)}")
    # 构成项 + 子集混在一起 → 明确拒绝
    if any(CALIBERS.get(c, {}).get("kind") == "subset" for c in cals):
        return Conflict(False, "含子集口径（如扣非）",
                        "子集不能和父集相加 —— 会重复计算")
    return Conflict(False, f"口径 {[name_of(c) for c in cals]} 之间没有定义好的加总关系",
                    "只有「构成项→合计」能加；其余要显式定义")


def verify_additive(total: Calibered, parts: list[Calibered],
                    *, tolerance: D = D("0")) -> Conflict:
    """验证「合计 = 各项之和」。**这是最硬的核验** —— 三个数都在原文里，还要能加起来。"""
    c = can_sum(parts)
    if not c.ok:
        return Conflict(False, c.reason, c.hint)
    if total.caliber not in [t for t, _ in _ADDITIVE_TRIPLES]:
        return Conflict(False, f"{name_of(total.caliber)} 不是已定义的合计口径", "")
    s = sum((p.value for p in parts), D(0))
    diff = abs(total.value - s)
    if diff <= tolerance:
        return Conflict(True, f"闭合：{s} = {total.value}")
    return Conflict(False,
                    f"**不闭合**：各项之和 {s}，声称的合计 {total.value}，差 {diff}",
                    "注意「净利润 = 归属净利润 + 少数股东损益」的差额里可能还有"
                    "「其他权益工具持有者的分配」等项 —— 差额不该被当成错误，"
                    "但必须解释清楚")


# ══════════════════════════════════════════════════════════════════
# 从公告文本抽口径（与 pdf_source 配合）
# ══════════════════════════════════════════════════════════════════
# ★ 只认**公告原文里的真实写法**，不认口语缩写。
#   「归属净利润」在原文里不存在 → 判不出是正确行为（认外号＝允许模糊匹配）。
CALIBER_PATTERNS: list[tuple[str, str]] = [
    ("net_profit_parent_deducted", r"扣除非经常性损益.{0,12}净利润"),
    ("net_profit_parent", r"归属于(上市公司|母公司)股东的净利润"),
    ("minority_interest", r"少数股东损益"),
    ("net_profit", r"(?<!归属)(?<!股东的)净利润"),
    ("total_profit", r"利润总额"),
    ("operating_revenue_total", r"营业总收入"),
    ("operating_revenue", r"(?<!总)营业收入"),
    ("operating_cost", r"营业成本"),
    ("income_tax", r"所得税费用"),
    ("eps", r"每股收益"),
    ("roe", r"净资产收益率"),
]


def caliber_of_label(label: str) -> Optional[str]:
    """从原文行标签判口径。判不出返回 **None**（不猜）。

    ★ 顺序有讲究：先长后短、先具体后笼统。
      「归属于母公司股东的净利润」必须比「净利润」先匹配 ——
      否则会把归属口径误判成合并口径，**那就是 15 亿的错**。
    """
    import re
    if not label:
        return None
    for key, pat in CALIBER_PATTERNS:
        if re.search(pat, label):
            return key
    return None
