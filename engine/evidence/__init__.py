# -*- coding: utf-8 -*-

import sys as _sys

# ↓↓ Windows 默认控制台是 GBK(cp936)：打印 ✓ 会 UnicodeEncodeError 直接崩，
#    看起来像「整套自测都坏了」，其实断言都跑过了。这里兜底成 utf-8，
#    保证无论从 bash / cmd / PowerShell 哪个终端跑都能跑到底。
try:
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(_sys.stderr, "reconfigure"):
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
"""证据层 —— 数字级溯源的载体与校验。

    判断层（自建）      结论 + 置信度 + 反方 + 失效条件
          ↓ 每个依据必须指向 ↓
    证据层（本包）      六元组式证据，每个字段可字符串级校验

★ 分层理由（VeriFin 解剖结论）：六元组的全部威力来自"每个字段都可验证"。
  一旦塞进"置信度/反方观点"这种不可验证字段，严谨性会被稀释。
  分层后，"判断层的每个依据必须指向一个通过校验的证据"就是需求②的程序化落地。

模块：
    record.py  证据记录（六元组+口径，所有字段可空）、拒答（结构化）、判定
    span.py    双层硬校验（片段在不在原文 + 数值能不能从片段推出来）
    bridge.py  ★ 取数结果 → 证据（**取不到就抛，绝不给 0**；数值必须归一）
    pdf_source.py  PDF 原文提取（一手来源通道）
"""
from .record import (          # noqa: F401
    ClaimKind,
    Evidence,
    Refusal,
    RefusalReason,
    Verdict,
)
from .span import (            # noqa: F401
    SpanMatch,
    extract_amounts,
    locate_span,
    normalize_text,
    value_matches_span,
    verify_computed,
    verify_span,
)
from .bridge import BridgeError, dig, to_evidence, trace   # noqa: F401

__all__ = [
    "ClaimKind", "Evidence", "Refusal", "RefusalReason", "Verdict",
    "SpanMatch", "extract_amounts", "locate_span", "normalize_text",
    "value_matches_span", "verify_computed", "verify_span",
    "BridgeError", "dig", "to_evidence", "trace",
]
