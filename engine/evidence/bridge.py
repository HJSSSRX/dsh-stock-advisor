# -*- coding: utf-8 -*-
"""取数结果 → 证据 —— 把"接口拿回来的原始包"变成"可核验的数字"。

═══════════════════════════════════════════════════════════════════
这一层的职责：**让"取不到这个字段"不可能被误当成"这个值是 0"**
═══════════════════════════════════════════════════════════════════
血泪清单第 6 条（咬过两次）：

    中文键 → 静默拿 None → `float(None or 0)` → **全变 0**
    而测试一边显示"通过"一边在验算 0。

所以本模块的核心不是"取值"，是**取不到就抛**：

    · 取数本身失败        → BridgeError（失败的东西不能当证据）
    · 字段不存在          → BridgeError（**不给 0**）
    · 字段是空值 / None   → BridgeError（**空不是 0**）

═══════════════════════════════════════════════════════════════════
★★ 第二个坑：同一个数字，两种写法，两个证据号
═══════════════════════════════════════════════════════════════════
实测（缓存里的真实包）：

    新浪 `financial_sina`   归母净利润 = "44516880421.860000"   （字符串，带尾随零）
    东财 `financial_income` 归母净利润 = 44516880421.86          （浮点数）

**是同一个数**。但如果直接 `str(raw)` 存进证据：

    "44516880421.860000"  vs  "44516880421.86"  → **证据号不同**
    → 两个源明明对上，系统却会把它们当成「两条不同的证据」
    → 更糟：同一指标「两条不一致的证据」会被判成 **冲突**

→ 所以数值**必须归一**，复用 `pdf_source.canon()`（它的注释里就写着
  "尾随零不归一，同一个数会被判成找不到"—— 同一个坑，PDF 那边先踩过）。

★ 归一**只改写法，不做任何数值近似** —— 改一位仍然是另一个数。

═══════════════════════════════════════════════════════════════════
为什么 import pdf_source（会连带加载 PyMuPDF）
═══════════════════════════════════════════════════════════════════
`canon()` 住在 `pdf_source.py` 里。**不把它抄一份到这儿** —— 数值归一只能有
一个 owner，抄第二份就是本项目血泪清单里"口径混用"那一类错误的开端。
代价是 import 本模块会加载 PyMuPDF（本项目本来就硬依赖它）。
"""
from __future__ import annotations

from typing import Any, Optional

from .pdf_source import canon
from .record import ClaimKind, Evidence


class BridgeError(ValueError):
    """这条数据变不成证据。**必须抛，绝不给个 0 糊过去。**"""


def dig(data: Any, path: str) -> tuple[bool, Any, Any]:
    """按 `"0.归母净利润"` 这种路径取值。

    返回 `(找到没, 值, 它所在的容器)`。

    ★ 容器一起返回，是为了让调用方知道「值是在哪种结构里拿到的」
      （list 的第 N 行 / dict 的某个键）。

    ★★★ 2026-10-13 修正一处**写错的假设**：
      这里原来写着「单位常常写在兄弟字段里 —— 腾讯行情包里就是 `现价` 旁边
      放一个 `现价_单位`」，并据此做了一个 `_unit_from_siblings` 去捡
      `<字段>_单位`。**实测这种包不存在**（两条路都没有这个约定）：
        · vendor 的 `tencent_quote` → 英文键 + **后缀**表单位
          （`amount_wan` / `mcap_yi` / `turnover_pct`）
        · 我们自己的 tencent/sina/eastmoney 源 → 中文字段名，
          单位在 `data/caliber.py` 的「字段→单位」表里
      → 那个分支**永远返回空**（拿真实包和真实 vendor 返回值各验一次，见
        `evidence/selftest_bridge.py` G 段）。留着它就是一段永远不执行的兜底，
      已按「无效职责应当退休」删掉。
    """
    cur = data
    parent: Any = None
    for part in str(path).split("."):
        parent = cur
        if isinstance(cur, dict):
            if part not in cur:
                return False, None, None
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return False, None, None
        else:
            return False, None, None
    return True, cur, parent


def to_evidence(result, *, metric: str, path: str, unit: Optional[str] = None,
                caliber: Optional[str] = None, company: Optional[str] = None,
                period: Optional[str] = None,
                kind: Optional[ClaimKind] = None) -> Evidence:
    """从一次取数的结果里取一个字段，做成一条证据。

    没给 `unit` / `caliber` / `company` / `period` 时，**从 Result 上取默认值** ——
    这些信息取数层已经知道，不该让调用方再抄一遍（抄漏一个就是静默出错）。

    ★★ `unit` 是**唯一不猜的那一项**：给就给，不给就留空（证据变成"不完整"）。
      桥不许看字段名去推断单位 —— 猜错一个单位，结论会差 10000 倍，
      而且**看起来完全正常**。分析代码必须自己声明（见 G 段的断言）。
    """
    if not getattr(result, "ok", False):
        why = result.why() if hasattr(result, "why") else getattr(result, "error", "")
        raise BridgeError(f"取数没成功，不能当证据：{why or '未知原因'}")

    data = getattr(result, "data", None)
    found, raw, _ = dig(data, path)
    if not found:
        raise BridgeError(
            f"字段 {path!r} 在 {getattr(result, 'kind', '?')} 的返回里**不存在** —— "
            "不许当成 0 或空值继续往下走"
        )
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise BridgeError(
            f"字段 {path!r} 是空值（{raw!r}）—— **空不是 0**，不能当证据"
        )

    return Evidence(
        company=company or str(getattr(result, "code", "")),
        period=period or str(getattr(result, "as_of", "")),
        metric=metric,
        value=canon(raw),                 # ★ 必须归一，见模块开头第二个坑
        unit=(unit or ""),                 # ★ 不猜：见 docstring 的 ★★
        source=str(getattr(result, "source", "")),
        caliber=(caliber if caliber is not None
                 else str(getattr(result, "caliber", ""))),
        origin_eid=str(getattr(result, "eid", "")),
        origin_path=str(path),
        kind=kind or ClaimKind.NUMERICAL,
    )


def trace(ev: Evidence, result) -> tuple[bool, str]:
    """★ 拿一条证据**走回它的原始包**，核对包里还是不是这个数。

    这是「数字级溯源」对**接口类数据**的落地 ——
    PDF 类数据的落地是 `span`/`page`（回原文找那一句话）。

    两种篡改都会被抓到：
        · 有人改了**证据里的数**      → 与包里的值对不上
        · 有人改了**原始包里的数**    → 包的内容指纹变了
    """
    if not ev.origin_eid:
        return False, "这条证据没记它来自哪一包（origin_eid 为空）—— 无法回溯"
    stored = str(getattr(result, "eid", ""))
    if stored != ev.origin_eid:
        return False, (f"原始包对不上：证据记的是 {ev.origin_eid}，"
                       f"现在这包是 {stored}（内容被改过）")

    found, raw, _ = dig(getattr(result, "data", None), ev.origin_path)
    if not found:
        return False, f"原始包里已经没有字段 {ev.origin_path!r} 了"
    # ★ 两边都归一后再比 —— 证据里可能写 "1288"，包里是 "1,288"，**是同一个数**。
    #   直接字符串比会把它们判成不一致（实测踩到，溯源自己报假警）。
    now, claimed = canon(raw), canon(ev.value)
    if now != claimed:
        return False, f"值对不上：证据记的是 {claimed}，包里现在是 {now}"
    return True, f"回溯成功：{ev.origin_path} = {claimed}（包 {ev.origin_eid}）"
