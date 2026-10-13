# -*- coding: utf-8 -*-
"""新闻 / 资讯源 —— 情报通道的入口。

★★★ 与其它源最本质的区别 ★★★
    这个模块取回来的东西**永远不进"结论"字段**，只能进"相关动态"。
    它必须带三样东西：媒体分级（source_rank）、发布时间、可信度标签。
    理由见 `设计\信息真伪判断-调研.md`：事实与情报混排会毁掉产品的可信度。

实测（2026-10-11）：东财资讯搜索接口可用。
  接口：https://search-api-web.eastmoney.com/search/jsonp
  返回：date / title / content(摘要) / mediaName / url / code

⚠️ 实测发现的噪声问题：同一个查询会返回大量**批量生成的格式化新闻**
   （如"610只个股流通市值不足20亿元"，每天一篇，把个股当数据点列进去）。
   所以本模块把"媒体分级"和"疑似批量稿"标记一起做了 —— 上层据此过滤。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import quote

from .http import FetchError, get_jsonp

NAME = "news"
_TIMEOUT = 15

# ── 媒体分级（五档，编号越小越权威）─────────────────────────────────
#    调研依据：NewsGuard 九条标准改造为 A 股版本；
#    源表必须自建并写明理由（反面教材：MBFC 被批"本质是一个人的意见"）。
#    ★ 这张表是 🧠 推断，必须用自建评测集校准，不要当成定论。
S0 = 0   # 法定披露：可一锤定音，可进结论
S1 = 1   # 官方媒体 / 交易所系媒体
S2 = 2   # 主流财经媒体
S3 = 3   # 门户 / 聚合
S4 = 4   # 自媒体 / 未知来源

MEDIA_RANK: dict[str, int] = {
    # S0
    "交易所": S0, "上交所": S0, "深交所": S0, "证监会": S0, "巨潮资讯": S0,
    # S1 官方/半官方（证券时报、中证报、上证报是三大证券报）
    "证券时报": S1, "证券时报网": S1, "中国证券报": S1, "中国证券报·中证网": S1,
    "中证网": S1, "上海证券报": S1, "证券日报": S1, "经济日报": S1,
    "新华社": S1, "人民日报": S1, "央广网": S1, "央视财经": S1, "中新社": S1,
    # S2 主流财经媒体
    "财联社": S2, "第一财经": S2, "界面新闻": S2, "每日经济新闻": S2,
    "21世纪经济报道": S2, "澎湃新闻": S2, "经济观察报": S2, "财新网": S2,
    "新浪财经": S2, "腾讯财经": S2, "网易财经": S2, "时代周报": S2,
    # S3 门户 / 聚合
    "东方财富": S3, "同花顺": S3, "金融界": S3, "和讯网": S3, "中金在线": S3,
}
RANK_NAME = {S0: "法定披露", S1: "官方媒体", S2: "主流财经", S3: "门户聚合", S4: "自媒体/未知"}

# 可信度标签（给用户看的）
def trust_label(rank: int) -> str:
    return {
        S0: "已核实（法定披露）",
        S1: "官方媒体",
        S2: "主流财经媒体",
        S3: "门户转载·未核实",
        S4: "来源不明·未核实",
    }.get(rank, "来源不明·未核实")


# ── 疑似"批量格式化稿"的识别（实测踩到的噪声）────────────────────────
#    特征：标题以"N只/家/个"开头 + 含"个股/公司"等泛化词 —— 这类稿子是
#    程序按当日数据生成、把成百上千只票当数据点列进去，与个股无关。
_BULK_TITLE = re.compile(
    r"^\s*\d+\s*(只|家|个|份)?.*(个股|公司|股票|基金).*(不足|超过|低于|高于|达到|创|排名)"
)
_GENERIC_HINT = ("名单", "一览", "盘点", "汇总", "榜", "今日", "本周", "上周")
# ★ 实测补的：财经媒体每天会发大量「市场统计稿」，标题里不含任何个股名，
#   但正文把成百上千只票当数据点列进去（如"深沪北百元股数量达192只"）。
#   这类稿件对个股毫无意义，必须识别出来，否则它带着"权威媒体"的身份虚高。
_STAT_NOISE = ("数量达", "只数", "家数", "占比最高", "净流出", "净流入",
               "涨停", "跌停", "涨幅榜", "跌幅榜", "换手率", "成交额",
               "百元股", "低价股", "创历史", "龙虎榜")


def is_bulk_generated(title: str) -> bool:
    """判断是否疑似批量生成的市场统计稿（与个股无关的数据点堆砌）。"""
    t = title or ""
    if _BULK_TITLE.search(t):
        return True
    if any(h in t for h in _GENERIC_HINT) and re.match(r"^\s*\d", t) is not None:
        return True
    # 泛化统计词出现 ≥2 个 → 几乎可以确定是市场统计稿，而不是这家公司的消息
    return sum(1 for k in _STAT_NOISE if k in t) >= 2


def _relevance(title: str, code: str, name: str = "") -> int:
    """粗粒度相关性：标题里直接出现代码或公司名，算强相关。"""
    t = title or ""
    if code and code in t:
        return 2
    if name and name in t:
        return 2
    return 0


def stock_news(code: str, page_size: int = 20, name: str = "") -> tuple[list, str, str]:
    """个股新闻（东财资讯搜索）。

    ★ 返回的每条都带 `媒体分级` / `可信度` / `疑似批量稿` / `相关性`，
      上层必须据此过滤 —— 不要直接把原始列表喂给模型。
    """
    param = {
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "time",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    url = (
        "https://search-api-web.eastmoney.com/search/jsonp?cb=jQuery&param="
        + quote(json.dumps(param, ensure_ascii=False))
    )
    js = get_jsonp(url, referer="https://so.eastmoney.com/", timeout=_TIMEOUT)
    arts = ((js or {}).get("result") or {}).get("cmsArticleWebOld") or []
    if not arts:
        raise FetchError("该股无相关新闻")

    rows = []
    for a in arts:
        title = (a.get("title") or "").strip()
        media = (a.get("mediaName") or "").strip()
        rank = MEDIA_RANK.get(media, S4)
        rel = _relevance(title, code, name)
        bulk = is_bulk_generated(title)
        rows.append({
            "标题": title,
            "时间": (a.get("date") or "")[:19],
            "来源": media,
            "媒体分级": f"S{rank} {RANK_NAME[rank]}",
            "可信度": trust_label(rank),
            "相关性": "强" if rel >= 2 else "弱",
            "疑似批量稿": bulk,
            "摘要": re.sub(r"\s+", " ", (a.get("content") or ""))[:120],
            "链接": a.get("url", ""),
            "id": a.get("code", ""),
        })
    # 排序：强相关优先、非批量稿优先、高权威优先、新→旧
    rows.sort(key=lambda r: (
        r["相关性"] != "强", r["疑似批量稿"],
        int(r["媒体分级"][1]), r["时间"],
    ), reverse=False)
    as_of = rows[0]["时间"][:10] if rows else ""
    return rows, as_of, "东财资讯聚合（情报通道：未逐条核实，媒体分级为自建表，需实测校准）"


def news_summary(code: str, page_size: int = 20, name: str = "") -> dict:
    """给上层用的摘要：把噪声和真消息分开，避免模型被批量稿淹没。"""
    rows, as_of, _ = stock_news(code, page_size, name)
    real = [r for r in rows if not r["疑似批量稿"] and r["相关性"] == "强"]
    noise = [r for r in rows if r["疑似批量稿"] or r["相关性"] == "弱"]
    return {
        "code": code,
        "as_of": as_of,
        "相关新闻": real[:10],
        "已过滤噪声": len(noise),
        "统计": {"总数": len(rows), "相关": len(real), "噪声": len(noise)},
    }
