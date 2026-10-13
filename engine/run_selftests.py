# -*- coding: utf-8 -*-
"""全量自测运行器 —— **按退出码判定，不按输出行判定**。

跑：python -m run_selftests   （在 stockharness 目录下）

═══════════════════════════════════════════════════════════════════
★ 为什么必须有这个文件（2026-10-13 实测踩到的血教训）
═══════════════════════════════════════════════════════════════════
在那之前，"跑全量自测"是一条 bash 循环，用 `grep '结果：'` 抓汇总行来统计。

**致命的缺陷**：如果某个模块**崩了**（异常退出、没打印汇总行），
`grep` 什么都抓不到 → 统计脚本得到空值 → 那个模块**被计成 0 通过 / 0 失败**，
于是"合计 621 通过 / 0 失败"看起来完全正常。

**实测后果**：`evidence.selftest_bridge` 崩了（退出 1、20 条断言后异常退出、
没有汇总行），**在好几轮"全绿"里被静默漏掉**。
是一个独立验证者用 `exit code` 才发现的 —— 而我自己的脚本一直说"全绿"。

→ 所以本运行器：
    ① **按退出码判定**（rc != 0 一律算失败，不管有没有汇总行）
    ② **没有汇总行**也算失败（那说明它中途崩了）
    ③ 汇总行里的"失败"数要 ≤ 0 且 rc == 0 才算过
    ④ 任何一项不过 → 整体退出码非 0（**不能再让"全绿"盖住崩溃**）
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import sys

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# (模块名, 是否吃网)  —— 吃网的排最后，且允许偶发失败（重跑一次再算）
MODULES = [
    ("compute.selftest_pit", False),
    ("compute.selftest", False),
    ("compute.selftest_indicators", False),
    ("compute.selftest_position", False),
    ("compute.selftest_risk", False),
    ("compute.selftest_breadth", True),
    ("compute.selftest_margin", True),
    ("compute.selftest_tieout", True),
    ("compute.selftest_minefield", True),
    ("compute.selftest_events", True),
    ("data.selftest_stability", False),
    ("data.selftest_bridge", True),
    ("data.selftest_intel_class", False),
    ("evidence.selftest", False),
    ("evidence.selftest_caliber", False),
    ("evidence.selftest_freshness", False),
    ("evidence.selftest_trust", False),
    ("evidence.selftest_pdf", False),
    ("evidence.selftest_bridge", False),
    ("judge.selftest_ledger", False),
    ("judge.selftest_claim", False),
    ("judge.selftest_ablation", False),
    ("judge.selftest_bear", False),
    ("judge.selftest_contract", False),
    ("judge.selftest_sensitivity", False),
    ("judge.selftest_score", False),
    ("judge.selftest_confidence", False),
    ("judge.selftest_provenance", False),
    ("selftest_advisor", False),                 # 根目录，个性化层+观察层
    ("selftest_ask", False),                     # 根目录，不在包里
]

_RE = re.compile(r"结果：(\d+)\s*通过\s*/\s*(\d+)\s*失败")


def run(mod: str) -> tuple[int, str, int, int]:
    _t0 = time.time()
    p = subprocess.run([PY, "-m", mod], cwd=HERE, capture_output=True)
    _dt = time.time() - _t0
    out = (p.stdout or b"").decode("utf-8", "replace") + \
          (p.stderr or b"").decode("utf-8", "replace")
    m = _RE.search(out)
    npass, nfail = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
    return p.returncode, out, npass, nfail, _dt


def main() -> int:
    tot = fails = 0
    bad: list[str] = []
    _times: list = []
    print("═" * 76)
    for mod, net in MODULES:
        rc, out, npass, nfail, _dt = run(mod)
        if rc != 0 and net:
            # 吃网的模块偶发失败：重跑一次再判（血泪清单：见到它失败先重跑）
            rc, out, npass, nfail, _dt = run(mod)
        ok = (rc == 0) and (nfail == 0) and (npass >= 0)
        if npass > 0:
            tot += npass
            fails += max(nfail, 0)
        flag = "✓" if ok else "✗"
        detail = (f"{npass} 通过 / {nfail} 失败" if npass >= 0
                  else "**没有汇总行（中途崩了）**")
        print(f"  {flag} {mod:32s} rc={rc}  {detail}  [{_dt:.1f}s]")
        _times.append((_dt, mod))
        if not ok:
            bad.append(mod)
            tail = "\n".join(out.strip().splitlines()[-12:])
            print("      ── 尾部输出 ──")
            for ln in tail.splitlines():
                print("      " + ln[:150])
    print("═" * 76)
    print(f"合计：{tot} 通过 / {fails} 失败 ｜ 有问题的模块：{len(bad)}")
    _times.sort(reverse=True)
    if _times:
        _tot = sum(t for t, _ in _times)
        print(f"总耗时 {_tot:.0f}s ｜ 最慢三项：" +
              "、".join(f"{m.split('.')[-1]}({t:.0f}s)" for t, m in _times[:3]))
        _slow = sum(t for t, _ in _times[:3])
        if _tot > 30 and _slow > _tot * 0.5:
            # ⚠️ 第一版这里写「它们吃网的」—— **那句是猜的，而且不对**。
            #    查过之后：`evidence.selftest_bridge` 是**离线**跑 `_cache/` 里
            #    85 个真实数据包、在 3000 条样本上统计误杀率 —— 是 **CPU 真干活**，
            #    不是网络慢。减样本会让统计失去意义，所以这个耗时是**可接受的代价**。
            print(f"  ⚠️ 前三项占了 {_slow/_tot*100:.0f}%：多为**在真实样本上做统计**"
                  f"（如误杀率 3000 条）—— 那是真干活，不是卡网络。")
    if bad:
        print("✗ 不许说「全绿」—— 这些模块没通过：" + "、".join(bad))
        return 1
    print("✓ 全部模块：退出码 0 + 有汇总行 + 0 失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
