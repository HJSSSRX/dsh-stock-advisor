# -*- coding: utf-8 -*-
"""个性化层 —— **它只能决定"先说哪条"，物理上够不到"这条是什么"**。

═══════════════════════════════════════════════════════════════════════════
那堵墙是这一层的全部设计
═══════════════════════════════════════════════════════════════════════════
用户有偏好、有策略、有情绪。**偏好很容易滑向谄媚** ——
「他喜欢看多，那就多给点看多依据」——这在投资场景下会害死人。

所以墙必须砌成**结构性的**，而不是靠约定：

    ① `PresentationPlan` 这个类**里根本没有装数值的地方** ——
       它只有 `section_order` / `metric_priority` / `term_glossary` / `verbosity`
       四个字段。**想用它改数字，无处可放。**

    ② 引擎的事实层（`compute/` `judge/` `evidence/` `data/`）**一律不许 import 本模块** ——
       这条由 `selftest_advisor.py` 里的机械断言守着，不是靠人自觉。

    ③ 本模块**不产出任何数值**，只产出"顺序"和"说法"。

★ 一句话：**偏好只能改"先说哪条"，不能改"这条是什么"。**
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

# 用户数据放哪 —— 和引擎代码分开，便于备份/迁移/删除
ROOT = os.environ.get("STOCK_ADVISOR_HOME") or os.path.join(
    os.path.expanduser("~"), ".dsh", "stock-advisor")
USERS_DIR = os.path.join(ROOT, "users")

# ── 白名单：profile 里**只允许**出现这些键 ────────────────────────
#   ★ 这是墙的第二道：不在白名单里的键一律拒绝。
#     于是「source_preference: 只看腾讯」「hide_risks: true」这类**想改事实的配置**
#     根本存不进来。
ALLOWED_TOP = {
    "user_id", "display_name", "watchlist", "strategy", "risk_tolerance",
    "knowledge", "section_order", "metric_priority", "term_glossary",
    "verbosity", "created_at", "updated_at", "notes",
}
ALLOWED_STRATEGY = {"波段", "基本面", "事件催化", "估值", "指数", "未指定"}
ALLOWED_RISK = {"低", "中", "高", "未指定"}
ALLOWED_VERBOSITY = {"简洁", "标准", "详细"}
#   ★ 这几类词出现在键名里 = 有人想用偏好去动事实 → 直接拒绝并说明
FORBIDDEN_IN_KEYS = re.compile(
    r"(source|prefer|only|hide|filter|bias|bull|bear|direction|"
    r"hide_|suppress|min_|max_|threshold|weight)", re.I)


class ProfileError(ValueError):
    """profile 不合法 —— **宁可拒绝加载，也不许带着一个想改事实的配置跑**。"""


@dataclass(frozen=True)
class PresentationPlan:
    """★★ 呈现计划。

    **这个类里没有任何一个字段能装数值。** 这是墙的本体。
    它能做的只有两件事：决定**先后**，决定**用词**。
    """

    section_order: tuple = ()          # 章节先后的偏好
    metric_priority: tuple = ()        # 同样呈现时，哪些指标排前面
    term_glossary: dict = field(default_factory=dict)  # 术语 → 更通俗的说法
    verbosity: str = "标准"

    def reorder(self, items: list, key=lambda x: x) -> list:
        """按偏好排序。★ **只改顺序，不改内容，不改数量，不改任何值。**

        未在 priority 里出现的排在后面，保持原有相对次序（稳定排序）。
        """
        if not self.metric_priority:
            return list(items)
        pref = {name: i for i, name in enumerate(self.metric_priority)}
        return sorted(items, key=lambda it: (pref.get(key(it), len(pref)), 0))

    def explain(self, term: str, default: str = "") -> str:
        """术语换通俗说法。★ **只是换词，不是换数。**"""
        return self.term_glossary.get(term, default or term)


# 引擎的章节名（供 section_order 参考；不由本模块决定它们的内容）
KNOWN_SECTIONS = ("必须说明", "事实", "核对", "风险", "缺口", "最坏情况", "结论")


@dataclass
class UserProfile:
    user_id: str
    display_name: str = ""
    watchlist: list = field(default_factory=list)
    strategy: str = "未指定"
    risk_tolerance: str = "未指定"
    knowledge: dict = field(default_factory=dict)   # 术语 → 他懂不懂
    section_order: tuple = ()
    metric_priority: tuple = ()
    term_glossary: dict = field(default_factory=dict)
    verbosity: str = "标准"
    notes: str = ""

    # ── 序列化 ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        d["section_order"] = list(self.section_order)
        d["metric_priority"] = list(self.metric_priority)
        return d

    def plan(self) -> PresentationPlan:
        """★ 本模块对外的**唯一出口** —— 一个装不下事实的呈现计划。"""
        return PresentationPlan(section_order=tuple(self.section_order),
                                metric_priority=tuple(self.metric_priority),
                                term_glossary=dict(self.term_glossary),
                                verbosity=self.verbosity)

    def render(self) -> str:
        L = [f"用户：{self.display_name or self.user_id}"]
        if self.watchlist:
            L.append(f"  关注：{'、'.join(self.watchlist)}")
        L.append(f"  策略框架：{self.strategy}｜风险偏好：{self.risk_tolerance}｜详略：{self.verbosity}")
        if self.metric_priority:
            L.append(f"  呈现顺序偏好：{' → '.join(self.metric_priority)}")
        if self.knowledge:
            known = [k for k, v in self.knowledge.items() if v]
            unknown = [k for k, v in self.knowledge.items() if not v]
            if known:
                L.append(f"  他懂的术语：{'、'.join(known)}")
            if unknown:
                L.append(f"  他不懂的术语（要解释）：{'、'.join(unknown)}")
        L.append("  ★ 以上**只影响呈现顺序与用词，不影响任何数值**")
        return "\n".join(L)


# ══════════════════════════════════════════════════════════════════
def validate(raw: dict) -> dict:
    """★ 校验一份 profile。**不合规就抛错，不许带着它跑。**

    拒绝的两种情形：
      ① 出现白名单外的键
      ② 键名里带 `source/prefer/hide/filter/...` 这类**想动事实**的词
    """
    if not isinstance(raw, dict):
        raise ProfileError("profile 必须是一个对象")
    bad = sorted(set(raw) - ALLOWED_TOP)
    if bad:
        raise ProfileError(f"profile 里有不认识的键：{bad}。"
                           f"允许的键只有：{sorted(ALLOWED_TOP)}")
    # ② 想动事实的键名 —— 逐层扫（包括嵌套的）
    def _scan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if FORBIDDEN_IN_KEYS.search(str(k)):
                    raise ProfileError(
                        f"profile 里出现疑似「想改变事实」的键：{path}{k}。\n"
                        f"★ 个性化层只允许影响**呈现顺序与用词**，"
                        f"不允许过滤来源、隐藏风险、或左右方向。")
                _scan(v, f"{path}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan(v, f"{path}[{i}].")
    _scan(raw)
    if raw.get("strategy") and raw["strategy"] not in ALLOWED_STRATEGY:
        raise ProfileError(f"strategy 只能是 {sorted(ALLOWED_STRATEGY)}，"
                           f"收到 {raw['strategy']!r}")
    if raw.get("risk_tolerance") and raw["risk_tolerance"] not in ALLOWED_RISK:
        raise ProfileError(f"risk_tolerance 只能是 {sorted(ALLOWED_RISK)}")
    if raw.get("verbosity") and raw["verbosity"] not in ALLOWED_VERBOSITY:
        raise ProfileError(f"verbosity 只能是 {sorted(ALLOWED_VERBOSITY)}")
    return raw


def path_of(user_id: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", user_id)
    return os.path.join(USERS_DIR, safe, "profile.json")


def load(user_id: str = "default") -> UserProfile:
    """读画像。**不存在就返回一个空白画像**（新用户不该被拦）。"""
    p = path_of(user_id)
    if not os.path.exists(p):
        return UserProfile(user_id=user_id)
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as e:                                   # noqa: BLE001
        raise ProfileError(f"画像文件读不了：{p} → {type(e).__name__}: {e}")
    validate(raw)
    return UserProfile(
        user_id=raw.get("user_id", user_id),
        display_name=raw.get("display_name", ""),
        watchlist=list(raw.get("watchlist", [])),
        strategy=raw.get("strategy", "未指定"),
        risk_tolerance=raw.get("risk_tolerance", "未指定"),
        knowledge=dict(raw.get("knowledge", {})),
        section_order=tuple(raw.get("section_order", ())),
        metric_priority=tuple(raw.get("metric_priority", ())),
        term_glossary=dict(raw.get("term_glossary", {})),
        verbosity=raw.get("verbosity", "标准"),
        notes=raw.get("notes", ""),
    )


def save(prof: UserProfile) -> str:
    """存画像。**存之前必须过同一套校验。**"""
    raw = prof.to_dict()
    validate(raw)
    p = path_of(prof.user_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)          # ★ 原子替换，不写坏已有画像
    return p
