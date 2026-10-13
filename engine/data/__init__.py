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
