# -*- coding: utf-8 -*-
"""巨潮资讯 —— 官方公告源（A 级）。

两步：
  1. 代码 → orgId：POST /new/information/topSearch/query
  2. 查公告：POST /new/hisAnnouncement/query（stock 参数格式："代码,orgId"）
返回里带 PDF 相对路径（adjunctUrl），可拼 http://static.cninfo.com.cn/ 下载原文。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from .http import FetchError, NoDataInWindow, post_json

NAME = "cninfo"
_BASE = "http://www.cninfo.com.cn"
_TIMEOUT = 12

# orgId 缓存（代码 → orgId），进程内即可，变动极少
_ORG_CACHE: dict[str, str] = {}


def _org_id(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code in _ORG_CACHE:
        return _ORG_CACHE[code]
    js = post_json(
        f"{_BASE}/new/information/topSearch/query",
        {"keyWord": code, "maxNum": "10"},
        referer=f"{_BASE}/new/index",
        timeout=_TIMEOUT,
    )
    if not isinstance(js, list) or not js:
        raise FetchError(f"巨潮未找到代码 {code} 的 orgId")
    hit = next((x for x in js if str(x.get("code")) == code), js[0])
    org = hit.get("orgId")
    if not org:
        raise FetchError(f"巨潮返回无 orgId：{hit}")
    _ORG_CACHE[code] = org
    time.sleep(0.3)          # 温和限流：官方站，别打急
    return org


def announcement(code: str, page_size: int = 20, days: int = 365) -> tuple[list, str, str]:
    org = _org_id(code)
    end = datetime.now().strftime("%Y-%m-%d")
    beg = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    col = "sse" if str(code).startswith("6") else "szse"
    js = post_json(
        f"{_BASE}/new/hisAnnouncement/query",
        {
            "pageNum": "1", "pageSize": str(page_size),
            "column": col, "tabName": "fulltext",
            "stock": f"{code},{org}",
            "seDate": f"{beg}~{end}",
        },
        referer=f"{_BASE}/new/commonUrl?url=disclosure/list/notice",
        timeout=_TIMEOUT,
    )
    anns = (js or {}).get("announcements")
    if not anns:
        # ★ 区间内没有披露 = 有信息量的结论（不是故障）
        raise NoDataInWindow(f"该股在 {beg} ~ {end} 区间内没有公告披露")
    rows = [
        {
            "标题": a.get("announcementTitle", "").strip(),
            "日期": datetime.fromtimestamp(a["announcementTime"] / 1000).strftime("%Y-%m-%d")
            if a.get("announcementTime") else "",
            "PDF": f"http://static.cninfo.com.cn/{a.get('adjunctUrl', '')}",
            "公告ID": a.get("announcementId", ""),
        }
        for a in anns
    ]
    return rows, rows[0]["日期"], "交易所官方披露（原始 PDF 可下载）"
