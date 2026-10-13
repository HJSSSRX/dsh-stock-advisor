# -*- coding: utf-8 -*-
"""证据记录 —— 数字级溯源的载体。

设计来源与取舍（全部来自参考项目实测，不是拍脑袋）：

★ 来自 VeriFin（Apache-2.0，1★ 但 26 模块 / 283 测试的真工程）：
  1. **所有字段可空** —— 「字段不全 → 证据作废 → 拒答」是**一等业务结果，不是异常**。
     所以本模块不用异常表达"缺字段"，用 `missing_fields`。
  2. **value 用字符串，不用 float** —— 避免 JSON 浮点损失（0.1+0.2 那类问题）。
  3. **数值 0 不算缺失** —— 净利润为 0 是合法数据，不能用 falsy 判断。
  4. **六元组之外还有第七项「口径」** —— 合并/母公司、前复权/不复权，不写清就是歧义。

★ 来自 FinGround（arXiv 2604.23588）：
  5. 声明分六类，**computational 类幻觉率最高（28.4%），必须真重算，容差 ±0.5%**。

★ 来自 TradingAgents（110K★）：
  6. `coverage_gap` —— **区分「查了没有」与「源没观测那段」**。沉默不是证据。
  7. **冲突时只报冲突，不许调和** —— "flag the discrepancy rather than inventing a reconciled number"。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional


# ══════════════════════════════════════════════════════════════════
# 声明分类（FinGround 六类 —— 实测六类比三类高 4.3 F1，十类无显著增益）
# ══════════════════════════════════════════════════════════════════
class ClaimKind(str, Enum):
    NUMERICAL = "numerical"                  # 数值类：净利润 1.1 亿
    TEMPORAL = "temporal"                    # 时间类：2026-06-30
    ENTITY_ATTR = "entity-attribute"         # 实体属性：行业=通用设备
    COMPARATIVE = "comparative"              # 比较类：同比/环比/高于同行
    REGULATORY = "regulatory"                # 规则类：深交所规定…
    COMPUTATIONAL = "computational"          # ⚠️ 计算类：毛利率=毛利/营收 ← 幻觉率最高，必须重算


# ══════════════════════════════════════════════════════════════════
# 判定
# ══════════════════════════════════════════════════════════════════
class Verdict(str, Enum):
    """核验结论。**刻意分出 REFUSE 与 ABORT**（VeriFin 的设计）。

    - PASS   ：证据齐全且通过校验
    - WARN   ：能过但有疑点（如同比变动 300% —— 可能是并购，不能判 FAIL）
    - FAIL   ：校验不通过（数值对不上、片段找不到）
    - REFUSE ：**我们主动不答**（证据不足）—— 这是业务结果，要如实告诉用户
    - ABORT  ：**系统故障**（源挂了/被限流）—— 与 REFUSE 严格区分，不能混成一句"我不知道"
    """
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    REFUSE = "REFUSE"
    ABORT = "ABORT"


# ══════════════════════════════════════════════════════════════════
# 拒答原因（机器可读 + 人看）
# ══════════════════════════════════════════════════════════════════
class RefusalReason(str, Enum):
    """拒答的机器可读原因。**上层据此决定话术，不许模型自由发挥。**

    来源：VeriFin 的 `refusal.reason` + TradingAgents 的 `coverage_gap` + 我们取数层的 error_kind。
    """
    MISSING_FIELD = "missing_field"                    # 证据缺字段（company/period/…）
    SPAN_NOT_FOUND = "span_not_found"                  # 片段在原文里找不到
    VALUE_NOT_IN_SPAN = "value_not_in_span"            # 数值不能从该片段解析出来
    SOURCE_SILENT = "source_silent"                    # ★ 源没观测那段 —— 沉默不是证据
    NO_DATA_EXISTS = "no_data_exists"                  # 确实没有这条记录（有信息量的结论）
    SOURCE_FAILED = "source_failed"                    # 源故障/被限流（≠ 没有数据）
    CONFLICT = "conflict"                              # ★ 多源冲突 —— 只报冲突，不调和
    AMBIGUOUS_CALIBER = "ambiguous_caliber"            # 口径歧义（合并 vs 母公司）
    VERIFY_FAILED = "verify_failed"                    # 重算不通过
    OUT_OF_SCOPE = "out_of_scope"                      # 超出能力边界（如预测涨跌）
    NOT_UNDERSTOOD = "not_understood"                  # 没听懂问题（需澄清而非硬答）


@dataclass(frozen=True)
class Refusal:
    """拒答。**结构化**：机器可读的 reason + 人看的 detail + 补充字段。"""
    reason: RefusalReason
    detail: str = ""                                   # 给用户看的一句话
    missing: tuple[str, ...] = ()                      # 缺哪些字段/数据
    hint: str = ""                                     # 用户可以怎么自己确认
    options: tuple[str, ...] = ()                      # 多轮澄清的候选（VeriFin 的 ambiguous_options）

    def to_user(self) -> str:
        """给用户看的文本。**必须有"你可以怎么自己确认"** —— 调研结论：
        不要把不确定性变成数字，要把「缺什么」变成一句用户能自己去核对的具体话。"""
        parts = [self.detail or "这一条我没法给结论。"]
        if self.missing:
            parts.append("缺的是：" + "、".join(self.missing))
        if self.hint:
            parts.append("你可以自己核对：" + self.hint)
        if self.options:
            parts.append("你是想问：" + " / ".join(self.options))
        return " ".join(parts)


# ══════════════════════════════════════════════════════════════════
# 证据记录（六元组 + 口径）
# ══════════════════════════════════════════════════════════════════
_REQUIRED = ("company", "period", "metric", "value", "unit", "source")


@dataclass(frozen=True)
class Evidence:
    """一条证据 = 一个可字符串级核验的"数字 + 出处"。

    ★ 关键：**所有字段默认为空**，用 `is_complete` 判断，不用异常。
      "证据不完整" 是预期内的正常情况，不是 bug。
    """
    # ── 六元组 ──
    company: str = ""            # 主体：公司名或代码（口径之一 —— 同一代码可能指母公司或合并）
    period: str = ""             # 期间：报告期 / 交易日
    metric: str = ""             # 指标名
    value: str = ""              # ★ 数值，**字符串**（避免浮点损失）；"0" 是合法值
    unit: str = ""               # 单位：元 / 万元 / 亿元 / % / 股
    source: str = ""             # 出处：接口名 / 公告标题 / "2024年报 P83"

    # ── 第七项：口径（不写清就是歧义）──
    caliber: str = ""            # 合并 / 母公司；前复权 / 不复权

    # ── 溯源附加 ──
    span: str = ""               # 原文片段（PDF 场景用于双层校验）
    page: int = 0                # 页码（1-based）
    source_url: str = ""         # 可回访的地址
    fetched_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    kind: ClaimKind = ClaimKind.NUMERICAL

    # ★★ 接口类数据的「回原文核对」靠下面两个字段
    #    （PDF 类数据靠 span/page：回原文找那一句话）
    #
    #    为什么必须有：没有它们，"这个数字来自哪一包原始数据"就只是一句**声明**，
    #    没有任何办法核对。有了它们，「拿一个数字走回原始包、发现包里不是这个数」
    #    才是一个**能执行的操作** —— 这是证据层对接口类数据的立身之本。
    origin_eid: str = ""         # 来自哪一包原始数据（data/result.content_eid）
    origin_path: str = ""        # 在那一包的哪个字段（如 "0.归母净利润"）

    # ── 证据号（内容寻址，append-only）──
    eid: str = ""

    def __post_init__(self) -> None:
        # ★ eid 只在**为空时**才算 —— 这是故意的：
        #   从存储里还原时必须保留「记录下来的 eid」，否则「存盘之后被改过」
        #   就永远查不出来了（保真靠的就是这个不一致）。
        #
        # ⚠️ 副作用（实测踩到）：`dataclasses.replace(ev, value=...)` 会得到一个
        #    **eid 在说谎**的证据 —— 内容变了、指纹没重算。
        #    它会被下游核验逮住（claim.py：引用号与内容对不上）。
        #    → **要造一条合法的新证据，就重新构造 Evidence，别用 replace。**
        if not self.eid:
            payload = "|".join([self.company, self.period, self.metric,
                                self.value, self.unit, self.source, self.caliber])
            object.__setattr__(self, "eid", hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12])

    # ------------------------------------------------------------------
    @property
    def is_complete(self) -> bool:
        """六元组齐不齐。齐了才能当证据；不齐 → 拒答（一等业务结果）。"""
        return not self.missing_fields

    @property
    def missing_fields(self) -> list[str]:
        """缺哪些字段。

        ★ 注意 value 的判空：**用空串判断，不用 falsy** —— "0" 是合法数值。
        """
        miss = []
        for f in ("company", "period", "metric", "unit", "source"):
            if not str(getattr(self, f)).strip():
                miss.append(f)
        if not str(self.value).strip():
            miss.append("value")
        return miss

    @property
    def decimal(self) -> Optional[Decimal]:
        """数值的 Decimal 形式。**解析不了返回 None，不抛异常**（交给校验层判 FAIL）。"""
        try:
            return Decimal(str(self.value).replace(",", "").strip())
        except (InvalidOperation, AttributeError, ValueError):
            return None

    def refusal_if_incomplete(self) -> Optional[Refusal]:
        """不完整就给出拒答（而不是抛异常）。"""
        if self.is_complete:
            return None
        return Refusal(
            reason=RefusalReason.MISSING_FIELD,
            detail=f"关于「{self.metric or '这个指标'}」，我没能凑齐一条可核对的证据。",
            missing=tuple(self.missing_fields),
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    def tool_message(self) -> str:
        """喂给模型的形态。**必须带上出处** —— 这是要求①的底线。"""
        if not self.is_complete:
            r = self.refusal_if_incomplete()
            return f"【证据不完整·不可用】缺：{'、'.join(self.missing_fields)}。{r.detail if r else ''}"
        loc = self.source + (f" P{self.page}" if self.page else "")
        cal = f"（{self.caliber}）" if self.caliber else ""
        return (f"{self.metric} = {self.value} {self.unit}{cal}｜"
                f"{self.company}｜{self.period}｜出处：{loc}｜证据号 {self.eid}")
