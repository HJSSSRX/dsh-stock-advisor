# -*- coding: utf-8 -*-
"""从 a-stock-data 的 SKILL.md 提取可 import 的 Python 模块。

═══════════════════════════════════════════════════════════════════
为什么需要这个提取器
═══════════════════════════════════════════════════════════════════
`a-stock-data`（10,795★, Apache-2.0）不是 Python 包 —— 它是一份 **434 KB 的
Markdown 文件**，里面嵌了 **91 个 python 代码块 / 181 个函数**，形态是
「给人读、给 AI 抄」，不是「给程序 import」。

我们要用它当取数层，就必须把代码块拼成可 import 的模块。**这一步必须自动化**，
因为它一个月发 3 个版本 —— 手工提取一次，下次升级就废了。

实测结构（2026-10-12）：
    91 个块 / 5,854 行 / 261 KB
    重复定义的函数只有 1 个（`_macro_get` × 2）→ 去重规则简单
    块分两类：
        · 定义块（50~230 行）—— 含 def/class
        · 用法示例块（2~5 行）—— 只有调用语句，**不能在 import 时执行**
    → 只保留含顶层 def/class 的块，按出现顺序拼接，同名只留第一份。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SKILL = os.path.join(
    os.path.dirname(HERE), "素材", "竞品调研", "a-stock-data-main", "SKILL.md"
)

# 提取时会遇到的「块内自带 import」——合并到文件头，避免同一模块被 import 多次
HEADER = '''# -*- coding: utf-8 -*-
"""a-stock-data 提取产物 —— **自动生成，请勿手改**。

═══════════════════════════════════════════════════════════════════
来源   : simonlin1212/a-stock-data  (Apache-2.0, 可商用)
仓库   : https://github.com/simonlin1212/a-stock-data
版本   : {version}
提取于 : {extracted_at}
提取器 : 脚本/extract_astock.py
重新提取: python 脚本/extract_astock.py

★ 它**一个月发 3 个版本**（V3.9.0 / V3.10.0 / V3.10.1 / V3.10.2…）。
  · 升级步骤：重下 tarball → 跑提取器 → 跑 `python -m data.selftest_bridge`
    （那 27 项里有对拍，**升级后对拍不过就是它改坏了或改了口径**）
  · 本文件**不要手改** —— 下次跑提取器就覆盖了。要改行为，改提取器或写适配层。
═══════════════════════════════════════════════════════════════════

它做什么：34 个数据源 / 87 个端点 / 15 层（行情·研报·信号·资金筹码·新闻·
         基础数据·公告·打板·ETF期权·舆情互动·宏观利率·指数日历·期货·事件·可转债）
我们做什么：source/as_of/caliber 三件套 → 内容指纹 eid → span 核验 → 拒答
            （这些它**没有**；它有的 source_url/fetched_at 我们已经映射过来了）
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone

try:
    import pandas as pd
except ImportError:                      # pandas 是它的硬依赖
    raise

try:
    import requests
except ImportError:
    requests = None

# 各源客户端（按需缺失，不阻断 import）
try:
    import mootdx
except ImportError:
    mootdx = None
try:
    import baostock as bs
except ImportError:
    bs = None
