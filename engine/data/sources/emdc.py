# -*- coding: utf-8 -*-
"""东方财富数据中心报表（datacenter-web）—— 一次封装，覆盖十余类数据。

与 `eastmoney.py` 分开的原因：这是**另一个 API 家族**（datacenter-web 而非 push2），
熔断与健康度按源独立统计，一个被限流不影响另一个。

实测可用（2026-10-11）：
  利润表 / 资产负债表 / 现金流量表 / 季度主要指标 / 股东户数 / 十大流通股东 /
  高管持股变动 / 机构调研 / 大宗交易 / 限售解禁 / 行业估值 / 业绩预告 / 宏观（CPI 等）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .http import FetchError, NoDataInWindow, NotFound, get_json

NAME = "emdc"
_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REF = "https://data.eastmoney.com/"

# ── 报表登记：key → (报表名, 中文名, 默认排序字段, 每页默认条数) ──────
REPORTS: dict[str, tuple[str, str, str, int]] = {
    "financial_income":   ("RPT_DMSK_FN_INCOME",        "利润表",       "REPORT_DATE", 6),
    "financial_balance":  ("RPT_DMSK_FN_BALANCE",       "资产负债表",   "REPORT_DATE", 6),
    "financial_cashflow": ("RPT_DMSK_FN_CASHFLOW",      "现金流量表",   "REPORT_DATE", 6),
    "quarterly_metrics":  ("RPT_F10_QTR_MAINFINADATA",  "季度主要指标", "REPORT_DATE", 12),
    "holder_num":         ("RPT_HOLDERNUM_DET",         "股东户数",     "END_DATE", 8),
    "top_free_holders":   ("RPT_F10_EH_FREEHOLDERS",    "十大流通股东", "END_DATE", 10),
    "executive_trades":   ("RPT_EXECUTIVE_HOLD_DETAILS", "高管持股变动", "CHANGE_DATE", 10),
    "org_survey":         ("RPT_ORG_SURVEY",            "机构调研",     "RECEIVE_START_DATE", 8),
    "block_trades":       ("RPT_DATA_BLOCKTRADE",       "大宗交易",     "TRADE_DATE", 10),
    "restricted_release": ("RPT_LIFT_STAGE",            "限售解禁",     "FREE_DATE", 10),
    # ★ 坑：RPT_PERFORMANCE 不认 SECURITY_CODE 过滤（实测过滤后恒为"返回数据为空"），
    #   改用 RPT_PUBLIC_OP_NEWPREDICT（支持按代码过滤）
    "forecast":           ("RPT_PUBLIC_OP_NEWPREDICT",  "业绩预告",     "REPORT_DATE", 6),
    # ★ 坑：RPT_PCF10_INDUSTRY_MARKET 的 TRADE_DATE 不是合法排序列，不能传 sort
    "industry_valuation": ("RPT_PCF10_INDUSTRY_MARKET", "行业估值",     "", 10),
}

# ── 宏观指标登记 ────────────────────────────────────────────────────
MACROS: dict[str, str] = {
    "CPI":     "RPT_ECONOMY_CPI",
    "PPI":     "RPT_ECONOMY_PPI",
    "PMI":     "RPT_ECONOMY_PMI",
    "GDP":     "RPT_ECONOMY_GDP",
    "M2":      "RPT_ECONOMY_CURRENCY_SUPPLY",
    "社融":     "RPT_ECONOMY_STOCK_STATISTICS",
    "LPR":     "RPT_ECONOMY_RMB_LOAN",
    "房价":     "RPT_ECONOMY_HOUSE_PRICE",
    "零售":     "RPT_ECONOMY_TOTAL_RETAIL",
    "失业率":   "RPT_ECONOMY_BOOM_INDEX",
}

# ── 关键字段中文化（按报表分别映射，同名不同义的要小心）──────────────
FIELD_CN: dict[str, str] = {
    # 通用
    "SECURITY_CODE": "代码", "SECURITY_NAME_ABBR": "名称", "SECUCODE": "统一代码",
    "REPORT_DATE": "报告期", "NOTICE_DATE": "公告日期", "UPDATE_DATE": "更新日期",
    "END_DATE": "截止日期", "TRADE_DATE": "交易日", "CHANGE_DATE": "变动日期",
    "INDUSTRY_NAME": "行业",
    # 财务三表
    "TOTAL_OPERATE_INCOME": "营业总收入", "OPERATE_INCOME": "营业收入",
    "OPERATE_COST": "营业成本", "PARENT_NETPROFIT": "归母净利润",
    "NETPROFIT": "净利润", "TOTAL_ASSETS": "总资产", "TOTAL_LIABILITIES": "总负债",
    "TOTAL_EQUITY": "股东权益", "TOTAL_PARENT_EQUITY": "归母股东权益",
    "MONETARYFUNDS": "货币资金", "INVENTORY": "存货", "ACCOUNTS_RECE": "应收账款",
    "GOODWILL": "商誉", "SHORT_LOAN": "短期借款", "LONG_LOAN": "长期借款",
    "NETCASH_OPERATE": "经营活动现金流净额", "NETCASH_INVEST": "投资活动现金流净额",
    "NETCASH_FINANCE": "筹资活动现金流净额", "SALES_SERVICES": "销售商品收到的现金",
    # 主要指标
    "EPSJB": "基本每股收益", "EPSKCJB": "扣非每股收益", "BPS": "每股净资产",
    "MGJYXJJE": "每股经营现金流", "ROEJQ": "加权ROE", "XSMLL": "销售毛利率",
    "XSJLL": "销售净利率", "ZCFZL": "资产负债率", "TOTALOPERATEREVE": "营业总收入",
    "PARENTNETPROFIT": "归母净利润", "TOTALOPERATEREVETZ": "营收同比",
    "PARENTNETPROFITTZ": "净利同比", "KFJLRGDHBZC": "扣非净利环比",
    # 股东
    "HOLDER_NUM": "股东户数", "PRE_HOLDER_NUM": "上期股东户数",
    "HOLDER_NUM_CHANGE": "户数变化", "HOLDER_NUM_RATIO": "户数变化率%",
    "AVG_HOLD_NUM": "户均持股", "HOLDER_NAME": "股东名称",
    "HOLD_NUM": "持股数", "FREE_HOLDNUM_RATIO": "占流通股比%",
    "HOLD_NUM_CHANGE": "持股变动",
    # 高管
    "PERSON_NAME": "姓名", "CHANGE_SHARES": "变动股数",
    "AVERAGE_PRICE": "成交均价", "CHANGE_REASON": "变动原因",
    "HOLD_TYPE": "董监高类型", "CHANGE_TYPE": "变动方向",
    # 机构调研
    "RECEIVE_START_DATE": "接待开始日", "RECEIVE_END_DATE": "接待结束日",
    "RECEIVE_PLACE": "接待地点", "RECEIVE_WAY": "接待方式",
    "INVESTIGATORS": "参与机构", "RECEIVE_OBJECT": "接待对象",
    # 大宗交易
    "DEAL_PRICE": "成交价", "DEAL_VOLUME": "成交量", "DEAL_AMT": "成交额",
    "PREMIUM_RATIO": "折溢价率%", "BUYER_NAME": "买方", "SELLER_NAME": "卖方",
    # 解禁
    "FREE_DATE": "解禁日", "CURRENT_FREE_SHARES": "本次解禁股数",
    "LIFT_MARKET_CAP": "解禁市值", "FREE_SHARES_TYPE": "解禁股份类型",
    "BATCH_HOLDER_NUM": "解禁股东数",
    # 业绩预告
    "NP_YOY": "净利同比%", "NP_QOQ": "净利环比%", "FORECAST_TYPE": "预告类型",
    "FORECAST_CONTENT": "预告内容", "PREDICT_FINANCE": "预告财务指标",
    "PREDICT_AMT_LOWER": "预告下限", "PREDICT_AMT_UPPER": "预告上限",
    "ADD_AMP_LOWER": "增幅下限%", "ADD_AMP_UPPER": "增幅上限%",
    "PREDICT_TYPE": "预告类型", "PREDICT_CONTENT": "预告内容",
    "CHANGE_REASON_EXPLAIN": "变动原因", "PREYEAR_SAME_PERIOD": "上年同期",
    "INCREASE_JZ": "增减幅度", "IS_LATEST": "是否最新",
    "PREDICT_RATIO_LOWER": "变动比例下限%", "PREDICT_RATIO_UPPER": "变动比例上限%",
    # 行业估值
    "BOARD_CODE": "板块代码", "BOARD_NAME": "板块名称",
    "PE_TTM": "市盈率TTM", "PB": "市净率", "TOTAL_MARKET_CAP": "总市值",
}


def _translate(row: dict) -> dict:
    return {FIELD_CN.get(k, k): v for k, v in row.items() if v is not None}


def _dc(report: str, code: str = "", page_size: int = 10,
        sort: str = "", order: str = "-1", extra_filter: str = "") -> tuple[list, str]:
    url = f"{_API}?reportName={report}&columns=ALL&pageSize={page_size}"
    filters = []
    if code:
        filters.append(f'(SECURITY_CODE="{code}")')
    if extra_filter:
        filters.append(extra_filter)
    if filters:
        from urllib.parse import quote
        url += "&filter=" + quote("".join(filters))
    if sort:
        url += f"&sortColumns={sort}&sortTypes={order}"
    js = get_json(url, referer=_REF)
    res = (js or {}).get("result")
    if res is None:
        msg = (js or {}).get("message", "") or ""
        # ★ "返回数据为空" 是**有信息量的结论**（该股没这条记录），不是故障 ——
        #   混成"取数失败"，用户就永远分不清"没有"和"没拿到"。
        if "为空" in msg or "无数据" in msg:
            raise NoDataInWindow(f"该报表无匹配记录（{msg}）")
        raise FetchError(f"数据中心返回失败：{msg or 'result 为空'}")
    rows = res.get("data") or []
    if not rows:
        raise NoDataInWindow("该报表无记录（该股未发生过此类事件）")
    return rows, sort


def report(code: str, kind: str, page_size: Optional[int] = None) -> tuple[list, str, str]:
    """通用数据中心报表查询。kind 见 REPORTS。"""
    if kind not in REPORTS:
        raise FetchError(f"未知报表：{kind}（可用：{', '.join(REPORTS)}）")
    rpt, cn, sort_col, default_size = REPORTS[kind]
    rows, _ = _dc(rpt, code, page_size or default_size, sort=sort_col)
    out = [_translate(r) for r in rows]
    as_of = ""
    for r in out:
        for key in ("报告期", "截止日期", "交易日", "变动日期", "解禁日", "公告日期", "接待开始日"):
            if r.get(key):
                as_of = str(r[key])[:10]
                break
        if as_of:
            break
    return out, as_of, f"数据中心报表 {rpt}（{cn}）"


def macro(code: str, indicator: str, page_size: int = 12) -> tuple[list, str, str]:
    """宏观指标。indicator 见 MACROS。"""
    key = str(indicator).upper()
    if key not in MACROS and indicator not in MACROS:
        raise FetchError(f"未知宏观指标：{indicator}（可用：{', '.join(MACROS)}）")
    rpt = MACROS.get(key) or MACROS[indicator]
    rows, _ = _dc(rpt, "", page_size, sort="REPORT_DATE")
    out: list[dict] = []
    for r in rows:
        item = {}
        for k, v in r.items():
            if v is None:
                continue
            # 宏观表字段多且随指标变化，只保留有意义的
            if k in ("REPORT_DATE", "TIME", "DIM_NAME", "DIM_CODE"):
                item[{"REPORT_DATE": "报告期", "TIME": "时间",
                      "DIM_NAME": "地区", "DIM_CODE": "地区码"}[k]] = v
            elif k.endswith(("_SAME", "_BASE", "_SEQUENTIAL", "_ACCUMULATE")):
                item[k] = v
            elif k in ("VALUE", "DATA_VALUE", "INDEX_VALUE"):
                item["数值"] = v
        if item:
            out.append(item)
    if not out:
        raise FetchError("宏观数据为空")
    return out, str(out[0].get("报告期") or out[0].get("时间") or "")[:10], \
        f"国家统计局/央行口径（{rpt}）"


def industry_valuation(code: str, page_size: int = 10) -> tuple[list, str, str]:
    """行业估值（给定个股代码时返回其所属行业的估值记录）。

    ★ 坑：这张报表的 TRADE_DATE 不是合法排序列 —— 传 sort 会报"排序列不存在"。
    """
    rows, _ = _dc("RPT_PCF10_INDUSTRY_MARKET", code, page_size)
    out = [_translate(r) for r in rows]
    as_of = ""
    for r in out:
        for k in ("交易日", "TRADE_DATE", "报告期"):
            if r.get(k):
                as_of = str(r[k])[:10]
                break
        if as_of:
            break
    return out, as_of, "行业整体口径（东财行业分类）"


def dividend(code: str, page_size: int = 20) -> tuple[list, str, str]:
    """分红送配（复权计算的关键输入）。

    ★ 归属说明：这个接口走的是 datacenter-web，所以放在 emdc 模块下 ——
      熔断是按"主机家族"算的，放错模块会导致 push2 的故障连带把它熔断（实测踩到）。
    """
    rows, _ = _dc("RPT_SHAREBONUS_DET", code, page_size, sort="EX_DIVIDEND_DATE")
    out = [
        {
            "除权除息日": str(r.get("EX_DIVIDEND_DATE") or "")[:10],
            "股权登记日": str(r.get("EQUITY_RECORD_DATE") or "")[:10],
            "每10股派息_元": r.get("PRETAX_BONUS_RMB"),
            "送转比例": r.get("BONUS_IT_RATIO"),
            "方案": r.get("IMPL_PLAN_PROFILE"),
            "报告期": str(r.get("REPORT_DATE") or "")[:10],
        }
        for r in rows
    ]
    return out, out[0]["除权除息日"], "税前口径"
