# -*- coding: utf-8 -*-
"""stockctl —— 引擎的命令行工具面（给模型用的，不是给人用的）。

═══════════════════════════════════════════════════════════════════════════
为什么要有这个文件
═══════════════════════════════════════════════════════════════════════════
模型要跑引擎，只有一条路：`python -m ask "…"`。它有两个问题：

  ① 输出是 6000+ 字中文简报 —— 模型得从一堆排版文字里挑数字，**又费 token 又容易看错**
  ② **溯源防线没法调用** —— `judge/provenance.py` 写好了，但模型没有方便的入口

本文件就是补这两条的：

    python -m stockctl check  ← ★ 模型在发出回答前，自己跑一遍这个
    python -m stockctl ask --json
    python -m stockctl tieout --json
    python -m stockctl selftest

★★ 纪律（写在代码里，不写在提示词里）：
    · `check` 的退出码 **非 0 表示有数字溯源不过** —— 模型必须处理，不许忽略
    · `--json` 只输出机器可读结构，**不做任何解释性文字**
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def _read(path_or_dash: str) -> str:
    """`-` 表示从 stdin 读。"""
    if path_or_dash == "-":
        return sys.stdin.read()
    with open(path_or_dash, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def cmd_check(args) -> int:
    """★ 数字溯源闸门。**退出码非 0 = 有数字在引擎输出里找不到 = 不许发出。**"""
    from judge import provenance as PV
    answer = _read(args.answer)
    source = _read(args.source)
    result = PV.check(answer, source)
    if args.json:
        print(json.dumps({
            "total": result["total"],
            "ok": result["ok"],
            "missing": [{"raw": m.raw, "value": m.value, "context": m.context,
                         "nearest_in_source": result.get("nearest", {}).get(m.raw)}
                        for m in result["missing"]],
            "pass": not result["missing"],
            "hint": ("逐个改掉上面这些数：能用源里的值就用源里的值；"
                     "要四舍五入就**四舍五入对**（源 1293.5247 写 1293 是错的，该写 1294）。"
                     if result["missing"] else ""),
        }, ensure_ascii=False, indent=1))
    else:
        print(PV.render(result))
    return EXIT_OK if not result["missing"] else EXIT_FAIL


def _brief_to_dict(b) -> dict:
    """把 Brief 抽成机器可读结构。**只搬运，不解释。**"""
    def fact(f):
        return {"label": f.label, "value": f.value, "unit": f.unit,
                "caliber": f.caliber, "as_of": f.as_of, "source": f.source,
                "eid": f.eid, "path": f.path, "origin": f.origin,
                "note": getattr(f, "note", "")}
    out = {
        "question": getattr(b.q, "qtype", "") + " | " + str(getattr(b.q, "code", "")),
        "asked": getattr(b, "asked", ""),
        "facts": [fact(f) for f in b.facts],
        "gaps": list(b.gaps),
        "notices": list(getattr(b, "notices", [])),
    }
    if getattr(b, "market", None):
        out["market"] = dict(b.market)
    if getattr(b, "bundle", None):
        out["computed"] = {k: v for k, v in b.bundle.items() if not k.startswith("_")}
    c = getattr(b, "claim", None)
    if c is not None:
        out["claim"] = {"direction": getattr(c, "direction", ""),
                        "confidence": getattr(c, "confidence", None),
                        "caliber": getattr(c, "caliber", ""),
                        "flags": dict(getattr(c, "flags", {})),
                        "invalidators": list(getattr(c, "invalidators", []))}
    ab = getattr(b, "ablation", None)
    if ab is not None:
        out["ablation"] = {"state": getattr(ab, "state", ""),
                           "flip_count": getattr(ab, "flip_count", None),
                           "n_usable": getattr(ab, "n_usable", None)}
    tg = getattr(b, "tieout", None)
    if tg:
        out["tieout"] = [{"rule": r.rule_id, "verdict": r.verdict,
                          "residual": r.residual, "tolerance": r.tolerance,
                          "can_fail": r.can_fail, "gate_met": r.gate_met,
                          "fp_upper": r.false_positive_upper,
                          "note": r.note} for r in tg]
    # ★★★ 2026-10-13 补：**踩雷排查**和**事件雷达**原来只在文字版里渲染，
    #   `_brief_to_dict` 把它们漏掉了 —— 于是模型读到的 JSON 里**根本没有这张清单**。
    #   而"踩雷排查"恰恰是这个产品最核心的东西（见 ask.py::_collect_minefield 的注释：
    #   "它解决的是最冤的一类亏损：踩到本来就公开披露、只是没去看的雷"）。
    #   **算了却不送出去 = 等于没算。**（实测：用户问一只票，回答里一个雷字都没有。）
    mf = getattr(b, "minefield", None)
    if mf is not None:
        out["minefield"] = {
            "命中": mf.n_hit, "未发现": mf.n_clear, "无法判定": mf.n_unknown,
            "checks": [{"id": r.id, "name": r.name, "verdict": r.verdict,
                        "detail": r.detail, "source": r.source,
                        "citation": r.citation, "threshold": r.threshold}
                       for r in mf.results],
            "边界": ["「未发现」≠「没有」，「无法判定」≠「没问题」（没查就是没查）",
                     "覆盖不了：财务造假、大股东掏空、行业周期反转、政策突变"],
        }
    ev = getattr(b, "events", None)
    if ev is not None:
        out["events"] = {
            "件数": len(ev.events), "没取到的类别": list(ev.missed),
            "events": [{"id": e.id, "name": e.name, "日期": e.date,
                        "方向仅用于分组": e.direction, "detail": e.detail,
                        "source": e.source} for e in ev.events],
            "边界": "只报告「这里发生了不寻常的事」，**不判断是机会还是陷阱**",
        }
    return out


def _auto_observe(question: str, resolved: str, outcome: str, reason: str = "") -> None:
    """★ 自动记一条观察。

    **为什么要自动**：观察层是"从真实使用里长大"的唯一素材源，
    但如果靠模型自觉去调 `observe`，**模型忘了就没数据** ——
    那这一层就等于没建。放在 `ask` 里 = **记录变成免费的**。

    ⚠️ 记录失败**绝不**影响主流程（try 住），但它会静默 —— 所以下面留了
       `STOCK_ADVISOR_DEBUG` 开关，需要排查时打开。
    """
    try:
        from advisor import observe as O
        user = os.environ.get("STOCK_ADVISOR_USER", "default")
        sess = os.environ.get("STOCK_ADVISOR_SESSION") or f"auto-{user}"
        # ★ 原话一字不改地记 —— 这是纪律
        O.Observer(sess).record(question, resolved=resolved,
                                outcome=outcome, reason=reason)
    except Exception as e:                                   # noqa: BLE001
        if os.environ.get("STOCK_ADVISOR_DEBUG"):
            print(f"[observe 记录失败，不影响本次回答] {type(e).__name__}: {e}",
                  file=sys.stderr)


def cmd_ask(args) -> int:
    """跑引擎。`--json` 给结构化结果（省 token），默认给人看的简报。

    ★★ 每次调用**自动记一条观察**（除非 `--no-observe`）——
       人是没法指望每次都记得手动记的。
    """
    import ask as A
    import contextlib
    import io as _io
    # ★★★ stdout 必须是纯的（2026-10-13 修）。
    #   实测：`baostock` 会往 stdout 打 `login success!` / `logout success!`，
    #   于是 `--json` 输出**第一行不是 `{`** → **模型直接 JSON 解析失败**。
    #   → 把整个分析过程包在 redirect_stdout 里，**噪声全部转去 stderr**，
    #     只有最终 payload 走 stdout。
    _buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(_buf):
            brief = A.analyze(args.question)
    except A.AskError as e:
        if args.json:
            print(json.dumps({"refused": True, "reason": str(e)}, ensure_ascii=False))
        else:
            print(f"【拒答】{e}")
        if not args.no_observe:
            # ★ 拒答也要记 —— **拒答就是设计缺口的第一手证据**
            _auto_observe(args.question, "", _refusal_kind(str(e)), str(e))
        return EXIT_FAIL
    finally:
        _noise = _buf.getvalue()
        if _noise.strip():
            sys.stderr.write(_noise)   # ★ 第三方库噪声 → stderr
    if not args.no_observe:
        q = getattr(brief, "q", None)
        resolved = f"{getattr(q,'qtype','')}|{getattr(q,'code','') or ''}".strip("|")
        _auto_observe(args.question, resolved, "answered")
    if args.json:
        print(json.dumps(_brief_to_dict(brief), ensure_ascii=False, indent=1))
    else:
        print(brief.render())
    return EXIT_OK


def _refusal_kind(reason: str) -> str:
    """把拒答原因归到观察层的结局枚举里。**分类是为了统计，不是为了评价用户。**"""
    r = reason or ""
    if "不是个股" in r or "不是一只" in r:
        return "refused_not_stock"
    if "对比" in r:
        return "refused_comparison"
    if "消息" in r or "新闻" in r:
        return "refused_news"
    return "refused_unrecognized"


def cmd_tieout(args) -> int:
    """只跑三表勾稽（快，不取行情/财务）。"""
    from compute import tieout as TO
    mk = "SH" if args.code.startswith("6") else "SZ"
    try:
        results = TO.run_all(f"{mk}{args.code}", args.period,
                             prev_period=args.prev or "")
    except Exception as e:                                   # noqa: BLE001
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
              if args.json else f"勾稽未跑：{type(e).__name__}: {e}")
        return EXIT_FAIL
    if args.json:
        print(json.dumps([{"rule": r.rule_id, "verdict": r.verdict,
                           "residual": r.residual, "tolerance": r.tolerance,
                           "can_fail": r.can_fail, "gate_met": r.gate_met,
                           "fp_upper": r.false_positive_upper,
                           "n_tested": r.n_tested, "n_flagged": r.n_flagged,
                           "note": r.note} for r in results],
                          ensure_ascii=False, indent=1))
    else:
        for r in results:
            print(r.render())
    return EXIT_OK


def cmd_profile(args) -> int:
    """看用户画像；`--plan` 只吐呈现计划（给模型用）。

    ★ 呈现计划里**只有顺序和用词**，没有数值 —— 这是那堵墙的出口。
    """
    from advisor import profile as P
    prof = P.load(args.user)
    if args.plan:
        pl = prof.plan()
        print(json.dumps({
            "user_id": prof.user_id,
            "section_order": list(pl.section_order),
            "metric_priority": list(pl.metric_priority),
            "term_glossary": pl.term_glossary,
            "verbosity": pl.verbosity,
            "strategy": prof.strategy,
            "risk_tolerance": prof.risk_tolerance,
            "watchlist": prof.watchlist,
            "note": "★ 以上只影响「先说哪条」与「用哪个词」，不影响任何数值",
        }, ensure_ascii=False, indent=1))
    else:
        print(prof.render())
    return EXIT_OK


def cmd_observe(args) -> int:
    """观察层：记一次提问，或看汇总报告。

    ★ **原话一字不改地记。** 它是将来重建规格的唯一可靠依据。
    """
    from advisor import observe as O
    if args.report:
        print(O.summarize(args.session))
        return EXIT_OK
    if not args.raw:
        print("要给 --raw 「用户原话」，或者用 --report 看汇总", file=sys.stderr)
        return EXIT_USAGE
    ob = O.Observer(args.session)
    o = ob.record(args.raw, resolved=args.resolved or "",
                  outcome=args.outcome or "answered", reason=args.reason or "")
    print(json.dumps({"seq": o.seq, "raw": o.raw, "outcome": o.outcome},
                     ensure_ascii=False))
    return EXIT_OK


def cmd_warm(args) -> int:
    """★ 预热「全市场共用」的缓存。

    **为什么需要**：实测冷启动 **199 秒**、热启动 **4 秒**。
    冷启动里有一大块是「一份数据服务全市场」的：
        · 横截面（全市场 5562 只 clist）  ~11s
        · ST 名单（翻 60 页 clist）        ~24s
    这两样**跟问哪只票无关**。先取好，之后任何一只票的第一次提问都能省掉它们。

    ★ 它**只预热、不下结论**，取不到也不报错（只如实说哪一项没成）。
    """
    import time as _t
    ok, bad = [], []
    from compute import xsection as _XS
    from data import adapters as _AD
    # ★★★ `universe`（全市场名单，通达信封 52k 行）**必须在这里预热**。
    #   实测：它**只用来把「五粮液」这种名字翻译成代码**，却占了整个冷启动
    #   **97 秒 / 100 秒**（profiler 实测：_universe 97.2s）——
    #   而它同样是「一份数据服务全市场」，缓存 1 天。
    #   → 不进 warm 的话，每天第一次问任何票都要先等 97 秒。
    from ask import _universe as _UNI
    for label, fn in (("全市场名单（名字→代码用）", _UNI),
                      ("横截面（全市场）", _XS.load),
                      ("ST 名单（全市场）", _AD.st_snapshot)):
        t0 = _t.time()
        try:
            fn()
            ok.append(f"{label} {_t.time()-t0:.1f}s")
        except Exception as e:                                   # noqa: BLE001
            bad.append(f"{label} 失败：{type(e).__name__}: {str(e)[:50]}")
    if args.json:
        print(json.dumps({"warmed": ok, "failed": bad}, ensure_ascii=False))
    else:
        for s2 in ok:
            print(f"  ✓ 已预热 {s2}")
        for s2 in bad:
            print(f"  ✗ {s2}")
        print("  → 之后单只票的第一次提问会快很多（全市场那部分已就绪）")
    return EXIT_OK


def cmd_selftest(args) -> int:
    """跑全量自测。**改过任何代码之后必须跑这个。**"""
    r = subprocess.run([sys.executable, "-m", "run_selftests"],
                       cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
    print(tail)
    if r.returncode != 0:
        print("❌ 自测未全绿 —— 不许说「已修好」")
    return r.returncode


def main() -> int:
    p = argparse.ArgumentParser(prog="stockctl", description="A 股参谋引擎的命令行工具面")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="★ 数字溯源闸门：回答里的数字必须在引擎输出里能找到")
    c.add_argument("--answer", required=True, help="回答文本文件，`-` 表示 stdin")
    c.add_argument("--source", required=True, help="引擎输出文件，`-` 表示 stdin")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)

    a = sub.add_parser("ask", help="跑引擎")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    a.add_argument("--no-observe", action="store_true",
                   help="跳过自动记录（默认每次调用都自动记一条）")
    a.set_defaults(fn=cmd_ask)

    t = sub.add_parser("tieout", help="只跑三表勾稽")
    t.add_argument("code")
    t.add_argument("--period", default="2025-12-31")
    t.add_argument("--prev", default="2024-12-31")
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_tieout)

    s = sub.add_parser("selftest", help="跑全量自测")
    s.set_defaults(fn=cmd_selftest)

    w = sub.add_parser("warm", help="★ 预热全市场共用的缓存（第一次用之前跑一次）")
    w.add_argument("--json", action="store_true")
    w.set_defaults(fn=cmd_warm)

    f = sub.add_parser("profile", help="看用户画像；--plan 吐呈现计划")
    f.add_argument("--user", default="default")
    f.add_argument("--plan", action="store_true", help="只输出呈现计划（JSON）")
    f.set_defaults(fn=cmd_profile)

    o = sub.add_parser("observe", help="★ 观察层：记原话 / 看汇总")
    o.add_argument("--session", default="default")
    o.add_argument("--raw", help="用户原话（一字不改地记）")
    o.add_argument("--resolved", help="理解成了什么")
    o.add_argument("--outcome", help="answered / refused_* / error / abandoned")
    o.add_argument("--reason", help="失败或拒答的原因")
    o.add_argument("--report", action="store_true", help="输出汇总报告")
    o.set_defaults(fn=cmd_observe)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
