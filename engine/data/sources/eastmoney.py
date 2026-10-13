# -*- coding: utf-8 -*-
"""东方财富 —— 主数据源。

为什么是主源：`push2` / `push2his` / `datacenter-web` 就是东方财富 App 自己在用的接口，
JSON 规范、字段最全、覆盖最广（行情/K线/资金流/财务/公告/板块/分红/全市场列表）。

约定：每个函数返回 (data, as_of, caliber)；失败抛 FetchError，由 registry 统一包成 Result。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..caliber import FQT, KLT, index_secid, secid, scale_quote
from .http import FetchError, get_json, post_json

NAME = "eastmoney"
REF_DATA = "https://data.eastmoney.com/"
REF_EMWEB = "https://emweb.securities.eastmoney.com/"

# ── 字段串 ──────────────────────────────────────────────────────────
_QUOTE_FIELDS = ",".join(
    ["f43", "f44", "f45", "f46", "f47", "f48", "f57", "f58", "f59",
     "f60", "f86", "f107", "f116", "f117", "f152", "f162", "f167",
     "f168", "f169", "f170", "f171", "f292"]
)
_KLINE_F1 = "f1,f2,f3,f4,f5,f6"
_KLINE_F2 = "f51,f52,f53,f54,f55,f56,f57,f58"


def _ts_to_str(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:                                        # noqa: BLE001
        return ""


# ══════════════════════════════════════════════════════════════════
# 1. 实时行情
# ══════════════════════════════════════════════════════════════════
def quote(code: str) -> tuple[dict, str, str]:
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid(code)}&fields={_QUOTE_FIELDS}"
    )
    js = get_json(url)
    raw = (js or {}).get("data")
    if not raw:
        raise FetchError("data 为空（代码可能不存在，或该股已退市）")

    out = scale_quote(raw)
    # ★ 停牌识别：f43（现价）为 0 但 f60（昨收）有值 → 停牌，绝不能报"股价 0 元"
    if raw.get("f43") == 0:
        prev = raw.get("f60")
        if prev:
            out["⚠️停牌"] = True
            out["现价"] = round(prev / 100, 4)
            out["备注"] = "现价为 0（停牌或无成交），此处显示昨收价"
        else:
            out["⚠️异常"] = True
            out["备注"] = "价格字段为 0 且无昨收，数据不可用"

    as_of = _ts_to_str(raw.get("f86")) or datetime.now().strftime("%Y-%m-%d")
    return out, as_of, "不复权（实时快照）"


def index_quote(code: str) -> tuple[dict, str, str]:
    """指数行情。code 用 000001（上证）/ 399001（深证）/ 399006（创业板）。"""
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={index_secid(code)}&fields={_QUOTE_FIELDS}"
    )
    js = get_json(url)
    raw = (js or {}).get("data")
    if not raw:
        raise FetchError("指数数据为空")
    return scale_quote(raw), _ts_to_str(raw.get("f86")), "指数点位"


# ══════════════════════════════════════════════════════════════════
# 2. K 线（日/周/月/分钟，三种复权）
# ══════════════════════════════════════════════════════════════════
def kline(
    code: str,
    freq: str = "daily",
    fqt: int = 1,
    limit: int = 250,
    beg: str = "0",
    end: str = "20500101",
    is_index: bool = False,
) -> tuple[list, str, str]:
    klt = KLT.get(freq)
    if klt is None:
        raise FetchError(f"不支持的周期：{freq}")

    # ★★ is_index 必须显式传（理由见 caliber.index_secid）★★
    #   指数代码和股票代码会撞：000001 当股票是**平安银行**、当指数是**上证指数**。
    #   不传 → 你以为在取指数，实际取到一只个股，而且**不会报任何错**
    #   （实测：拿"上证指数的交易日历"去做停牌检测，拿到的其实是平安银行的 K 线）。
    #   走 registry 时由 kind="index_kline" 固定带上（ROUTES 的 3 元组）。
    sid = index_secid(code) if is_index else secid(code)

    # ★ 坑（实测）：东财的 lmt 参数在 beg=0 时**不生效**，会把全历史（2800+ 根）都返回。
    #   解决：需要少量数据时用 beg 限定起始日，且无论如何都在本地做一次切片兜底。
    if limit and beg == "0" and freq in ("daily", "weekly", "monthly"):
        from datetime import timedelta
        # 系数要留足节假日余量（实测：2.2 倍在国庆假期后只取到 3 根，应为 5 根）
        span = {"daily": 3.5, "weekly": 12.0, "monthly": 45.0}[freq] * limit
        beg = (datetime.now() - timedelta(days=int(span))).strftime("%Y%m%d")

    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={sid}&fields1={_KLINE_F1}&fields2={_KLINE_F2}"
        f"&klt={klt}&fqt={fqt}&beg={beg}&end={end}&lmt={limit}"
    )
    js = get_json(url)
    data = (js or {}).get("data")
    if not data or not data.get("klines"):
        raise FetchError("K 线为空（可能是新股、停牌，或参数有误）")

    items = [k.split(",") for k in data["klines"]]
    if freq in ("daily", "weekly", "monthly"):
        rows = [
            {
                "日期": i[0], "开盘": float(i[1]), "收盘": float(i[2]),
                "最高": float(i[3]), "最低": float(i[4]),
                "成交量_手": float(i[5]), "成交额_元": float(i[6]),
                "涨跌幅_%": float(i[7]) if len(i) > 7 and i[7] not in ("", "-") else None,
            }
            for i in items
        ]
    else:   # 分钟线字段不同：时间,开,收,高,低,成交量
        rows = [
            {
                "时间": i[0], "开盘": float(i[1]), "收盘": float(i[2]),
                "最高": float(i[3]), "最低": float(i[4]), "成交量_手": float(i[5]),
            }
            for i in items
        ]

    # 本地兜底切片（保留最近 limit 根）
    truncated = 0
    if limit and len(rows) > limit:
        truncated = len(rows) - limit
        rows = rows[-limit:]

    # ★ 前复权在长期历史里可能出现负价（分红除权累积）——必须显式告警
    neg = [r for r in rows if (r.get("收盘") or 0) < 0]
    caliber = FQT.get(fqt, str(fqt))
    if truncated:
        caliber += f"｜已截取最近 {limit} 根（源站返回 {len(rows) + truncated} 根）"
    if neg:
        caliber += f"｜⚠️ 有 {len(neg)} 根 K 线收盘价为负（复权累积所致），长周期计算需注意"

    return rows, rows[-1]["日期"] if freq in ("daily", "weekly", "monthly") else rows[-1]["时间"], caliber


# ══════════════════════════════════════════════════════════════════
# 3. 资金流
# ══════════════════════════════════════════════════════════════════
def fundflow(code: str, limit: int = 30) -> tuple[list, str, str]:
    """历史资金流。

    ★ 坑（实测）：`push2/fflow/kline/get` 现在**只返回当天 1 条**（不论 lmt 传多少）。
      历史数据必须用 `push2his/fflow/daykline/get`（实测 lmt=0 → 120 条，lmt=30 → 30 条）。
    """
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?secid={secid(code)}&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        f"&klt=101&lmt={limit}"
    )
    js = get_json(url)
    data = (js or {}).get("data")
    if not data or not data.get("klines"):
        raise FetchError("资金流为空")
    rows = []
    for k in data["klines"]:
        p = k.split(",")

        def _v(i):
            try:
                return float(p[i]) if p[i] not in ("", "-") else None
            except (IndexError, ValueError):
                return None

        rows.append({
            "日期": p[0],
            "主力净流入_元": _v(1),
            "小单净流入_元": _v(2),
            "中单净流入_元": _v(3),
            "大单净流入_元": _v(4),
            "超大单净流入_元": _v(5),
            "主力净占比_%": _v(6),
        })
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    return rows, rows[-1]["日期"], "按单笔金额分档（大单+超大单=主力）；各档净额之和恒为 0"


# ══════════════════════════════════════════════════════════════════
# 4. 财务（业绩报表）
# ══════════════════════════════════════════════════════════════════
_FIN_CN = {
    "REPORTDATE": "报告期", "DATATYPE": "报告类型", "NOTICE_DATE": "公告日期",
    "BASIC_EPS": "基本每股收益_元", "DEDUCT_BASIC_EPS": "扣非每股收益_元",
    "TOTAL_OPERATE_INCOME": "营业总收入_元", "PARENT_NETPROFIT": "归母净利润_元",
    "WEIGHTAVG_ROE": "加权ROE_%", "YSTZ": "营收同比_%", "SJLTZ": "净利同比_%",
    "BPS": "每股净资产_元", "MGJYXJJE": "每股经营现金流_元",
    "XSMLL": "销售毛利率_%", "PUBLISHNAME": "行业", "BOARD_CODE": "行业代码",
    "ASSIGNDSCRPT": "分红方案",
}


def financial(code: str, page_size: int = 4) -> tuple[list, str, str]:
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName=RPT_LICO_FN_CPD&columns=ALL"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)&pageSize={page_size}"
    )
    js = get_json(url, referer=REF_DATA)
    rows_raw = ((js or {}).get("result") or {}).get("data")
    if not rows_raw:
        raise FetchError("财务数据为空（可能是指数/基金代码，或该股无财报记录）")

    rows = []
    for r in rows_raw:
        item = {}
        for k, v in r.items():
            if k in _FIN_CN:
                item[_FIN_CN[k]] = v
        item["_原始报告期"] = r.get("REPORTDATE", "")[:10]
        rows.append(item)

    # ★ 坑（实测）：接口返回顺序不是按报告期倒序（出现过 2025H1 → 2026H1 → 2025Q1）。
    #   必须显式排序，否则"最新一期"会取错。
    rows.sort(key=lambda x: x.get("_原始报告期") or "", reverse=True)
    return rows, rows[0].get("_原始报告期", ""), "合并报表口径（未调整）"


# ══════════════════════════════════════════════════════════════════
# 5. 公告
# ══════════════════════════════════════════════════════════════════
def announcement(code: str, page_size: int = 20, ann_type: str = "A") -> tuple[list, str, str]:
    url = (
        f"https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?sr=-1&page_size={page_size}&page_index=1&ann_type={ann_type}"
        f"&client_source=web&stock_list={code}"
    )
    js = get_json(url)
    lst = ((js or {}).get("data") or {}).get("list")
    if not lst:
        raise FetchError("公告为空")
    rows = [
        {
            "标题": it.get("title", "").strip(),
            "日期": (it.get("notice_date") or "")[:10],
            "类型": (it.get("columns") or [{}])[0].get("column_name", ""),
            "art_code": it.get("art_code", ""),
        }
        for it in lst
    ]
    return rows, rows[0]["日期"], "交易所披露（B 级：内容未逐条核验）"


# ══════════════════════════════════════════════════════════════════
# 6. 板块 / 全市场 / 分红
# ══════════════════════════════════════════════════════════════════
# ★ 坑（实测）：clist 接口的 pz 参数被服务端硬限制为 100 条 ——
#   传 pz=6000 也只返回 100 条。要拿全量必须翻页。
_CLIST_MAX_PZ = 100


def _clist(fs: str, fields: str, fid: str = "f12", max_pages: int = 80) -> tuple[list, int]:
    """带翻页的列表查询。返回 (rows, total)。

    注意：受底座限流影响，每页间隔约 1.2 秒；全市场 5922 只 ≈ 60 页 ≈ 75 秒。
    所以它只适合"每日/每周一次的基础信息同步"，**不适合随用户提问调用**。
    """
    rows: list[dict] = []
    total = 0
    for pn in range(1, max_pages + 1):
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={pn}&pz={_CLIST_MAX_PZ}&po=1&np=1&fltt=2&invt=2"
            f"&fid={fid}&fs={fs}&fields={fields}"
        )
        js = get_json(url)
        d = (js or {}).get("data") or {}
        diff = d.get("diff") or []
        total = d.get("total") or total
        if not diff:
            break
        rows.extend(diff)
        if total and len(rows) >= total:
            break
    return rows, total


def board_list(code: str = "", limit: int = 200) -> tuple[list, str, str]:
    """行业板块列表（含涨跌幅与主力净流入）。

    注：第一个参数 `code` 是为统一调用约定保留的（registry 总是把 code 作为
    第一个位置参数传入），本接口不使用它。
    """
    raw, total = _clist("m:90+t:2", "f12,f14,f3,f62", fid="f62", max_pages=max(1, (limit + 99) // 100))
    if not raw:
        raise FetchError("板块列表为空")
    rows = [
        {"板块代码": d.get("f12"), "板块名称": d.get("f14"),
         "涨跌幅_%": d.get("f3"), "主力净流入_元": d.get("f62")}
        for d in raw[:limit]
    ]
    return rows, datetime.now().strftime("%Y-%m-%d"), f"行业板块（东财分类，共 {total} 个）"


def board_members(code: str, limit: int = 500) -> tuple[list, str, str]:
    """板块成分股。code = 板块代码（如 BK0478）。"""
    raw, total = _clist(f"b:{code}", "f12,f14,f100", max_pages=max(1, (limit + 99) // 100))
    if not raw:
        raise FetchError(f"板块 {code} 无成分股")
    rows = [{"代码": d.get("f12"), "名称": d.get("f14"), "行业": d.get("f100")} for d in raw[:limit]]
    return rows, datetime.now().strftime("%Y-%m-%d"), f"板块成分（东财分类，共 {total} 只）"


def universe(code: str = "", max_pages: int = 80) -> tuple[list, str, str]:
    """全市场列表，带行业 + 地域 + 概念标签。

    ⚠️ 约 60 次请求 / 75 秒。这是**基础信息同步任务**（每日或每周跑一次），
       不是随用户提问调用的接口 —— 上层请从缓存读取。
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    raw, total = _clist(fs, "f12,f13,f14,f100,f102,f103", max_pages=max_pages)
    if not raw:
        raise FetchError("全市场列表为空")
    rows = [
        {"代码": d.get("f12"), "名称": d.get("f14"), "行业": d.get("f100"),
         "地域": d.get("f102"),
         "概念": [c for c in (d.get("f103") or "").split(",") if c]}
        for d in raw
    ]
    return rows, datetime.now().strftime("%Y-%m-%d"), f"东财行业+概念分类（覆盖 {len(rows)}/{total} 只）"


def dividend(code: str, limit: int = 20):   # noqa: D401
    """已迁移到 emdc 模块（本接口走的是 datacenter-web 主机，不是 push2 家族）。

    保留此占位是为了在调用方能拿到明确报错，而不是静默 AttributeError。
    """
    raise FetchError("dividend 已迁移到 emdc.dividend，请改用 fetch('dividend', ...)")
