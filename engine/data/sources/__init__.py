# -*- coding: utf-8 -*-
"""数据源集合。

每个源模块统一约定：
    func(code, **kwargs) -> (data, as_of, caliber)
失败抛 FetchError（由 registry 统一捕获并包成 Result）。
"""
from . import (    # noqa: F401
    akshare_src, cninfo, eastmoney, emdc, http, news, sina, tencent, vendor,
)

SOURCES = {
    # ★ vendor = a-stock-data（10,795★ / Apache-2.0 / 34 数据源 / 87 端点）的包装。
    #   排在 eastmoney 前面是**实测结论**（2026-10-12）：
    #     东财是唯一会封 IP 的源，而行情/K线本来就能从腾讯拿。
    #     实测关缓存：腾讯 458ms / 东财 1256ms，且腾讯不封 IP。
    #   详见 sources/vendor.py 的说明。
    "vendor": vendor,         # 首选：腾讯等不封 IP 的源（经 a-stock-data）
    "eastmoney": eastmoney,   # 备胎：行情 / K线 / 资金流 / 板块（push2 家族）
    "emdc": emdc,             # 数据中心报表：三表 / 股东 / 解禁 / 调研 / 大宗 / 宏观
    "tencent": tencent,       # 备源（行情，直连）
    "sina": sina,             # 备源（行情）
    "cninfo": cninfo,         # 官方公告（巨潮）
    "news": news,             # ★ 情报通道：新闻（永不进结论，只进"相关动态"）
    # ★ akshare（22,897★ / MIT）—— **东财 clist 被封后的换源通道**。
    #   实测 2026-10-12：我们自己 / a-stock-data / akshare 三家在东财 clist 上全挂，
    #   但 akshare 的**同花顺**和**巨潮**通道是通的 —— 这就是成熟项目的做法：
    #   不是"能不能用东财"，而是"同一份数据永远准备多个源"。
    "akshare": akshare_src,
}
