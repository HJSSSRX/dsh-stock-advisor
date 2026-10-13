# -*- coding: utf-8 -*-
"""完整性守卫 —— **让「取不到」变成系统的正常状态，而不是意外。**

═══════════════════════════════════════════════════════════════════
为什么要有这一层
═══════════════════════════════════════════════════════════════════
用户的担心（原话）：
    「最基础的数据都没有办法保证完整，稳定，
      这很难让人相信可以真的有好结果，后面的判断都没有意义」

这是对的 —— **如果一个系统不知道自己的数据是不是完整的，
它给出的任何结论都是不可信的。**

但"源不稳定"是无法解决的（实测三家独立项目在同一接口上全挂）。
能解决的是：**让系统清楚地知道自己现在缺什么，并且如实说出来。**

═══════════════════════════════════════════════════════════════════
★ 设计原则
═══════════════════════════════════════════════════════════════════
① **分关键级** —— 不是所有数据都同等重要：
     必需：价格 / K线 / 指数        （没有它整个分析做不了）
     重要：财务 / 公告 / 估值        （缺了结论会明显变弱）
     可选：资金流 / 舆情 / 行业      （缺了只是少一个角度）

② **守的是"能不能给客户一个诚实的回答"，不是"接口通不通"**
   某个源挂了但缓存里有 → **对客户而言是完整的**（只是数据旧一点）
   所以健康判据是「**拿到的东西够不够用**」，不是「接口返回 200 没有」

③ **守卫结果要能传给判断层** —— 判断层拿它决定"这次能答什么、不能答什么"
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import cache as C                      # noqa: E402
from data.registry import fetch                  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# 关键级
# ══════════════════════════════════════════════════════════════════
REQUIRED = "必需"      # 缺了整个分析做不了
IMPORTANT = "重要"     # 缺了结论明显变弱
OPTIONAL = "可选"      # 缺了只是少一个角度

# 探测计划：(kind, 关键级, 参数, 说明)
PROBES: list[tuple[str, str, dict, str]] = [
    # ── 必需：没有这些，连"现在多少钱"都答不了 ──
    ("quote",      REQUIRED,  {},                          "实时价格"),
    ("kline",      REQUIRED,  {"limit": 60},               "日线（算风险/位置）"),
    ("index_kline", REQUIRED, {"limit": 60},               "指数（算相对强弱）"),
    ("universe",   IMPORTANT, {},                          "全市场列表（算市场宽度）"),

    # ── 重要：缺了结论明显变弱 ──
    ("financial_sina", IMPORTANT, {"report_type": "lrb", "num": 4}, "财务三表"),
    ("announcement",   IMPORTANT, {"page_size": 5},        "公告原文"),
    ("valuation_now",  IMPORTANT, {},                      "估值快照"),
    ("st_list",        IMPORTANT, {},                      "风险警示名单"),
    ("restricted_release", IMPORTANT, {},                  "解禁"),
    ("equity_pledge",  IMPORTANT, {},                      "股权质押"),

    # ── 可选：少一个角度而已 ──
    ("board",          OPTIONAL,  {},                      "行业板块"),
    ("concept_blocks", OPTIONAL,  {},                      "概念板块"),
    ("industry_cninfo", OPTIONAL, {},                      "巨潮行业分类"),
    ("news_flash",     OPTIONAL,  {"page_size": 5},        "财联社快讯"),
    ("dragon_tiger",   OPTIONAL,  {},                      "龙虎榜"),
    ("margin_trading", OPTIONAL,  {},                      "两融"),
    ("interactive_qa", OPTIONAL,  {"page_size": 3},        "投资者互动"),
]

STATE_PATH = os.path.join(C.CACHE_DIR, "_health.json")


@dataclass
class HealthItem:
    kind: str
    level: str
    note: str
    ok: bool = False
    stale: bool = False
    age_hours: float = 0.0
    source: str = ""
    rows: int = 0
    error: str = ""

    @property
    def usable(self) -> bool:
        """★ **对客户而言可用吗** —— 这是健康判据，不是"接口通不通"。

        取不到但缓存里有旧数据 → **可用**（只是要标注时效）。
        """
        return self.ok and self.rows > 0

    def describe(self) -> str:
        if not self.usable:
            return f"❌ 取不到（{self.error[:40]}）"
        if self.stale:
            return f"⚠️ 可用但过期 {self.age_hours:.1f} 小时"
        return f"✅ 正常（{self.rows} 行）"


@dataclass
class HealthReport:
    ts: float = 0.0
    items: list[HealthItem] = field(default_factory=list)
    elapsed: float = 0.0

    # ------------------------------------------------------------------
    def by_level(self, level: str) -> list[HealthItem]:
        return [x for x in self.items if x.level == level]

    @property
    def missing_required(self) -> list[HealthItem]:
        return [x for x in self.by_level(REQUIRED) if not x.usable]

    @property
    def missing_important(self) -> list[HealthItem]:
        return [x for x in self.by_level(IMPORTANT) if not x.usable]

    @property
    def stale_items(self) -> list[HealthItem]:
        return [x for x in self.items if x.usable and x.stale]

    @property
    def can_analyze(self) -> bool:
        """★ 能不能做一次完整分析 —— **必需项全在就行**。"""
        return not self.missing_required

    # ------------------------------------------------------------------
    def guard_note(self) -> str:
        """★ **给判断层的硬约束** —— 它必须照这个说，不许忽略。

        这是整个守卫存在的意义：把"我不知道什么"变成
        一句判断层不能跳过的指令。
        """
        lines: list[str] = []
        if self.missing_required:
            names = "、".join(f"{x.note}({x.kind})" for x in self.missing_required)
            lines.append(
                f"🚫 **【必需数据缺失】{names}** —— "
                "**本次不能给出完整结论**。只能就手上有的部分回答，"
                "并且**开篇就要说清缺了什么**，不许用旧数据或推测填空。"
            )
        if self.missing_important:
            names = "、".join(f"{x.note}({x.kind})" for x in self.missing_important)
            lines.append(
                f"⚠️ **【重要数据缺失】{names}** —— 结论会明显变弱，"
                "**必须说明这部分没有覆盖**。"
            )
        if self.stale_items:
            names = "、".join(f"{x.note}({x.age_hours:.1f}小时前)"
                              for x in self.stale_items)
            lines.append(
                f"⏰ **【数据已过期】{names}** —— "
                "**凡是用到这些数据的结论，都必须写明数据时点**，不许当成实时。"
            )
        if not lines:
            lines.append("✅ 关键数据齐全且新鲜，可以正常分析。")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "生成时间": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.ts)),
            "耗时_秒": round(self.elapsed, 1),
            "能不能做完整分析": self.can_analyze,
            "缺口": {
                "必需": [x.kind for x in self.missing_required],
                "重要": [x.kind for x in self.missing_important],
                "过期": [f"{x.kind}({x.age_hours:.1f}h)" for x in self.stale_items],
            },
            "逐项": [
                {"kind": x.kind, "关键级": x.level, "说明": x.note,
                 "可用": x.usable, "过期": x.stale, "时效_小时": round(x.age_hours, 2),
                 "来源": x.source, "行数": x.rows, "错误": x.error[:60]}
                for x in self.items
            ],
            "给判断层的约束": self.guard_note(),
        }


# ══════════════════════════════════════════════════════════════════
def check(full: bool = False) -> HealthReport:
    """跑一遍守卫。

    full=False（默认）：**优先走缓存**（快，不打扰数据源）
    full=True：**全部真取**（慢，但能测出源的真实现状）
    """
    t0 = time.time()
    rep = HealthReport(ts=time.time())
    for kind, level, params, note in PROBES:
        it = HealthItem(kind=kind, level=level, note=note)
        try:
            r = fetch(kind, "" if kind in ("board", "concept_blocks", "universe",
                                           "st_list", "industry_cninfo") else "600519",
                      dict(params), use_cache=not full)
            it.ok = bool(r.ok)
            it.stale = bool(getattr(r, "stale", False))
            it.age_hours = float(getattr(r, "age_seconds", 0.0)) / 3600.0
            it.source = r.source or ""
            it.rows = len(r.data) if isinstance(r.data, list) else (1 if r.data else 0)
            it.error = r.error or ""
        except Exception as exc:                    # noqa: BLE001
            it.error = f"{type(exc).__name__}: {exc}"[:80]
        rep.items.append(it)
    rep.elapsed = time.time() - t0
    return rep


def save(rep: HealthReport) -> str:
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep.to_dict(), f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)
    return STATE_PATH


def load() -> Optional[dict]:
    """读上次的守卫结果（判断层用它决定这次能答什么）。"""
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                               # noqa: BLE001
        return None


def ensure(max_age_minutes: float = 30) -> dict:
    """★ **判断层的入口**：拿到可用的守卫结果（过期就重跑）。"""
    blob = load()
    if blob:
        try:
            t = time.mktime(time.strptime(blob["生成时间"], "%Y-%m-%d %H:%M:%S"))
            if (time.time() - t) / 60 <= max_age_minutes:
                return blob
        except Exception:                           # noqa: BLE001
            pass
    rep = check()
    save(rep)
    return rep.to_dict()


# ══════════════════════════════════════════════════════════════════
def _main(argv: list[str]) -> int:
    full = "--full" in argv
    verb = "--json" in argv
    rep = check(full=full)
    save(rep)
    if verb:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=1))
        return 0 if rep.can_analyze else 2

    print("═" * 74)
    print(f"数据完整性守卫（{'全量真取' if full else '优先走缓存'}，耗时 {rep.elapsed:.1f}s）")
    print("═" * 74)
    for level in (REQUIRED, IMPORTANT, OPTIONAL):
        items = rep.by_level(level)
        if not items:
            continue
        ok_n = sum(1 for x in items if x.usable)
        print(f"\n── {level}（{ok_n}/{len(items)} 可用）──")
        for x in items:
            print(f"  {x.describe():<34} {x.note:<22} {x.kind}")
    print()
    print("═" * 74)
    print("给判断层的约束：")
    print("═" * 74)
    for line in rep.guard_note().split("\n"):
        print(f"  {line}")
    print()
    print(f"  能不能做完整分析：{'✅ 能' if rep.can_analyze else '🚫 不能（必需数据缺失）'}")
    print(f"  状态已存：{STATE_PATH}")
    return 0 if rep.can_analyze else 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
