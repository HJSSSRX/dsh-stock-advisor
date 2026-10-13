# -*- coding: utf-8 -*-
"""唯一取数口 —— 模型只能通过这里拿数据。

职责：
  路由（按数据类型选源链） → 重试（指数退避） → 熔断（连续失败停用） → 缓存（TTL 分级） → 统一返回 Result

三条纪律：
  1. 失败也是值：全源挂掉返回 ok=False + 每个源的尝试记录，**绝不返回空表或 0**
  2. 降级要留痕：用了备源就标 fallback_used=True（上层会告知用户）
  3. 熔断不是可选项：实测本机网络有 ~16 秒的抖动窗口，没有熔断会把抖动放大成雪崩
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Optional

from . import cache
from .result import Result, fail
from .sources import SOURCES
from .sources.http import FetchError

# ── 路由表：每种数据类型的主源 → 备源链 ──────────────────────────────
#   条目格式：(源名, 函数名) 或 (源名, 函数名, 固定参数)
ROUTES: dict[str, list] = {
    # 行情类
    # ★ 源优先级：腾讯优先、东财降为备胎（2026-10-12 实测）
    #   东财是唯一会封 IP 的源，而行情/K线本来就能从腾讯拿
    #   实测（关缓存）：腾讯 458ms vs 东财 1256ms，且腾讯不封 IP
    "quote":        [("vendor", "quote"), ("eastmoney", "quote"),
                     ("tencent", "quote"), ("sina", "quote")],
    "index":        [("eastmoney", "index_quote")],
    # ★ 指数日K —— 必须用**独立 kind**，不能复用 "kline"：
    #   000001 当股票是平安银行、当指数是上证指数，靠代码分不出来。
    #   这里用 ROUTES 的 3 元组把 is_index=True 固定带上。
    # ⛔ **不要**把 index_kline 接到 vendor —— 实测踩到（2026-10-12）：
    #   他们 tencent_kline 的前缀白名单是
    #       SH_INDEX = {"000300","000905","000016","000688","000852","000010"}
    #   **000001（上证指数）不在里面** → 落到 sz000001 = **平安银行**（11.76），
    #   而且**不会报错**。腾讯 K 线做不了上证指数。
    #   → 指数走我们自己的东财 index 路由（caliber.index_secid 有 1.000001 的正确拼法）。
    #   我们加过断言：指数收盘必须在千点量级，就是这条断言当场抓住了回退。
    # ★★ 指数必须有非东财的腿 —— 2026-10-12 实测：东财被限流时指数取不到，
    #    **整个风险模块（波动率/回撤/Beta）连带 15 个测试全挂**。
    #    腾讯/新浪都不封 IP，所以放前面；东财退到第三。
    #    ⚠️ 东财那条**不能直接删** —— 它带 {"is_index": True} 才不走"平安银行"的坑。
    "index_kline":  [("akshare", "index_kline_tx"),
                     ("akshare", "index_kline_sina"),
                     ("eastmoney", "kline", {"is_index": True})],
    "kline":        [("vendor", "kline"), ("eastmoney", "kline")],
    "fundflow":     [("vendor", "fundflow"), ("eastmoney", "fundflow")],
    # ⛔ 东财 clist 被封（三家独立项目实测均挂）→ **首选换同花顺**
    "board":        [("akshare", "industry_board_ths"), ("eastmoney", "board_list")],
    "board_members": [("eastmoney", "board_members")],
    # ⛔ 东财的 clist 接口**被单独封了**（实测：同 host 的行情/K线都通，clist 秒断 HTTP 000）
    #    → 全市场列表改用通达信封（它含全部股票且带成交额）
    #    ⚠️ 但通达信封**只有当日**，历史全市场列表仍要用东财（若它恢复）
    "universe":     [("vendor", "tdx_package")],   # ⚠️ vendor 源的 kind 必须是**桥绑定名**
    # 公告（官方优先）
    "announcement": [("cninfo", "announcement"), ("eastmoney", "announcement")],
    "dividend":     [("emdc", "dividend")],
    # 财务（业绩报表走 push2 家族，其余走数据中心）
    "financial":    [("eastmoney", "financial")],
    "financial_income":   [("emdc", "report", {"kind": "financial_income"})],
    "financial_balance":  [("emdc", "report", {"kind": "financial_balance"})],
    "financial_cashflow": [("emdc", "report", {"kind": "financial_cashflow"})],
    "quarterly_metrics":  [("emdc", "report", {"kind": "quarterly_metrics"})],
    # 股东与内部人
    "holder_num":         [("vendor", "holder_num"), ("emdc", "report", {"kind": "holder_num"})],
    "top_free_holders":   [("emdc", "report", {"kind": "top_free_holders"})],
    "executive_trades":   [("emdc", "report", {"kind": "executive_trades"})],
    # 事件类
    "org_survey":         [("emdc", "report", {"kind": "org_survey"})],
    "block_trades":       [("vendor", "block_trades"), ("emdc", "report", {"kind": "block_trades"})],
    "restricted_release": [("vendor", "restricted_release"), ("emdc", "report", {"kind": "restricted_release"})],
    "forecast":           [("emdc", "report", {"kind": "forecast"})],
    # 行业与宏观
    "industry_valuation": [("emdc", "industry_valuation")],
    "macro":              [("emdc", "macro")],
    # ★ 情报通道（永不进结论）
    "news":               [("news", "stock_news")],
    # ══════════════════════════════════════════════════════════════
    # 以下由 a-stock-data 提供（2026-10-12 接线）
    #   ★ 源优先级按人家实测：腾讯/交易所官方/新浪 → 东财仅用于其独有数据
    #   ★ 全部经 data/vendor_bridge.py 转成我们的 Result（带 eid/source/as_of）
    # ══════════════════════════════════════════════════════════════
    # 资金与筹码
    "fundflow_minute":        [("vendor", "fundflow_minute")],
    "dragon_tiger":           [("vendor", "dragon_tiger")],
    "margin_trading":         [("vendor", "margin_trading")],
    "equity_pledge":          [("vendor", "equity_pledge")],
    "share_buyback":          [("vendor", "share_buyback")],
    "holder_trades":          [("vendor", "holder_trades")],
    # 估值与财务
    "valuation_history":      [("vendor", "valuation_history")],
    "valuation_now":          [("vendor", "valuation_now")],
    "eps_forecast":           [("vendor", "eps_forecast")],
    "earnings_forecast":      [("vendor", "earnings_forecast")],
    "financial_sina":         [("vendor", "financial_sina")],
    # 行业与板块
    "concept_blocks":         [("akshare", "concept_board_ths"), ("vendor", "concept_blocks")],
    # ★ 巨潮行业分类 —— **法定披露级**，比东财/同花顺的自家分类更硬
    "industry_cninfo":        [("akshare", "industry_cninfo")],
    "industry_pe_cninfo":     [("akshare", "industry_pe_cninfo")],
    "company_profile":        [("akshare", "company_profile_cninfo")],
    "sw_industry":            [("vendor", "sw_industry")],
    "industry_board":         [("akshare", "industry_board_ths"), ("vendor", "industry_board")],
    # 事件与风险
    "st_list":                [("vendor", "st_list")],
    "price_anomaly":          [("vendor", "price_anomaly")],
    "stock_monitor":          [("vendor", "stock_monitor")],
    "ipo_calendar":           [("vendor", "ipo_calendar")],
    "convertible_bonds":      [("vendor", "convertible_bonds")],
    # 舆情与互动
    "interactive_qa":         [("vendor", "interactive_qa")],
    "hot_rank":               [("vendor", "hot_rank")],
    "institution_survey":     [("vendor", "institution_survey")],
    "research_reports":       [("vendor", "research_reports")],
    "stock_info":             [("vendor", "stock_info")],
    # 宏观（官方一手）
    "macro_pmi":              [("vendor", "macro_pmi")],
    "macro_lpr":              [("vendor", "macro_lpr")],
    "macro_social_financing": [("vendor", "macro_social_financing")],
    "yield_curve":            [("vendor", "yield_curve")],
    # 新闻（政策分析师的原料）
    "cctv_news":              [("vendor", "cctv_news")],
    # ★ 舆情 / 社会风向（2026-10-12 补齐）
    #   ⚠️ **这一整组都是情报通道，永不进结论** —— 观点不是事实
    "news_flash":             [("vendor", "news_flash")],
    "news_global":            [("vendor", "news_global")],
    "news_wallstreetcn":      [("vendor", "news_wallstreetcn")],
    "hot_list":               [("vendor", "hot_list")],
    "limit_up_reason":        [("vendor", "limit_up_reason")],
    "limit_up_sentiment":     [("vendor", "limit_up_sentiment")],
    "industry_reports":       [("vendor", "industry_reports")],
    "research_sina":          [("vendor", "research_sina")],
    "hot_concept":            [("vendor", "hot_concept")],
    "irm_sz":                 [("vendor", "irm_sz")],
}

# ── 熔断状态 ────────────────────────────────────────────────────────
BREAKER_THRESHOLD = 3        # 连续失败几次开始熔断
BREAKER_COOLDOWN = 900       # 熔断时长（秒）= 15 分钟
_BREAKER: dict[str, dict] = {}
_HEALTH: dict[str, dict] = {}    # 源健康度统计（成功/失败/平均耗时）

# ★ 坑（实测）：熔断与健康度如果只放进程内存，CLI 每次新进程就归零，
#   等于熔断从来没生效过。必须落盘。
_HEALTH_FILE = os.path.join(cache.CACHE_DIR, "_health.json")


def _load_state() -> None:
    global _BREAKER, _HEALTH
    try:
        with open(_HEALTH_FILE, "r", encoding="utf-8") as f:
            blob = json.load(f)
        _BREAKER = blob.get("breaker", {}) or {}
        _HEALTH = blob.get("health", {}) or {}
    except Exception:                                        # noqa: BLE001
        pass


def _save_state() -> None:
    os.makedirs(cache.CACHE_DIR, exist_ok=True)
    tmp = _HEALTH_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"health": _HEALTH, "breaker": _BREAKER}, f, ensure_ascii=False)
        os.replace(tmp, _HEALTH_FILE)
    except Exception:                                        # noqa: BLE001
        pass


_load_state()

# ── 重试 ────────────────────────────────────────────────────────────
RETRY_PER_SOURCE = 2
BACKOFF = (0.5, 1.5)


def _breaker_open(src: str) -> bool:
    st = _BREAKER.get(src)
    return bool(st and st.get("until", 0) > time.time())


def _mark(src: str, ok: bool, ms: int = 0) -> None:
    h = _HEALTH.setdefault(src, {"ok": 0, "fail": 0, "ms_total": 0, "ms_n": 0})
    if ok:
        h["ok"] += 1
        if ms:
            h["ms_total"] = h.get("ms_total", 0) + ms
            h["ms_n"] = h.get("ms_n", 0) + 1
        _BREAKER.pop(src, None)          # 成功即复位
    else:
        h["fail"] += 1
        st = _BREAKER.setdefault(src, {"fails": 0, "until": 0})
        st["fails"] = st.get("fails", 0) + 1
        if st["fails"] >= BREAKER_THRESHOLD:
            st["until"] = time.time() + BREAKER_COOLDOWN
    _save_state()


def health() -> dict:
    """各源健康度（落盘，跨进程可见）。"""
    _load_state()
    out = {}
    for name in SOURCES:
        h = dict(_HEALTH.get(name, {"ok": 0, "fail": 0}))
        n = h.pop("ms_n", 0)
        tot = h.pop("ms_total", 0)
        h["平均耗时ms"] = int(tot / n) if n else 0
        st = _BREAKER.get(name)
        if st and st.get("until", 0) > time.time():
            h["熔断剩余秒"] = int(st["until"] - time.time())
        tot_calls = h.get("ok", 0) + h.get("fail", 0)
        h["成功率"] = f"{h.get('ok', 0) / tot_calls * 100:.0f}%" if tot_calls else "-"
        out[name] = h
    return out


def reset_health() -> None:
    global _BREAKER, _HEALTH
    _BREAKER, _HEALTH = {}, {}
    _save_state()


def fetch(
    kind: str,
    code: str = "",
    params: Optional[dict] = None,
    use_cache: bool = True,
    cache_ttl: Optional[int] = None,
    force_source: Optional[str] = None,
) -> Result:
    """统一取数入口。

    kind   : 见 result.KINDS
    code   : 股票代码（指数用 000001 等）
    params : 传给源函数的额外参数（如 freq/fqt/limit）
    """
    if kind not in ROUTES:
        return fail(kind, code, f"未知数据类型：{kind}（支持：{', '.join(ROUTES)}）")

    params = dict(params or {})
    cache_params = {"code": code, **params}

    # ① 缓存
    if use_cache:
        hit = cache.get(kind, cache_params, ttl=cache_ttl)
        if hit:
            return Result(
                ok=True, kind=kind, code=code, data=hit["payload"],
                source=hit["source"] + "(缓存)", as_of=hit.get("as_of", ""),
                caliber=hit.get("caliber", ""), cached=True,
            )

    # ② 逐源尝试
    chain = [e for e in ROUTES[kind] if force_source is None or e[0] == force_source]
    attempts: list[dict] = []

    for entry in chain:
        src_name, func_name = entry[0], entry[1]
        fixed = entry[2] if len(entry) > 2 else {}
        if _breaker_open(src_name):
            attempts.append({"source": src_name, "ok": False,
                             "note": "熔断中（连续失败已达阈值），本轮跳过"})
            continue

        mod = SOURCES.get(src_name)
        if mod is None:
            attempts.append({"source": src_name, "ok": False, "note": "源模块未加载"})
            continue
        func = getattr(mod, func_name, None)
        if func is None:
            attempts.append({"source": src_name, "ok": False,
                             "note": f"源不支持 {func_name}"})
            continue

        last_err = ""
        last_kind = "source_error"
        for i in range(RETRY_PER_SOURCE + 1):
            t0 = time.time()
            try:
                call_kwargs = dict(fixed)
                call_kwargs.update({k: v for k, v in params.items() if not k.startswith("_")})
                data, as_of, caliber = func(code, **call_kwargs)
                ms = int((time.time() - t0) * 1000)
                attempts.append({"source": src_name, "ok": True, "ms": ms})
                _mark(src_name, True, ms)
                if use_cache:
                    cache.put(kind, cache_params, data, src_name, as_of, caliber)
                return Result(
                    ok=True, kind=kind, code=code, data=data,
                    source=f"{src_name}.{func_name}", as_of=as_of, caliber=caliber,
                    fallback_used=(src_name != chain[0][0]),
                    attempts=attempts,
                )
            except FetchError as e:
                last_err = str(e)
                last_kind = getattr(e, "kind", "source_error")
                # ★ "确实没有记录"不是故障 —— 重试它毫无意义，直接判定并跳出
                if last_kind == "no_data_in_window":
                    attempts.append({"source": src_name, "ok": False,
                                     "note": f"确实无记录：{last_err}", "kind": last_kind})
                    _mark(src_name, True)          # 源本身是好的，不算它的失败
                    return Result(ok=False, kind=kind, code=code,
                                  source=f"{src_name}.{func_name}",
                                  error=last_err, attempts=attempts,
                                  error_kind="no_data_in_window")
            except Exception as e:                            # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                last_kind = "source_error"

            if i < RETRY_PER_SOURCE:
                time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])

        ms = int((time.time() - t0) * 1000)
        attempts.append({"source": src_name, "ok": False, "ms": ms,
                         "note": last_err, "kind": last_kind})
        _mark(src_name, False, ms)

    # ③ 全挂 —— ★ 必须区分「限流」与「源故障」：混成一个"失败"会丢掉可重试性信息
    kinds = {a.get("kind") for a in attempts if not a.get("ok")}
    if kinds == {"no_data_in_window"}:
        final_kind = "no_data_in_window"
    elif "rate_limited" in kinds:
        final_kind = "rate_limited"
    elif "not_found" in kinds:
        final_kind = "not_found"
    else:
        final_kind = "source_error"
    # ④ ★★ 全挂 → **过期缓存兜底**（"稳定"的关键一环）
    #    实测动机：东财被限流时指数取不到 → 整个风险模块挂 → 15 个测试失败。
    #    但股票分析不需要秒级实时（财报季度更新 / 日线收盘就定 / 公告小时级），
    #    所以**用旧数据 + 明确标注**远好于**什么都拿不到**。
    #    ⚠️ 但绝不能假装它是新的 —— stale/age_seconds 会一路传到判断层。
    stale = cache.get_stale(kind, cache_params)
    if stale and stale.get("payload") not in (None, [], {}):
        return Result(
            ok=True, kind=kind, code=code,
            data=stale.get("payload"),
            source=f"{stale.get('source', '')}（**缓存**）",
            as_of=stale.get("as_of", ""),
            caliber=(stale.get("caliber", "") or "")
                    + "\n⏰ **这是 {} 小时前的缓存数据**".format(
                        round(float(stale.get("stale_hours", 0)), 1))
                    + "（当前所有源都取不到，原因：{}）——".format(final_kind)
                    + "**判断层必须把时效性写进回答**，不许冒充实时。",
            attempts=attempts, error_kind="",
            stale=True, age_seconds=float(stale.get("age", 0)),
            error="",
        )
    return Result(
        ok=False, kind=kind, code=code,
        error=f"全部 {len(chain)} 个源均失败，且**缓存里也没有**",
        attempts=attempts, error_kind=final_kind,
    )


# ══════════════════════════════════════════════════════════════════
# 命令行入口（便于快速验证）
#   python -m data.registry quote 600519
#   python -m data.registry kline 300417 freq=daily limit=5
#   python -m data.registry health
# ══════════════════════════════════════════════════════════════════
def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        print("用法：python -m data.registry <kind> <code> [key=value ...]")
        print(f"支持：{', '.join(ROUTES)}  |  health  |  cache")
        return 1

    cmd = argv[0]
    if cmd == "health":
        for k, v in health().items():
            print(f"  {k:12s} 成功={v.get('ok', 0):4d} 失败={v.get('fail', 0):4d} "
                  f"{'熔断中 ' + str(v['熔断剩余秒']) + 's' if '熔断剩余秒' in v else ''}")
        return 0
    if cmd == "cache":
        print("缓存：", cache.stats())
        return 0
    if cmd == "reset":
        reset_health()
        print("已重置熔断与健康度统计")
        return 0

    kind = cmd
    code = argv[1] if len(argv) > 1 else ""
    params: dict[str, Any] = {}
    for a in argv[2:]:
        if "=" in a:
            k, v = a.split("=", 1)
            try:
                params[k] = int(v)
            except ValueError:
                params[k] = v

    print(f"→ 取数 {kind} {code} {params or ''}")
    r = fetch(kind, code, params)
    print(f"  ok={r.ok} 源={r.source} 时间={r.as_of} 口径={r.caliber} "
          f"降级={r.fallback_used} 缓存={r.cached} eid={r.eid}")
    if r.attempts:
        for a in r.attempts:
            print(f"    · {a}")
    if r.ok:
        d = r.data
        if isinstance(d, list):
            print(f"  共 {len(d)} 条，前 3 条：")
            for row in d[:3]:
                print("   ", row)
        else:
            print("  ", d)
    else:
        print("  失败原因：", r.why())
    return 0 if r.ok else 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
