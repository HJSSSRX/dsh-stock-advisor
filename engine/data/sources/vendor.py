# -*- coding: utf-8 -*-
"""vendor 源 —— 把 a-stock-data 包装成 registry 能认的一个「源」。

═══════════════════════════════════════════════════════════════════
为什么要有这个薄壳
═══════════════════════════════════════════════════════════════════
registry 的源函数约定是 `fn(code, **params) -> (data, as_of, caliber)`，
而 a-stock-data 返回 DataFrame / dict。本文件把两者对上，
这样 `ROUTES` 里就能把 `("vendor", "xxx")` 排在 `("eastmoney", "xxx")` 前面。

═══════════════════════════════════════════════════════════════════
★★★ 为什么不写 37 个包装函数（用 PEP 562 模块级 __getattr__）
═══════════════════════════════════════════════════════════════════
registry 取源函数的方式是 `func = getattr(module, fn_name)`。
所以在模块级实现 `__getattr__(name)`，**按 kind 名动态生成一个闭包**就够了 ——
绑定表（BINDINGS）加一条，这里自动可用，不需要同步改两个地方。

    ROUTES["dragon_tiger"] = [("vendor", "dragon_tiger"), …]
             ↓ getattr(vendor_module, "dragon_tiger")
          __getattr__ 生成 (code, **kw) → _adapt("dragon_tiger", …)

⚠️ 只在 `name in BINDINGS` 时生成；否则抛 AttributeError（**不静默返回 None**，
   否则拼错函数名会变成「取到 None 再调用」的怪错）。

═══════════════════════════════════════════════════════════════════
★★★ 源优先级（2026-10-12 实测，本轮最重要的修正）★★★
═══════════════════════════════════════════════════════════════════
我们原来的 ROUTES：**24 个 kind 里 23 个首选东财**。

a-stock-data 的实测结论（人家踩出来的）：
    1（首选）  腾讯财经      —— **不封 IP**（K线单入口约 600 次后限流，三入口轮换）
    2          交易所官方     —— 极低风险
    3          新浪/巨潮/同花顺
    4          mootdx
    5（仅独有数据才用） **东财** —— **有风控，会封 IP**
    「凡是行情 / K线 / 实时价 / 市值 / 财务三表能从腾讯、交易所官方或新浪
      拿到的，**一律走它们**」

实测（关缓存，各取 5 次同一份日K）：
    东财  平均 1256ms   逐次 119 / 1277 / 1771 / 1594 / 1521   ← 波动大
    腾讯  平均  458ms   逐次 703 /  250 /  414 /  454 /  467   ← 稳定
    → 腾讯快 2.7 倍，而且**不封 IP**

**本会话直证**：连打 37 个请求后
    [WARN] push2 资金流请求失败: RemoteDisconnected('Remote end closed connection without response')
    [WARN] 东财人气榜失败:     RemoteDisconnected(...)

⚠️ 保留东财为**备胎**而不是删掉：它有几项是别处拿不到的。
"""
from __future__ import annotations

from typing import Any, Callable

from ..vendor_bridge import BINDINGS, fetch_via_vendor
from .http import FetchError, NoDataInWindow

NAME = "vendor"


def _adapt(kind: str, code: str, params: dict) -> tuple[Any, str, str]:
    """调桥 → 翻译成 registry 的源约定；失败时抛它认得的异常。"""
    res = fetch_via_vendor(kind, code, params)
    if not res.ok:
        # ★ 「确实没有」与「取不到」必须分开 —— registry 据此决定 error_kind
        if res.error_kind == "no_data_in_window":
            raise NoDataInWindow(res.error or "该标的确实没有这类数据")
        raise FetchError(f"[vendor/{kind}] {res.error or '取数失败'}")
    return res.data, res.as_of, res.caliber


def make_source_fn(kind: str) -> Callable[..., tuple[Any, str, str]]:
    """按 kind 生成 registry 认的源函数。"""
    def fn(code: str = "", **params: Any) -> tuple[Any, str, str]:
        return _adapt(kind, code, params)

    fn.__name__ = kind
    fn.__qualname__ = f"vendor.{kind}"
    fn.__doc__ = (f"a-stock-data 的 {BINDINGS[kind].fn} → 我们的 Result 契约。\n\n"
                  f"{BINDINGS[kind].caliber}")
    return fn


def __getattr__(name: str) -> Callable[..., tuple[Any, str, str]]:
    """PEP 562 —— registry 用 getattr 取源函数，这里按 kind 动态生成。

    ★ 这个设计的好处：**绑定表加一条，这里自动可用**，
      不需要维护两份清单（少一处能写错的地方）。
    """
    if name in BINDINGS:
        return make_source_fn(name)
    raise AttributeError(
        f"vendor 源没有 {name} 这个端点。已绑定 {len(BINDINGS)} 个："
        f"{', '.join(sorted(BINDINGS))}"
    )


def __dir__() -> list[str]:
    return sorted(BINDINGS)