'''



def detect_version(skill_path: str = DEFAULT_SKILL) -> str:
    """从 SKILL.md 的 CHANGELOG 里探出当前版本号。

    ★ 为什么非要记版本：它一个月发 3 版（V3.9.0 / V3.10.0 / V3.10.1 / V3.10.2…）。
      有一天取数出错，第一个要问的就是「我们锁的是哪一版」。
    """
    s = io.open(skill_path, encoding="utf-8", errors="replace").read()
    # 形如：> **V3.10.2（PE 消化年数修正，2026-10-11 · #58）：**
    m = re.search(r"\*\*(V\d+\.\d+(?:\.\d+)?)\s*[（(]", s)
    return m.group(1) if m else "unknown"


def extract(skill_path: str = DEFAULT_SKILL) -> tuple[str, dict]:
    """返回 (模块源码, 统计信息)。

    ★ 为什么用 AST 而不是正则（第一版用正则，踩到坑）
    ──────────────────────────────────────────────
    第一版规则是「保留含顶层 def/class 的块」，结果模块里混进了一行演示代码：
        norm_ticker("6005190")   # 演示：7 位会被拒
    它在 import 时**真的执行了**并抛 ValueError → 整个模块 import 失败。

    实测顶层语句分布（91 个块）：
        Assign 247 / FunctionDef 182 / Import 100   ← 要留
        Expr    78 / For 36                          ← **全是演示代码，必须丢**
        If       9 / Try 2 / With 1                  ← 逐个看过：If/With 全是演示
                                                        Try 是可选 import（留 import 丢 Expr）
    """
    ver = detect_version(skill_path)
    s = io.open(skill_path, encoding="utf-8", errors="replace").read()
    blocks = re.findall(r"```python\n(.*?)```", s, re.S)

    # 顶层保留的语句类型（Assign 单独判定，见下）
    KEEP_TOP = (ast.Import, ast.ImportFrom, ast.FunctionDef,
                ast.AsyncFunctionDef, ast.ClassDef)
    # Try 里只挑这几类捞出来（可选 import 模式）
    KEEP_IN_TRY = (ast.Import, ast.ImportFrom)

    # ══════════════════════════════════════════════════════════════
    # Assign 的判定：**只有右边调用的全部是纯函数/构造器才保留**
    # ══════════════════════════════════════════════════════════════
    # 实测：顶层 Assign 共 247 个
    #   104 个右边无调用 → 真常量（_TDX_SERVERS / SH_INDEX / UA / DATACENTER_URL）
    #   143 个右边有调用 → 混着两类：
    #         ✅ 真需要的：_TICKER_RE = re.compile(...) / EM_SESSION = requests.Session()
    #         ⛔ 演示代码：quotes = tencent_quote([...]) / client = tdx_client()
    #                      ↑ 后者第一版被保留了，**import 时联网卡 50 秒然后抛错**
    #
    # → 判据：把右边出现的**所有**调用名收集起来，全部在白名单里才留。
    PURE_CALLS = {
        "re.compile",
        "requests.Session", "Session",
        "timezone", "timedelta", "date", "datetime",
        "Counter", "defaultdict", "OrderedDict",
        "list", "set", "dict", "tuple", "frozenset",
        "ssl.create_default_context", "create_default_context",
        "functools.partial", "partial",
    }

    def _call_names(node) -> set[str]:
        """收集表达式里出现的所有调用名（`a.b()` → `a.b`，`f()` → `f`）。"""
        out: set[str] = set()
        for c in ast.walk(node):
            if not isinstance(c, ast.Call):
                continue
            f = c.func
            if isinstance(f, ast.Attribute):
                if isinstance(f.value, ast.Name):
                    out.add(f.value.id + "." + f.attr)
                else:
                    out.add(f.attr)
            elif isinstance(f, ast.Name):
                out.add(f.id)
            else:
                out.add("<expr>")
        return out

    def _seg(src: str, node) -> str | None:
        """取源码片段 —— **必须包含装饰器**。

        ★★★ 这个是踩出来的坑，务必别改回去 ★★★
        `ast.get_source_segment()` **不含装饰器**。
        第一版直接用它，结果 28 个顶层函数的装饰器全丢了：

            @_v39_contract   ×27  ← 他们的「统一异常契约」。丢了之后，
                                    来源格式变化会抛裸 KeyError，
                                    而调用方会把它当成「参数错/没数据」——
                                    **正是我们要防的那种静默错误**
            @contextmanager  ×1   ← 生成器当上下文管理器用 → 直接报错（撞上的就是它）
            @functools.wraps ×1

        → 做法：从**最靠上的那个装饰器**那一行开始截，到函数体结束。
        """
        if node.col_offset != 0:                  # 只看顶层
            pass
        seg = ast.get_source_segment(src, node)
        if seg is None:
            return None
        decs = getattr(node, "decorator_list", None)
        if not decs:
            return seg
        lines = src.split("\n")
        start = min(d.lineno for d in decs) - 1   # 1-based → 0-based；取最靠上的装饰器
        end = getattr(node, "end_lineno", None) or node.lineno
        start = max(0, start)
        if start >= len(lines):
            return seg
        return "\n".join(lines[start:end])

    def extract_one(src: str, node) -> str | None:
        return _seg(src, node)

    kept_src: list[str] = []
    seen: set[str] = set()
    # 模块级已定义的名字（从内建库开始，按语句顺序累积 = Python 的真实语义）
    import builtins as _bi
    defined: set[str] = set(dir(_bi)) | {"__name__", "__file__"}
    stats = {"代码块": len(blocks), "丢弃片段": 0, "保留片段": 0,
             "重名丢弃": [], "演示语句丢弃": 0,
             "丢弃演示赋值": 0, "保留带纯调用的常量": 0}

    for b in blocks:
        try:
            tree = ast.parse(b)
        except SyntaxError:
            stats["丢弃片段"] += 1
            continue

        for node in tree.body:
            name = getattr(node, "name", None)

            # ① 重名函数/类：只留第一份
            if name and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                          ast.ClassDef)):
                if name in seen:
                    stats["重名丢弃"].append(name)
                    continue
                seen.add(name)

            # ② 直接保留（import / def / class），并登记它们定义的名字
            if isinstance(node, KEEP_TOP):
                seg = _seg(b, node)
                if seg:
                    kept_src.append(seg)
                    stats["保留片段"] += 1
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        defined.add((a.asname or a.name).split(".")[0])
                elif name:
                    defined.add(name)
                continue

            # ③ Assign / AnnAssign —— 两道判据都要过
            #    判据 A：右边的调用必须全是纯函数（挡 tencent_quote() / tdx_client()）
            #    判据 B：右边引用的名字必须**已在模块级定义**（挡 ticks[...] / data[-20:]）
            #           实测漏网的 5 条全靠 B 抓到：
            #             auction = ticks[ticks.time < "09:30:00"]   ← 只有取下标+比较，A 挡不住
            #             recent_20 = data[-20:] / q = quotes[code] / hot = [s for s in a[...]]
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                val = node.value
                calls = _call_names(val) if val is not None else set()
                ok_calls = (not calls) or (calls <= PURE_CALLS)
                ok_names = True
                if val is not None:
                    for x in ast.walk(val):
                        if (isinstance(x, ast.Name)
                                and isinstance(x.ctx, ast.Load)
                                and x.id not in defined):
                            ok_names = False
                            break
                if ok_calls and ok_names:
                    seg = _seg(b, node)
                    if seg:
                        kept_src.append(seg)
                        stats["保留片段"] += 1
                        if calls:
                            stats["保留带纯调用的常量"] += 1
                    # 登记它定义的名字，供后面的赋值判断
                    tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in tgts:
                        for x in ast.walk(t):
                            if isinstance(x, ast.Name):
                                defined.add(x.id)
                    continue
                stats["丢弃演示赋值"] += 1
                continue

            # ④ Try：只把里面的 import 捞出来
            if isinstance(node, ast.Try):
                for sub in node.body:
                    if isinstance(sub, KEEP_IN_TRY):
                        seg = _seg(b, sub)
                        if seg:
                            kept_src.append(seg)
                            stats["保留片段"] += 1
                continue

            # ⑤ 其余（Expr / For / If / With）一律丢 —— 全是演示代码
            stats["演示语句丢弃"] += 1

    body = "\n\n\n".join(kept_src)
    out = {
        "代码块总数": len(blocks),
        "保留片段": stats["保留片段"],
        "丢弃演示语句": stats["演示语句丢弃"],
        "顶层函数/类": len(seen),
        "重名丢弃": len(stats["重名丢弃"]),
        "重名名单": sorted(set(stats["重名丢弃"])),
        "产物行数": body.count("\n") + 1,
        "产物字节": len(body.encode("utf-8")),
    }
    from datetime import datetime as _dt
    hdr = HEADER.format(version=ver,
                        extracted_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"))
    out["来源版本"] = ver
    return hdr + "\n" + body + "\n", out


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), "产出", "stockharness", "vendor", "astock.py"
    )
    src, stats = extract()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="\n").write(src)

    print("  提取完成 → %s" % out)
    for k, v in stats.items():
        if k != "重名名单":
            print("    %-18s %s" % (k, v))
    if stats["重名名单"]:
        print("    %-18s %s" % ("重名名单", ", ".join(stats["重名名单"])))

    # 语法自检（不执行，只编译）
    try:
        compile(src, out, "exec")
        print("    %-18s 通过" % "语法编译")
    except SyntaxError as e:
        print("    %-18s ✗ 第 %s 行: %s" % ("语法编译", e.lineno, e.msg))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
