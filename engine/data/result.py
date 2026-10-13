# -*- coding: utf-8 -*-
"""统一取数结果结构。

核心纪律（写死在类型里，不靠自觉）：
  1. 失败也是值 —— 取不到数返回 ok=False，**绝不返回空表、绝不返回 0**
  2. 每个数字都要能追到 source + as_of + caliber
  3. 每条数据带内容寻址的 eid，供上层"证据层"引用
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

__all__ = ["Result", "content_eid", "now_str", "KINDS"]

# 数据类型（也是路由表的键）
KINDS = (
    "quote",         # 实时行情
    "kline",         # K 线（日/周/月/分钟）
    "fundflow",      # 资金流
    "financial",     # 财务（业绩报表）
    "announcement",  # 公告
    "board",         # 板块列表 / 成分股
    "dividend",      # 分红送配（除权除息日）
    "universe",      # 全市场列表（带行业+概念标签）
    "index",         # 指数现货行情
    "index_kline",   # 指数日K（交易日的权威来源）
)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def content_eid(source: str, kind: str, code: str, period: str, raw: Any) -> str:
    """内容寻址的证据 ID：同样内容永远得到同样的 eid（天然去重、可复现）。"""
    basis = "|".join(
        [
            source,
            kind,
            code,
            period,
            json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class Result:
    """一次取数的统一返回。

    ★ status 与 error_kind 是「失败状态作为一等公民」的落地（来自 TradingAgents 的教训）：
      参考项目最大的问题之一，是**把失败伪装成正常结论**（9 条失败路径全变成"中性 0.5"），
      下游在数据结构上分不清"系统崩了"和"真的没机会"。所以：
        · status: ok | degraded | failed
        · error_kind: 区分「取不到」与「确实没有」—— 后者是有信息量的结论
    """

    ok: bool
    kind: str
    code: str = ""
    data: Any = None

    # —— 溯源三件套（缺一不可）——
    source: str = ""          # 数据来自哪个接口/源
    as_of: str = ""           # 数据本身的时间（交易日 / 报告期）
    caliber: str = ""         # 口径说明（复权方式、统计区间等）

    # —— 可信度与状态 ——
    level: str = "A"          # A=核验级（接口直取） / B=参考级（新闻等）
    status: str = ""          # ok | degraded | failed（为空时按 ok/cached 自动推导）
    error_kind: str = ""      # not_found | no_data_in_window | rate_limited | source_error

    # ★★ 数据新鲜度 —— **取不到时用过期缓存兜底，但必须让上层知道它过期了**
    #    stale=True 表示：这是缓存里的旧数据，因为所有源都取不到才拿出来用的。
    #    **判断层必须把它写进给用户的回答**（"这是 X 小时前的数据"），
    #    否则就等于在用旧数据冒充实时数据。
    stale: bool = False
    age_seconds: float = 0.0
    cached: bool = False
    fallback_used: bool = False
    eid: str = ""
    error: str = ""
    attempts: list = field(default_factory=list)  # [{source, ok, ms, note}]
    fetched_at: str = field(default_factory=now_str)

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.ok and not self.eid and self.data is not None:
            self.eid = content_eid(self.source, self.kind, self.code, self.as_of, self.data)
        if not self.status:
            if not self.ok:
                self.status = "failed"
            elif self.cached or self.fallback_used:
                self.status = "degraded"
            else:
                self.status = "ok"

    @property
    def missing(self) -> bool:
        """这条数据是不是"缺"——上层据此决定要不要拒答。"""
        return not self.ok

    @property
    def is_conclusive_empty(self) -> bool:
        """**"确实没有" ≠ "没取到"**。

        例如"该公司从未分红"是一个**可以告诉用户的结论**；
        而"被限流了"是故障。上层必须区别对待，否则就是用沉默当证据。
        """
        return self.error_kind == "no_data_in_window"

    @property
    def needs_retry(self) -> bool:
        """是否值得稍后重试（限流/源故障值得，确实没有不值得）。"""
        return (not self.ok) and self.error_kind in ("rate_limited", "source_error")

    def why(self) -> str:
        """给人和模型看的失败原因 —— **必须区分"没数据"与"取不到"**。"""
        if self.ok:
            return ""
        if self.error_kind == "no_data_in_window":
            return "该区间内确实没有记录（这是一个结论，不是故障）"
        if self.error_kind == "not_found":
            return "标的代码不存在（请核对代码，不要猜测其他标的的数据）"
        if self.error_kind == "rate_limited":
            return "数据源限流，本次没取到 —— 注意：这**不代表该标的就是没数据**，应当重试或换源"
        detail = "; ".join(
            f"{a.get('source')}: {a.get('note', '')}"
            for a in self.attempts
            if not a.get("ok")
        )
        return self.error or detail or "未知原因"

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "error_kind": self.error_kind,
            "kind": self.kind,
            "code": self.code,
            "source": self.source,
            "as_of": self.as_of,
            "caliber": self.caliber,
            "level": self.level,
            "cached": self.cached,
            "fallback_used": self.fallback_used,
            "eid": self.eid,
            "fetched_at": self.fetched_at,
            "error": self.error,
            "attempts": self.attempts,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    def tool_message(self, max_rows: int = 40) -> str:
        """转成喂给模型的文本。

        ★ 关键：成功时**必须**带上来源与时间；失败时**必须**说清是哪种失败，
          绝不给一个看起来像数据的空壳，也绝不把"取不到"说成"没有"。
        """
        if not self.ok:
            if self.error_kind == "no_data_in_window":
                return (
                    f"【{self.kind} {self.code}】查询成功，但该区间内确实没有记录。\n"
                    "→ 这是**可以如实告诉用户的结论**（例如「该公司在此期间没有分红记录」），"
                    "但不要引申为「公司经营有问题」——没有记录不等于坏消息。"
                )
            return (
                f"【取数失败】{self.kind} {self.code}（{self.error_kind or '未知类型'}）\n"
                f"原因：{self.why()}\n"
                "→ 请如实告诉用户「这项数据我没取到」，不要推测、不要用记忆里的数字代替。"
            )

        head = f"【{self.kind} {self.code}】来源：{self.source}｜数据时间：{self.as_of}"
        if self.caliber:
            head += f"｜口径：{self.caliber}"
        if self.status == "degraded":
            head += "｜⚠️ 降级取数（" + ("缓存" if self.cached else "备源") + "）"
        head += f"｜证据号：{self.eid}"

        rows = self.data
        if isinstance(rows, list):
            body = "\n".join(
                "  " + json.dumps(r, ensure_ascii=False, default=str)
                for r in rows[:max_rows]
            )
            if len(rows) > max_rows:
                body += f"\n  ……（共 {len(rows)} 条，已截断）"
        else:
            body = "  " + json.dumps(rows, ensure_ascii=False, default=str)

        return head + "\n" + body


def fail(kind: str, code: str, error: str, attempts: Optional[list] = None,
         error_kind: str = "source_error") -> Result:
    """构造一个"失败即值"的结果。"""
    return Result(ok=False, kind=kind, code=code, error=error,
                  attempts=attempts or [], error_kind=error_kind)
