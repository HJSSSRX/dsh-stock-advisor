# -*- coding: utf-8 -*-
"""数字溯源防线 —— **回答里出现的每一个数字，必须能在工具输出里找到**。

═══════════════════════════════════════════════════════════════════════════
为什么需要这一层
═══════════════════════════════════════════════════════════════════════════
引擎（确定性程序）输出的每个数字都可核验。但从**引擎输出**到**说给人听的话**
之间，要经过一层模型转述。**那一层是没有护栏的** ——
模型可能在转述时顺手写一个"大概的数"、把两个数字心算相加、或者凭记忆补一个。

`judge/contract.py` 查的是「该说的话有没有说」，
**它查不了「有没有偷偷加一个数」。**

本模块就是那道护栏：把回答里的数字全抓出来，逐个回引擎输出里找。
**找不到的，就是不许发出去的。**

═══════════════════════════════════════════════════════════════════════════
难点：不是所有"数字"都是事实
═══════════════════════════════════════════════════════════════════════════
回答里会合法地出现大量**不需要溯源**的数字：

    · 6 位股票代码         000504、600519
    · 12 位十六进制证据号   6080e6150d4d、95ee3557defb0eb4
    · 日期                2026-10-13、2025-12-31、2026年半年度
    · 页码                第 115 页
    · 条款号              第十条、1.3 条
    · 版本号 / 序号         v2、第 3 项

**把这些当"未溯源数字"报出来，防线就会被噪音淹没，等于没有。**
所以先分类，只查**真·事实数字**。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

# ── 中文数量单位 ──────────────────────────────────────────────
UNITS = {"亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2}

# ── ★★ 先「屏蔽」不是数字的整块，再抽数字 ────────────────────
#   为什么要先屏蔽：`2026-08-24` 会被数字正则拆成 2026 / -08 / -24，
#   `27d00686c409` 会被拆成 27 / 00686 / 409。
#   **拆碎之后就无法识别了**，所以必须在抽取之前整块抹掉。
MASK_PATTERNS: tuple[str, ...] = (
    r"\d{4}-\d{2}-\d{2}",                    # 2026-10-13
    r"\d{4}年\d{1,2}月(?:\d{1,2}日)?",        # 2026年8月24日 / 2026年8月
    r"\d{4}\s*年(?:半年度|年度|年报|中报|一季报|三季报|第[一二三四]季度)",
    r"\b[0-9a-fA-F]{8,}\b",                  # 证据号 27d00686c409
    r"\b\d{6}\b",                            # 6 位股票代码
    r"\b\d{4}\b(?=\s*年)",                    # 2026 年（前面没月日）
)

# 数字本体（含千分位与小数）：1,288.00 ｜ 35.68 ｜ -2.98e-08 ｜ 4.76
_NUM = r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?"


@dataclass(frozen=True)
class NumRef:
    raw: str                     # 原文写法
    value: float                 # 归一化后的数值
    kind: str                    # fact / page / clause / index
    pos: int                     # 在文本中的位置（用于人工定位）
    tol: float = 0.0             # ★ 该数字「写到哪一位」所允许的误差
    context: str = ""            # 前后文（便于人看这是哪个数）

    def __str__(self) -> str:
        return f"{self.raw}（{self.kind}）@{self.pos}"


def _mask(text: str) -> str:
    """把日期/代码/证据号整块换成等长的空白（**保持位置不变**，便于报 pos）。"""
    out = text
    for pat in MASK_PATTERNS:
        out = re.sub(pat, lambda m: " " * len(m.group(0)), out)
    return out


def _looks_like_identifier(raw: str, before: str, after: str) -> Optional[str]:
    """判断这个数字是不是「标识符」而不是「事实值」。返回 kind 或 None。"""
    # 页码：前面紧跟「第」，后面紧跟「页」
    if re.search(r"第\s*$", before) and re.match(r"^\s*页", after):
        return "page"
    # 条款号：第十条 / 第二十四条
    if re.match(r"^\s*条", after):
        return "clause"
    # 序号：「第 3 项 / 第 2 名 / 第 1 部分 / 第 3 章」
    if re.search(r"第\s*$", before) and re.match(r"^\s*(项|个|名|位|部分|章|节|季度|次|轮|条)", after):
        return "index"
    # 「共 N 部分 / 共 N 项」→ 序号
    if re.search(r"共\s*$", before) and re.match(r"^\s*(部分|项|个|条|章|节|名|位|次)", after):
        return "index"
    # 行首编号「1. 」「2、」
    if re.match(r"^\s*[.、)]", after) and re.search(r"(^|\n)\s*$", before):
        return "index"
    return None


def scan(text: str) -> list:
    """把文本里的**事实数字**全抓出来（已剔除日期/代码/证据号/页码/条款号）。"""
    masked = _mask(text)
    out: list = []
    for m in re.finditer(_NUM, masked):
        raw = m.group(0)
        if not raw or raw in ("-", ""):
            continue
        # ★ 用「已屏蔽」的文本判断上下文，用原文取 raw
        before = masked[max(0, m.start() - 6):m.start()]
        after = masked[m.end():m.end() + 8]
        if _looks_like_identifier(raw, before, after):
            continue
        # ★★ 单位与百分号：**必须跳过空格** ——
        #   源里写的是「-4.76 %」和「599.27 万元」（数字后有空格），
        #   原来直接看紧跟的两个字符，于是全认不出单位，
        #   导致「4.76%」与「-4.76」对不上、「599.27 万元」与「5,992,694.22」对不上
        #   —— 这是误杀的最大来源。
        tail = masked[m.end():m.end() + 12].lstrip()
        unit_mult, is_pct = 1.0, False
        # ★★★ 单位识别必须防「半个词」—— 实测踩到两次，都是同一个坑：
        #
        #   ① `%`：先写成 `tail.startswith("%")`，但源里写的是「-4.76 %」（有空格），
        #      于是没认出百分号，把 4.76 当成 4.76 元 → 与 0.0476 对不上 → **误杀**。
        #   ② 「百」：`UNITS` 里有 `"百": 1e2`，而源里写「0.8374 **百分位**」——
        #      **「百分位」是 percentile 的意思，不是「一百」** ——
        #      于是 0.8374 被当成 83.74，**差 100 倍**，闸门当场误报「找不到出处」。
        #
        #   修法：单位是「量词」，后面不该紧跟数量词的续接字。
        #   「百」后面跟「分」= 百分位/百分点/百分比 → 不是单位。
        _UNIT_CONT = {"百": "分", "千": "分", "万": "分"}   # 万分之几 / 千分位…
        for u, mult in UNITS.items():
            if not tail.startswith(u):
                continue
            nxt = tail[len(u):len(u) + 1]
            if _UNIT_CONT.get(u) and nxt == _UNIT_CONT[u]:
                continue                  # 「百分位」这种 → 跳过，不是单位
            unit_mult = mult
            break
        if tail.startswith("%") or tail.startswith("百分点"):
            is_pct = True
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        # ★★ 容差跟「回答写到第几位小数」走 —— 这是本层最关键的一个设计。
        #
        #   源里是 295,914,290.97，回答写「2.96 亿元」。
        #   2.95914 亿写成 2.96 亿是**合法的四舍五入**，不是编造。
        #   但如果按相对容差（0.01%）算，2.96 与 2.95914 差 0.029%，就被冤枉了。
        #
        #   正确语义：回答写「2.96」，就是在说「这个值在 [2.955, 2.965] 之间」。
        #   → 容差 = 该写法最后一位的半个单位。
        #   → 反过来「10.81 改成 10.82」：写 2 位小数 → 容差 0.005，
        #     而差值 0.01 > 0.005 → **照样抓到**。松紧自动跟着精度走。
        digits = raw.replace(",", "")
        dec = len(digits.split(".")[1]) if "." in digits else 0
        half_ulp = 0.5 * (10.0 ** -dec)
        scale = 0.01 if is_pct else unit_mult
        tol = half_ulp * scale
        if is_pct:
            v = v / 100.0            # ★ 统一按"比率"存，避免 4.76% 与 0.0476 互不认
        else:
            v = v * unit_mult
        out.append(NumRef(raw=raw, value=v, kind="fact", pos=m.start(),
                          tol=max(tol, abs(v) * 1e-9),
                          context=text[max(0, m.start() - 18):m.end() + 10].replace("\n", " ")))
    return out


# ══════════════════════════════════════════════════════════════════════
# ★★★ 源侧：把「源**自己声明**的单位」也换算成基数量级
# ══════════════════════════════════════════════════════════════════════
# 实测踩到（这是**误杀**里最伤人的一类，因为它专挑诚实的转述下手）：
#
#   引擎输出是 JSON，**数字和单位分在两个字段里**：
#       {"label": "总市值", "value": "3,020.59", "unit": "亿元"}
#   平坦的文本扫描只看得见 3020.59（「亿元」不在数字后面）
#   → 池子里没有 3.02059e11；
#   而回答写「总市值 3020.59 亿元」会被 ×1e8 变成 3.02059e11
#   → **两边都不认对方 → 诚实的转述被拦下（闸门退出码 1，回答发不出去）**。
#
# 同一类还有百分号：`{"value": "-1.17", "unit": "%"}`
#   → 池子里是 -1.17，回答「跌了 1.17%」是 -0.0117 → 同样对不上。
#
# ★ 这里的单位**不是猜的**：它是源自己写在 `unit` 字段里的。
#   所以这不是「放松判据」，是**把源已经声明过、而平坦扫描看不见的信息补回来**。
_UNIT_PREFIX = (("亿", 1e8), ("万", 1e4), ("千", 1e3), ("百", 1e2))


def _declared_base_value(value, unit):
    """`{"value": "3,020.59", "unit": "亿元"}` → 3.02059e11。认不出单位就返回 None。"""
    u = str(unit or "").strip()
    if not u:
        return None
    try:
        f = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if u.startswith("%"):
        return f / 100.0                 # ★ 统一按"比率"存，和 scan() 一致
    if u.startswith("‰"):
        return f / 1000.0
    for pre, mult in _UNIT_PREFIX:
        if not u.startswith(pre):
            continue
        # 和 scan() 同一条防「半个词」的规矩：「百分位」不是「一百」
        if pre == "百" and u[1:2] == "分":
            return None
        return f * mult
    return None                          # 元/倍/股… 本来就是基数量级，无需别名


def _iter_declared_pairs(obj):
    """递归找出 JSON 里所有 `{… "value": X, "unit": U …}` 结构。"""
    if isinstance(obj, dict):
        if "value" in obj and "unit" in obj:
            yield obj.get("value"), obj.get("unit")
        for v in obj.values():
            yield from _iter_declared_pairs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_declared_pairs(v)


def scan_source(text: str) -> list:
    """源侧扫描 = 普通 `scan()` + **按源声明的单位换算出来的基数量级别名**。

    ★ 为什么不能只用 `scan()`：引擎输出是 JSON，单位在**另一个字段**里，
      平坦扫描看不见 → 见上面那段实测记录。
    """
    nums = list(scan(text))
    try:
        blob = json.loads(text)
    except Exception:                                        # noqa: BLE001
        return nums                      # 源不是 JSON（纯文本）→ 退化成普通扫描
    for val, unit in _iter_declared_pairs(blob):
        base = _declared_base_value(val, unit)
        if base is None:
            continue
        nums.append(NumRef(raw=str(val), value=base, kind="fact", pos=-1,
                           context=f"源声明的单位：{val} {unit}"))
    return nums


def _found(ref: "NumRef", pool: list) -> bool:
    """在源数字池里找这个数。

    ★ 容差**来自回答本身的写法**（`ref.tol` = 最后一位的半个单位），不是全局常数。

    为什么这样做（这是本层最关键的一处设计）：

      太松（固定 1%）→ 「10.81」被改成「10.82」只有 0.09%，**溜过去了**，防线作废。
      太严（逐字相等）→ 「2.96 亿」与「295,914,290.97」对不上、
                       「-4.76%」与「4.76%」对不上，**全是误报**。

    跟精度走之后两件事同时成立：
      · 写「2.96 亿」→ 容差 5e5，源 2.95914e8 落进来 → **放过**（合法四舍五入）
      · 写「10.82」  → 容差 0.005，源 10.81 差 0.01 → **抓到**（真改了）

    另外同时试**绝对值**：源写「涨跌幅 -4.76」，回答写「跌了 4.76%」，是同一个数。
    """
    tol = max(ref.tol, 1e-12)
    for x in pool:
        if abs(ref.value - x) <= tol or abs(abs(ref.value) - abs(x)) <= tol:
            return True
    return False


def check(answer: str, source: str) -> dict:
    """★ 主入口。

    返回 `{"total", "ok", "missing": [NumRef...], "pool_size"}`。
    `missing` 里每一个都是**回答里有、但引擎输出里找不到的数字** ——
    按铁律 3，这些数字不许发出去。
    """
    ans_nums = scan(answer)
    src_nums = scan_source(source)       # ★ 源侧要认「声明出来的单位」，见上面的实测记录
    pool = [n.value for n in src_nums]
    missing = [n for n in ans_nums if not _found(n, pool)]
    return {
        "total": len(ans_nums),
        "ok": len(ans_nums) - len(missing),
        "missing": missing,
        "pool_size": len(pool),
        # ★ 给每个「找不到」的数附上**源里最接近的那个值**。
        #   为什么要附：闸门只说「找不到」对写回答的人**没有可操作性** ——
        #   实测最常见的一类失败是「四舍五入写错」（源 1293.5247，写 1293，
        #   而正确是 1294）。把最近值给出来，一句话就能改对。
        "nearest": {n.raw: _nearest(n.value, pool) for n in missing},
    }


def _nearest(v: float, pool: list):
    """源数字池里离 v 最近的那个值（相对差 > 50% 就不给，免得误导）。"""
    best, bd = None, None
    for x in pool:
        d = abs(abs(x) - abs(v))
        if bd is None or d < bd:
            best, bd = x, d
    if best is None or bd is None:
        return None
    if abs(v) > 0 and bd / max(abs(v), 1e-12) > 0.5:
        return None                     # 差太远 → 不是"近似写错"，是编造
    return best


def render(result: dict) -> str:
    """人类可读的结果。

    ★★ 每个「找不到」的数都附上**源里最接近的值**。
       为什么：闸门只说「找不到」对写回答的人**没有可操作性** ——
       实测最常见的一类失败是**四舍五入写错**
       （源 1293.5247，写 1293，而正确是 1294），
       把最近值给出来，一句话就能改对。
    """
    n, ok, miss = result["total"], result["ok"], result["missing"]
    if not n:
        return "数字溯源：回答里没有事实数字（或全是标识符）"
    if not miss:
        return f"数字溯源：✅ {ok}/{n} 全部在引擎输出里找到"
    L = [f"数字溯源：🚫 {len(miss)}/{n} 个数字**在引擎输出里找不到** —— 不许发出"]
    near = result.get("nearest", {}) or {}
    for m in miss[:12]:
        L.append(f"    · {m.raw}  ← …{m.context}…")
        nv = near.get(m.raw)
        if nv is not None:
            L.append(f"        源里最接近的是 **{nv}**"
                     f"（要么直接用它，要么**四舍五入对**）")
    if len(miss) > 12:
        L.append(f"    · …还有 {len(miss) - 12} 个")
    L.append("    → 改掉上面这些数，再跑一次。")
    return "\n".join(L)
