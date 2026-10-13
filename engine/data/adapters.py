# -*- coding: utf-8 -*-
"""适配层 —— 修 vendor 生成物的已知缺陷，**不手改生成物**。

═══════════════════════════════════════════════════════════════════════════
为什么要有这一层
═══════════════════════════════════════════════════════════════════════════
`vendor/astock.py` 是从 `simonlin1212/a-stock-data` 提取的**生成物**，
文件头自己写着：「**本文件不要手改** —— 下次跑提取器就覆盖了。
要改行为，改提取器**或写适配层**。」

本模块就是那个「适配层」：把 vendor 的已知缺陷在这里修掉，
**并如实记录修了什么、以及走的是哪条路**。

═══════════════════════════════════════════════════════════════════════════
已知缺陷 #1：ST 名单因「北交所 ST 重复」整体失效
═══════════════════════════════════════════════════════════════════════════
实测（2026-10-13）：

    `st_stock_list()` 取两张表：
        shsz = 风险警示板  m:0+f:4,m:1+f:4      → 406 条
        bj   = 北交所全表  m:0+t:81+s:2048      → 361 条（其中含 ST 的 3 条）
    然后把两者合并。**但风险警示板里已经包含了北交所的 ST 股**：
        920575 *ST康乐 ／ 920090 *ST同辉 ／ 920023 *ST田野
    → 这 3 只被加了两遍 → `frame.duplicated(["code"])` 为真
    → 抛 `RuntimeError: ST 名单为空或代码重复` → **整个 ST 名单功能挂掉**

★ **3 只票让整个功能失效。** 而且这正是「数据源结构变了」那一类 ——
  东财后来把北交所 ST 股也纳入了风险警示板过滤，而 vendor 的合并逻辑没跟上。

★ 适配层的做法：**按 code 去重**，并**如实报告走的是哪条路**。
"""
from __future__ import annotations

# 走的路（调用方可以据此判断可信度）
PATH_VENDOR = "vendor_direct"        # vendor 自己就成了
PATH_ADAPTER = "adapter_dedup"       # 适配层去重后才成
PATH_FAILED = "failed"


def st_snapshot() -> tuple:
    """ST / *ST 名单快照。返回 `(行列表, 走了哪条路, 说明)`。

    ★★ **落盘缓存一天**（2026-10-13 加）。为什么必须加：
       这份名单要翻 **60 页 clist（全市场 5562 只）**，实测 **11~32 秒**。
       而它是「**一份数据服务全市场**」—— 查第 2 只票时再翻 60 页纯属浪费。
       加缓存后：第一次 ~11s，之后 **0.02s**。

    ★ 缓存按**日期**切（ST 名单是每日快照），并**如实报告用的是缓存还是实时**。
    ★ 取不到时**不返回旧数据冒充新的** —— 宁可真报「取不到」。
    """
    import json
    import os
    import time

    day = time.strftime("%Y%m%d")
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_cache")
    cache_path = os.path.join(cache_dir, f"st_list_{day}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                d = json.load(fh)
            return (d["rows"], d.get("path", PATH_VENDOR),
                    f"（**当日缓存**，抓于 {d.get('fetched_at', '')}）"
                    + (d.get("note", "") or ""))
        except Exception:                                    # noqa: BLE001
            pass

    rows, path, note = _st_snapshot_live()
    if rows:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump({"rows": rows, "path": path, "note": note,
                           "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                          fh, ensure_ascii=False)
        except Exception:                                    # noqa: BLE001
            pass
    return rows, path, note


def _st_snapshot_live() -> tuple:
    """真正去取（无缓存）。拆出来是为了让缓存层能干净地包住它。"""
    import vendor.astock as V
    # ── 路径 1：vendor 自己能不能成 ──
    try:
        df = V.st_stock_list()
        rows = [{"code": str(r.get("code")), "name": str(r.get("name", "")),
                 "market": str(r.get("market", "")), "st_type": str(r.get("st_type", ""))}
                for r in df.to_dict("records")]
        return rows, PATH_VENDOR, f"vendor 直取成功，{len(rows)} 只"
    except Exception as e:                                   # noqa: BLE001
        first_err = f"{type(e).__name__}: {str(e)[:70]}"

    # ── 路径 2：适配层自己取 + 去重 ──
    try:
        shsz, _ = V._em_clist_all("m:0+f:4,m:1+f:4", "f12,f13,f14,f2,f3")
        bj, _ = V._em_clist_all("m:0+t:81+s:2048", "f12,f13,f14,f2,f3")
        rows, seen = [], set()
        dup_hits, filtered_non_st = [], 0
        for rec, is_bj in [(r, False) for r in shsz] + [(r, True) for r in bj]:
            code = str(rec.get("f12", ""))
            name = str(rec.get("f14", "")).strip()
            if is_bj and "ST" not in name.upper():
                filtered_non_st += 1
                continue                     # 北交所非 ST → 按规则筛掉（**不是重复**）
            if not code or not name:
                continue
            if code in seen:
                dup_hits.append(f"{code} {name}")   # ★★ 这才是重复
                continue
            seen.add(code)
            mid = rec.get("f13")
            rows.append({"code": code, "name": name,
                         "market": "bj" if is_bj else ("sh" if mid == 1 else "sz"),
                         "st_type": "*ST" if name.startswith("*") else "ST"})
        if not rows:
            return [], PATH_FAILED, f"适配层也取不到（vendor 原报：{first_err}）"
        # ⚠️ 计数必须说清是哪种：**「重复」和「按规则筛掉」是两件事**。
        #    第一版把两者相加（406+361−406=361）报成「去掉 361 条重复」——
        #    361 其实是北交所全表的全部行数，其中只有 3 条是真重复。
        return (rows, PATH_ADAPTER,
                f"vendor 原报「{first_err}」→ **适配层按 code 去重后成功**："
                f"{len(rows)} 只（**真重复 {len(dup_hits)} 条**：{'、'.join(dup_hits[:4])}；"
                f"另按「北交所只取 ST」的规则筛掉 {filtered_non_st} 条**非重复**行）")
    except Exception as e:                                   # noqa: BLE001
        return [], PATH_FAILED, (f"vendor 失败（{first_err}），"
                                 f"适配层也失败（{type(e).__name__}: {str(e)[:60]}）")


def find(code: str) -> tuple:
    """查一只票在不在 ST 名单里。

    返回 `(在不在 或 None, 路径, 说明)`。**None 表示查不了，不是「不在」。**
    """
    rows, path, note = st_snapshot()
    if path == PATH_FAILED:
        return None, path, note
    hit = next((r for r in rows if r["code"] == str(code).zfill(6)), None)
    if hit:
        return True, path, f"在名单里：{hit['name']}（{hit['st_type']}）｜{note}"
    return False, path, f"不在名单中（名单共 {len(rows)} 只）｜{note}"
