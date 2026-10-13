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
"""判断层 —— 结论 + 依据 + 置信度 + 反方 + 失效条件。

★ 与证据层的分工（`evidence/__init__.py` 里也写了这条）：
    判断层（本包）  只有**不可验证**的东西：结论措辞、置信度、反方观点、失效条件
          ↓ 每个依据必须指向 ↓
    证据层          只有**可验证**的东西：六元组 + span 双层校验 + 引用号

模块：
    ledger.py  决策台账（★ 唯一能验证"判断准不准"的机制）
    claim.py   结论构造器 —— **没有依据的结论构造不出来**

判断层六块（详见 `设计\判断层调研报告.md` §五、§七）：
    ① compute/pit.py   时点守卫      ✅ 44 项
    ② judge/claim.py   结论构造器    ✅ 本模块
    ③ judge/ablation.py 证据消融     ⬜
    ④ judge/bear.py    反方观点      ⬜
    ⑤ judge/contract.py 输出契约     ⬜
    ⑥ judge/ledger.py  决策台账      ✅ 42 项
"""
from .claim import (          # noqa: F401
    CONF_CAP,
    CONF_CAP_MAX,
    Citation,
    Claim,
    ClaimInvalid,
    F_CALIBER_MISMATCH,
    F_CALIBER_MISSING,
    F_CONF_CAP,
    F_FRAGILE,
    F_CONFLICT,
    F_INSUFFICIENT,
    F_SINGLE,
    MIN_EVIDENCE,
    NoEvidenceError,
    build,
)
from .ablation import (          # noqa: F401
    AblationOutcome,
    AblationPolicy,
    AblationReport,
    DEFAULT_POLICY,
    FRAGILE,
    NOT_ESTIMABLE,
    RULES,
    RuleMismatch,
    SENSITIVE,
    STABLE,
    cp_upper,
    register,
    wilson,
)
from .bear import (          # noqa: F401
    CLEAR,
    F_BEAR_UNKNOWN,
    F_BEAR_UNVERIFIED,
    HIT,
    BearHit,
    BearScan,
    Trigger,
    TRIGGERS,
    UNKNOWN,
)
from .contract import (          # noqa: F401
    BLOCKER,
    OutputContext,
    OutputIssue,
    WARN,
    blockers,
    report,
)
from .ledger import (          # noqa: F401
    BEAR,
    BULL,
    DIRECTIONS,
    EDGE_PCT,
    HOLD,
    Decision,
    Stats,
    calibration,
    judge_correct,
    record,
    settle_one,
    summarize,
)

__all__ = [
    # claim
    "Citation", "Claim", "ClaimInvalid", "NoEvidenceError", "build",
    "MIN_EVIDENCE", "CONF_CAP", "CONF_CAP_MAX",
    "F_INSUFFICIENT", "F_SINGLE", "F_CONFLICT", "F_CALIBER_MISMATCH",
    "F_CALIBER_MISSING", "F_CONF_CAP", "F_FRAGILE",
    # bear（法定看空触发器）
    "HIT", "CLEAR", "UNKNOWN", "Trigger", "BearHit", "BearScan", "TRIGGERS",
    "F_BEAR_UNKNOWN", "F_BEAR_UNVERIFIED",
    # ablation（证据消融）
    "STABLE", "SENSITIVE", "FRAGILE", "NOT_ESTIMABLE", "RuleMismatch",
    "AblationPolicy", "AblationOutcome", "AblationReport", "DEFAULT_POLICY",
    "cp_upper", "wilson", "register", "RULES",
    # contract（输出契约）
    "BLOCKER", "WARN", "OutputContext", "OutputIssue", "blockers", "report",
    # ledger
    "BEAR", "BULL", "DIRECTIONS", "EDGE_PCT", "HOLD", "Decision", "Stats",
    "calibration", "judge_correct", "record", "settle_one", "summarize",
]
