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
"""计算层 —— 把数据变成结论。

模块：
    rules.py       A 股制度规则时间表（涨跌幅/涨跌停判定/口径声明）—— 地基，无依赖
    indicators.py  指标层（通达信口径 + 固定取数窗口）

★ 本层的三条纪律（来自四份调研，详见 `设计\计算层实现方案.md`）：
  1. **每个输出必须带口径声明** —— 用户会问「为什么和软件不一样」
  2. **不许把民间说法当信号** —— 禁止清单在方案文档 §0.1
  3. **统计显著 ≠ 可交易** —— 凡说「历史上管用」，必须带数据窥探校正

★ 两条实测出来的硬规则（都会静默出错，必须靠测试兜住）：
  1. **制度判定（涨跌停/一字板）必须用不复权价（fqt=0）**；
     用复权价，南华仪器 7 次涨停会**全部漏检**。见 `rules.CALIBER_NOTES`。
  2. **指标必须先取够预热再算**。MACD 需要 **300 根**预热，
     少了偏差 0.149（肉眼可见），而**不会报任何错**。见 `indicators.WARMUP_BARS`。
"""
from .indicators import (          # noqa: F401
    DEFAULT_WARMUP,
    INDICATOR_CALIBER,
    WARMUP_BARS,
    Window,
    atr,
    boll,
    ema_tdx,
    kdj,
    ma,
    macd,
    plan_window,
    rsi,
    sma_cn,
    with_indicators,
)
from .breadth import (          # noqa: F401
    MarketBreadth,
    breadth_from_package,
    market_snapshot,
)
from .tieout import (          # noqa: F401
    RULES,
    RULE_BY_ID,
    TieoutResult,
    run_all as run_tieout,
)
from .margin import (          # noqa: F401
    IDENTITY_TOL,
    MarginReport,
    MarginRow,
    load as load_margin,
    verify_identity,
)
from .load import LoadError, load_index_kline, load_kline, load_quote   # noqa: F401
from .pit import (          # noqa: F401
    DEADLINE,
    NO_HISTORY_KINDS,
    TRUNCATABLE_KINDS,
    Availability,
    assert_no_lookahead,
    audit_kinds,
    disclosure_deadline,
    financial_available,
    is_historical,
    period_end,
    snapshot_notice,
    truncate_to,
)
from .position import (          # noqa: F401
    BASELINE_BAND,
    LEVEL_CALIBER,
    MIN_BARS_FOR_LEVELS,
    MIN_TOUCHES_FOR_STRENGTH,
    RANDOM_WALK_BASELINE,
    Level,
    LevelSet,
    find_levels,
    levels_for_report,
)
from .risk import (          # noqa: F401
    LONG_SUSPENSION_DAYS,
    MAX_LOOKBACK_BARS,
    RISK_CALIBER,
    RiskProfile,
    Suspension,
    compute_beta,
    detect_suspensions,
    downside_vol,
    ewma_vol,
    fill_suspensions,
    hv,
    max_drawdown,
    risk_profile,
    var_historical,
)
from .rules import (          # noqa: F401
    BarLimit,
    Board,
    CALIBER_NOTES,
    LimitRule,
    Status,
    TRADING_RULES,
    board_of,
    classify_bar,
    limit_pct,
)

__all__ = [
    # rules
    "BarLimit", "Board", "CALIBER_NOTES", "LimitRule", "Status", "TRADING_RULES",
    "board_of", "classify_bar", "limit_pct",
    # indicators
    "DEFAULT_WARMUP", "INDICATOR_CALIBER", "WARMUP_BARS", "Window",
    "atr", "boll", "ema_tdx", "kdj", "ma", "macd", "plan_window", "rsi", "sma_cn",
    "with_indicators",
    # breadth
    "MarketBreadth", "breadth_from_package", "market_snapshot",
    # margin（资金面：只做可复算量）
    "IDENTITY_TOL", "MarginReport", "MarginRow", "load_margin", "verify_identity",
    # load
    "LoadError", "load_index_kline", "load_kline", "load_quote",
    # pit（时点守卫 —— 防前视偏差）
    "DEADLINE", "NO_HISTORY_KINDS", "TRUNCATABLE_KINDS", "Availability",
    "assert_no_lookahead", "audit_kinds", "disclosure_deadline",
    "financial_available", "is_historical", "period_end", "snapshot_notice",
    "truncate_to",
    # position
    "BASELINE_BAND", "LEVEL_CALIBER", "MIN_BARS_FOR_LEVELS", "MIN_TOUCHES_FOR_STRENGTH",
    "RANDOM_WALK_BASELINE", "Level", "LevelSet", "find_levels", "levels_for_report",
    # risk
    "LONG_SUSPENSION_DAYS", "MAX_LOOKBACK_BARS", "RISK_CALIBER", "RiskProfile",
    "Suspension", "compute_beta", "detect_suspensions", "downside_vol", "ewma_vol",
    "fill_suspensions", "hv", "max_drawdown", "risk_profile", "var_historical",
]
