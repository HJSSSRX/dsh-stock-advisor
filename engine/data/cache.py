# -*- coding: utf-8 -*-
"""落盘缓存，TTL 分级。

为什么落盘而不是只放内存：
  1. 重启不丢（省请求 = 少被封）
  2. 出问题能回看"当时取到的到底是什么"（配合证据层）
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_cache")

# TTL 分级（秒）
TTL = {
    "quote": 120,          # 实时快照 2 分钟
    "kline": 1800,         # 日线 30 分钟
    "fundflow": 1800,
    "financial": 43200,    # 财务 12 小时
    "announcement": 1800,
    "board": 1800,
    "dividend": 86400,     # 分红送配 24 小时
    "universe": 86400,     # 全市场列表 24 小时
    "index": 120,
    # ★★★ 下面这批是 2026-10-13 补的。
    #   之前它们**不在表里** → 走 `TTL.get(kind, 600)` 的默认值 **600 秒 = 10 分钟**，
    #   于是 10 分钟就过期、重新现拉 —— 实测单次提问被拖到 **190 秒**。
    #   而这些数据本身就是**按日/按期更新**的，10 分钟 TTL 纯属浪费。
    #   改完之后稳态才是真的 3~5 秒。
    "valuation_history": 86400,     # 估值历史（按日）
    "margin_trading": 86400,        # 两融（按日）
    "equity_pledge": 86400,         # 股权质押（按周更新，给 1 天足够）
    "holder_trades": 86400,         # 股东增减持（按次披露）
    "restricted_release": 86400,    # 限售解禁安排
    "earnings_forecast": 86400,     # 业绩预告
    "share_buyback": 86400,         # 回购
    "st_list": 86400,               # ST 名单（每日快照）
    "financial_sina": 43200,        # 新浪三表
    "financial_income": 43200,
    "financial_balance": 43200,
    "financial_cashflow": 43200,
    "company_profile": 86400,       # 公司资料（几乎不变）
    "industry": 86400,
    "dragon_tiger": 86400,          # 龙虎榜（按日）
    "concept_blocks": 86400,        # 概念板块归属（几乎不变）
}


def _key(kind: str, params: dict) -> str:
    basis = kind + "|" + json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _path(kind: str, params: dict) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{kind}_{_key(kind, params)}.json")


def get(kind: str, params: dict, ttl: Optional[int] = None) -> Optional[dict]:
    """命中未过期的缓存则返回 {'payload':..., 'source':..., 'as_of':..., 'ts':...}。"""
    p = _path(kind, params)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        return None
    age = time.time() - blob.get("ts", 0)
    limit = TTL.get(kind, 600) if ttl is None else ttl
    if age > limit:
        return None
    blob["age"] = round(age, 1)
    return blob


def get_stale(kind: str, params: dict) -> Optional[dict]:
    """**不看 TTL**，只要盘上有就返回 —— 取不到数据时的兜底。

    ═══════════════════════════════════════════════════════════════
    ★★ 为什么必须有这个（这是"稳定"的核心）
    ═══════════════════════════════════════════════════════════════
    原来的行为：
        缓存过期 → 去取 → **全部源都失败** → 返回 ok=False
        → 客户**什么都拿不到**（哪怕我们 3 小时前刚刚取到过）
    实测就是这样：东财被限流时，指数取不到 → 整个风险模块挂 →
    连带 15 个测试失败。

    但**股票分析根本不需要秒级实时**：
        · 财报       → 季度更新，**过期一天都无所谓**
        · 行情日线   → 收盘后就定了，**过期几小时没关系**
        · 公告       → 小时级够
    真正需要实时的只有「下单前看现价」—— 而**这个产品不自动下单**。

    → 所以正确行为是：
        **取不到 → 用过期缓存兜底，但明确标注「这是 X 小时前的数据」**
      ★ 关键是**标注**，而不是假装它是实时的。

    调用方拿到 blob 后必须读 `blob['age']`（秒）和 `blob['stale']=True`，
    在给用户的回答里说清数据是什么时候的。
    """
    p = _path(kind, params)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except Exception:
        return None
    age = time.time() - blob.get("ts", 0)
    limit = TTL.get(kind, 600)
    blob["age"] = round(age, 1)
    blob["stale"] = age > limit
    blob["stale_hours"] = round(age / 3600, 2)
    return blob


def put(kind: str, params: dict, payload: Any, source: str, as_of: str = "",
        caliber: str = "") -> None:
    p = _path(kind, params)
    blob = {
        "kind": kind,
        "params": params,
        "payload": payload,
        "source": source,
        "as_of": as_of,
        "caliber": caliber,      # ★ 缓存必须存口径，否则命中时口径会丢失（实测踩到）
        "ts": time.time(),
    }
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False, default=str)
        os.replace(tmp, p)   # 原子替换，防止写一半被读到
    except Exception:
        pass


def stats() -> dict:
    """缓存概况，便于观察命中率。"""
    if not os.path.isdir(CACHE_DIR):
        return {"files": 0, "bytes": 0}
    files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    return {"files": len(files), "bytes": sum(os.path.getsize(f) for f in files)}


def clear(kind: Optional[str] = None) -> int:
    """清缓存（kind=None 清全部）。"""
    if not os.path.isdir(CACHE_DIR):
        return 0
    n = 0
    for f in os.listdir(CACHE_DIR):
        if kind and not f.startswith(kind + "_"):
            continue
        if f.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, f))
            n += 1
    return n
