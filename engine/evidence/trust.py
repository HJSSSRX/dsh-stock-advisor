# -*- coding: utf-8 -*-
"""可信度分档 —— **说清每个数据"能验证到什么程度"。**

═══════════════════════════════════════════════════════════════════
为什么不能只分"可信/不可信"两档
═══════════════════════════════════════════════════════════════════
实测遇到的问题：**有些数据保真，但没法验证。**

    · 东财「主力资金流」：全市场只有东财在算这个口径，**没有第二个源可比**
      → 不是它错，是**无从判断**。说它"不可信"是冤枉，说它"可信"是背书。
    · 概念板块归属：东财/同花顺/通达信**三家分类各不相同**，都是"对的"
      → 不能交叉验证，因为**没有唯一正确答案**。
    · 行情收盘价：腾讯 vs 东财 **2400 个交易日零差异**
      → 可以交叉验证，而且是强证据。

**所以分档要分两个维度：**
    ① 来源权威性（谁说的）
    ② **可验证性**（能不能拿第二个源对）

两个维度**不重合**：交易所法定披露（权威）通常也可验证；
东财自家口径（业务上合理）不可验证。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# 可验证性 —— 本模块的核心概念
# ══════════════════════════════════════════════════════════════════
VERIFIABILITY = {
    "cross_source": (
        "**可跨源验证**：有第二个独立来源能对拍。"
        "对上了 = 强证据；对不上 = 立刻查清谁错。"
    ),
    "primary_only": (
        "**只有一手来源**：它是法定披露的唯一出口（如上市公司公告），"
        "但可以**在原文里逐字核对**（PDF 溯源）。"
    ),
    "no_second_source": (
        "⚠️ **没有第二来源可比**：这个口径只有这一家在算。"
        "**不是不可信，是无从判断** —— 不能当事实用，只能当参考，且必须标注。"
    ),
    "definitional": (
        "**定义之争**：不同机构分类不同，**没有唯一正确答案**（如概念板块归属）。"
        "只能说明「按谁的分类」，不能声称「就是这样的」。"
    ),
}

# ══════════════════════════════════════════════════════════════════
# 证据等级（与说明.md 的 S0–S5 一致）
# ══════════════════════════════════════════════════════════════════
LEVELS = {
    "S0": "法定披露原文（公告 PDF / 交易所）",
    "S1": "官方平台（巨潮 / 交易所 / 上证e互动 / 深交所互动易）",
    "S2": "官方统计（统计局 / 央行 / 中债 / 交易所行情）",
    "S3": "财经媒体 / 数据商整理（东财 / 新浪 / 同花顺 的加工数据）",
    "S4": "自媒体 / 观点",
    "S5": "黑名单",
}


@dataclass
class SourceSpec:
    """一个数据源的完整画像。"""
    key: str                    # 我们的 kind 名
    label: str                  # 人话名
    level: str                  # S0..S5
    verifiability: str          # VERIFIABILITY 的键
    can_enter_conclusion: bool  # 能不能进结论
    note: str = ""

    def describe(self) -> str:
        return (f"{self.label}［{self.level} {LEVELS[self.level]}］\n"
                f"    可验证性：{VERIFIABILITY[self.verifiability]}\n"
                f"    能进结论：{'是' if self.can_enter_conclusion else '**否**'}"
                + (f"\n    {self.note}" if self.note else ""))


# ══════════════════════════════════════════════════════════════════
# 实际用到的源的画像（不是理论，是我们真的在用的）
# ══════════════════════════════════════════════════════════════════
SOURCES: dict[str, SourceSpec] = {
    # ── S0/S1：法定披露，可回原文核对 ──
    "announcement": SourceSpec(
        "announcement", "巨潮公告（原始 PDF）", "S0", "primary_only", True,
        "★ 交易所指定披露平台。**PDF 可下载、可在原文里逐字核对**（实测茅台半年报 110 页）。"),
    "dragon_tiger": SourceSpec(
        "dragon_tiger", "龙虎榜席位", "S0", "primary_only", True,
        "交易所法定披露。⚠️ 席位是营业部，**不代表具体人**；「机构专用」是交易所标记口径。"),
    "margin_trading": SourceSpec(
        "margin_trading", "融资融券", "S0", "primary_only", True,
        "交易所官方数据。两融余额是**全市场情绪**的硬指标。"),
    "holder_trades": SourceSpec(
        "holder_trades", "股东/高管增减持", "S0", "primary_only", True,
        "★ 达阈值**必须公告**，所以是法定披露。"),
    "earnings_forecast": SourceSpec(
        "earnings_forecast", "业绩预告", "S0", "primary_only", True,
        "达到阈值必须预告（深交所要求）。"),
    "restricted_release": SourceSpec(
        "restricted_release", "限售解禁", "S0", "primary_only", True,
        "★ 解禁是**确定性事件**（日期数量都在公告里）；"
        "但「解禁→抛压」**是推测**，要另说。"),
    "st_list": SourceSpec(
        "st_list", "ST 名单", "S0", "primary_only", True,
        "风险警示由交易所判定，**是可直接断言的事实**。"),
    "interactive_qa": SourceSpec(
        "interactive_qa", "投资者互动问答", "S1", "primary_only", True,
        "★ **上市公司自己说的话**，比媒体转述可靠。"
        "⚠️ 但仍是**公司自述**，涉及经营数据必须回财报核对。"),

    # ── S2：官方统计 ──
    "macro_pmi": SourceSpec("macro_pmi", "PMI", "S2", "cross_source", True,
                            "国家统计局。"),
    "macro_lpr": SourceSpec("macro_lpr", "LPR", "S2", "cross_source", True, "央行公布。"),
    "macro_social_financing": SourceSpec(
        "macro_social_financing", "社会融资规模", "S2", "cross_source", True, "人民银行。"),
    "yield_curve": SourceSpec(
        "yield_curve", "中债收益率曲线", "S2", "cross_source", True,
        "★ **无风险利率的权威来源** —— 算索提诺、算估值折现都要它。"),

    # ── S3：可跨源验证的加工数据 ──
    "quote": SourceSpec(
        "quote", "实时行情快照", "S2", "cross_source", True,
        "★ 实测：腾讯 vs 东财，**2400 个交易日零差异**（6 只票 5 个板块）。"),
    "kline": SourceSpec(
        "kline", "K 线", "S2", "cross_source", True,
        "★ 同上。⚠️ **前复权口径两家不同**（腾讯等差 / 东财比例，实测差 1.279%）"
        "→ 长区间回测用不复权。"),
    "valuation_history": SourceSpec(
        "valuation_history", "估值历史 PE/PB/PS", "S3", "cross_source", True,
        "★ 实测与腾讯快照 PE/PB 差 0.4%~2.3%（价格时点不同所致，**无系统偏差**）。"
        "⚠️ **负 PE 必须标「不适用」**，不能拿去排分位。"),
    "xsection": SourceSpec(
        "xsection", "横截面分位（全市场一次取）", "S2", "cross_source", True,
        "★ 底层是东财 clist 的 PE/PB/市值/换手率，**一次取全市场 5562 只**。"
        "PE/PB 已与腾讯快照/baostock 双源对拍过（差 0.02%）。\n"
        "    ⚠️ **「分位」是对比出来的，必须标对比口径** —— "
        "①同行业+同规模档 / ②同行业 / ③全市场，三级降级，**每一级都要能说出来用的是哪一级**。\n"
        "    ⚠️ 0 是**哨兵值**（表示不适用，如亏损股的 PE），已按缺失处理，不当真值。\n"
        "    ⚠️ 中位秩法（无参数、抗极值）；同类样本 < 20 只**不给分位**。"),
    "financial_sina": SourceSpec(
        "financial_sina", "财务三表（新浪整理）", "S3", "primary_only", True,
        "★ 实测：5 个数字在**公告原文 PDF 里逐位找到**（净利润/营业总收入/营业收入/"
        "利润总额/营业成本），且**算术闭合**（归属+少数股东=净利润）。"
        "⚠️ 必须标口径 —— 净利润 vs 归属净利润差 15.16 亿。"),

    # ── S3：**不可跨源验证**的加工数据（这是重点）──
    "fundflow": SourceSpec(
        "fundflow", "个股资金流（东财）", "S3", "no_second_source", False,
        "⚠️⚠️ **「主力」= 大单+超大单，是东财按成交金额分档的自家统计口径**，"
        "**全市场只有东财在算这个口径，没有第二个源可比**。\n"
        "    → **不是不可信，是无从判断**。\n"
        "    → **不能当事实进结论**；只能作参考，且必须标明口径与出处。\n"
        "    → 跨日不可加总（分档标准会变），跨股不可比较（盘子大小不同）。"),
    "concept_blocks": SourceSpec(
        "concept_blocks", "概念板块归属（东财）", "S3", "definitional", False,
        "⚠️ **东财/同花顺/通达信三家分类各不相同，都是「对的」** —— "
        "没有唯一正确答案，**无从交叉验证**。\n"
        "    → 只能说「**按东财的分类**它属于这几个概念」，"
        "**不能说「它就是这几个概念的股票」**。"),
    "hot_rank": SourceSpec(
        "hot_rank", "人气榜（东财）", "S3", "no_second_source", False,
        "⚠️ 散户关注度，**与基本面无关**。★ 情报通道 —— **永不进结论**。"),
    "research_reports": SourceSpec(
        "research_reports", "卖方研报", "S3", "no_second_source", False,
        "⚠️ **卖方研报有系统性乐观偏差**（券商与上市公司有业务关系），"
        "评级「买入」的基线就很高。★ 情报，不是事实。"),
    "eps_forecast": SourceSpec(
        "eps_forecast", "一致预期 EPS", "S3", "no_second_source", False,
        "⚠️ **覆盖率是硬约束**：2024 年 沪深300 约 98% / 中证1000 约 79% / 全A 约 53%。"
        "**没覆盖的票不能拿「预期」说事**。"),
    "institution_survey": SourceSpec(
        "institution_survey", "机构调研", "S0", "primary_only", True,
        "★ 法定披露。⚠️ 「机构去调研了」**≠「机构看好」** —— "
        "调研是双向的，也可能是去看它有多差。"),
    "news": SourceSpec(
        "news", "个股新闻", "S3", "no_second_source", False,
        "★ 情报通道 —— **永不进结论**，只进「相关动态」。"
        "⚠️ 实测：按代码搜会捞进行业/大盘稿（茅台 10 条里 9 条不是它的）。"),
    "cctv_news": SourceSpec(
        "cctv_news", "新闻联播文字稿", "S2", "primary_only", True,
        "★ 产业政策、监管口径往往**先在这里出现** —— 政策分析师的原料。"),
    "equity_pledge": SourceSpec(
        "equity_pledge", "股权质押", "S0", "primary_only", True,
        "★ **有实证**：杨柳勇等（2023）XGBoost 预警，召回 74.44% / 精确 56.57%。"
        "★ **深交所硬阈值：控股股东质押 >80% → 风险分类不得为正常类**（官方口径）。"),
    "price_anomaly": SourceSpec(
        "price_anomaly", "日内异动池", "S3", "primary_only", True,
        "交易所口径的异常判定。"),
    "stock_monitor": SourceSpec(
        "stock_monitor", "重点监控池", "S3", "primary_only", False,
        "⚠️ **名单是事实，含义是解释** —— 在名单里 ≠ 这票有问题。"),

    # ══════════════════════════════════════════════════════════════
    # 以下为 2026-10-12 补齐：原来 55 个 kind 里只登记了 27 个，
    # 剩下 28 个因「保守默认」全被挡在结论外 —— **那是没标，不是不该标**。
    # ══════════════════════════════════════════════════════════════

    # ── 行情与指数 ──
    "index": SourceSpec("index", "指数实时行情", "S2", "cross_source", True,
                        "交易所/指数公司口径。"),
    "index_kline": SourceSpec(
        "index_kline", "指数日K", "S2", "cross_source", True,
        "⚠️ **不能用腾讯 K 线取上证指数** —— 它的前缀白名单没有 000001，"
        "会取到**平安银行**（11.76）而且不报错。我们走东财 index 路由 + 千点量级断言。"),
    "universe": SourceSpec("universe", "全市场股票列表", "S2", "cross_source", True,
                           "代码/名称/板块映射。"),
    "stock_info": SourceSpec("stock_info", "个股概况", "S3", "primary_only", True,
                             "公司基本信息（名称/行业/上市日）。涉及财务字段要回公告。"),
    "sw_industry": SourceSpec(
        "sw_industry", "申万行业分类历史", "S2", "cross_source", True,
        "★★ **重要**：行业分类会随时间调整，用**当前**分类回溯历史会引入**前视偏差**。"
        "⚠️ 实测 2026-10-12 该端点不可用（申万站点 SSL 握手失败）。"),
    "industry_board": SourceSpec("industry_board", "行业板块排名", "S3", "cross_source", True,
                                 "按板块聚合的涨跌统计，可与指数对拍。"),
    "board": SourceSpec("board", "板块列表", "S3", "definitional", False,
                        "⚠️ 东财自家板块分类 —— 与同花顺/通达信**各不相同，没有唯一答案**。"),
    "board_members": SourceSpec("board_members", "板块成分股", "S3", "definitional", False,
                                "⚠️ 同上：**按谁的分类**必须说清，不能说「它就是这板块的」。"),

    # ── 财务与估值 ──
    "financial": SourceSpec("financial", "业绩报表", "S3", "primary_only", True,
                            "★ 数字可回公告 PDF 逐位核对（实测 5/5 命中）。⚠️ 必须标口径。"),
    "financial_income": SourceSpec("financial_income", "利润表", "S3", "primary_only", True,
                                   "★ 同上。⚠️ 净利润 vs 归属净利润差 15.16 亿。"),
    "financial_balance": SourceSpec("financial_balance", "资产负债表", "S3", "primary_only", True,
                                    "★ 同上。"),
    "financial_cashflow": SourceSpec("financial_cashflow", "现金流量表", "S3", "primary_only", True,
                                     "★ 同上。"),
    "quarterly_metrics": SourceSpec("quarterly_metrics", "单季指标", "S3", "primary_only", True,
                                    "★ 同上。⚠️ 单季 ≠ 累计，口径要说清。"),
    "forecast": SourceSpec("forecast", "业绩预告", "S0", "primary_only", True,
                           "★ 达到阈值必须预告（交易所要求）。"),
    "valuation_now": SourceSpec(
        "valuation_now", "估值快照", "S3", "cross_source", True,
        "★ 实测与 baostock 历史差 0.4%~2.3%（时点不同所致，**无系统偏差**）。"),
    "industry_valuation": SourceSpec("industry_valuation", "行业估值", "S3", "cross_source", True,
                                     "行业指数估值。"),

    # ── 股东与内部人 ──
    "dividend": SourceSpec("dividend", "分红送转", "S0", "primary_only", True,
                           "★ 法定披露（分红方案必须公告）。"),
    "executive_trades": SourceSpec("executive_trades", "高管持股变动", "S0", "primary_only", True,
                                   "★ 法定披露。"),
    "top_free_holders": SourceSpec("top_free_holders", "十大流通股东", "S0", "primary_only", True,
                                   "★ 季报法定披露。"),
    "holder_num": SourceSpec("holder_num", "股东户数", "S0", "primary_only", True,
                             "★ 法定披露。⚠️ 披露频率低（季报），两次之间无法更新。"),
    "org_survey": SourceSpec("org_survey", "机构调研", "S0", "primary_only", True,
                             "★ 法定披露。⚠️ 「机构调研了」**≠「机构看好」**。"),

    # ── 事件与资金 ──
    "block_trades": SourceSpec("block_trades", "大宗交易", "S0", "primary_only", True,
                               "★ 交易所披露。⚠️「折价=减持压力」是**经验规则不是实证**。"),
    "share_buyback": SourceSpec("share_buyback", "股份回购", "S0", "primary_only", True,
                                "★ 法定披露。⚠️ 回购**预案** ≠ **实施**，看进度字段。"),
    "ipo_calendar": SourceSpec("ipo_calendar", "新股日历", "S2", "primary_only", True,
                               "交易所发行安排。"),
    "convertible_bonds": SourceSpec("convertible_bonds", "可转债清单", "S2", "primary_only", True,
                                    "⚠️ 可转债 1 手 = **10 张**（不是 100），T+0 且无涨跌停 —— **规则与股票不同**。"),
    "fundflow_minute": SourceSpec(
        "fundflow_minute", "分钟级资金流（东财）", "S3", "no_second_source", False,
        "⚠️ 与日级资金流同源同口径：**东财自家按成交金额分档，全市场只有它在算**，"
        "**无从判断对错** → 不进结论。"),

    # ── 宏观 ──
    "macro": SourceSpec("macro", "宏观指标（通用）", "S2", "cross_source", True,
                        "统计局/央行等官方一手数据。"),

    # ── 未接线 ──
    # ── 2026-10-12 补：市场宽度 + 舆情类 ──
    "tdx_package": SourceSpec(
        "tdx_package", "全市场日线（通达信盘后包）", "S2", "cross_source", True,
        "★ **通达信官网一手数据**，含成交额。收盘后数小时才发布。"
        "⚠️ 三个坑：①「000001」在这里是**上证指数**不是平安银行；"
        "② **成交额不能全表加总**（含债券/ETF，加总得 90 倍）；③ 盘中取会报未发布（正常）。"),
    "market_breadth": SourceSpec(
        "market_breadth", "市场宽度（涨跌家数/成交额/涨跌停/封板率）", "S2", "cross_source", True,
        "★ 从通达信封自己算。**已与豆包的外部基准逐项对照通过**"
        "（指数/成交额/涨跌家数完全一致；涨停数差异来自 ST 5% 限幅）。"),
    "news_flash": SourceSpec(
        "news_flash", "财联社电报", "S3", "no_second_source", False,
        "★ 情报通道，**永不进结论**。⚠️ 快讯是**抢发**的，措辞可能后续修正。"),
    "news_global": SourceSpec(
        "news_global", "东财全球7x24", "S3", "no_second_source", False,
        "★ 情报通道，**永不进结论**。"),
    "news_wallstreetcn": SourceSpec(
        "news_wallstreetcn", "华尔街见闻7x24", "S3", "no_second_source", False,
        "★ 情报通道，**永不进结论**。"),
    "hot_list": SourceSpec(
        "hot_list", "同花顺热榜", "S3", "no_second_source", False,
        "⚠️ **热度 ≠ 基本面** —— 反映关注度，可能是炒作也可能是出货。"
        "★ 情报通道，**永不进结论**。"),
    "limit_up_reason": SourceSpec(
        "limit_up_reason", "涨停题材归因", "S3", "no_second_source", False,
        "★★ 回答「今天为什么涨」最直接的原料。"
        "⚠️ **归因是同花顺编辑写的，不是公司说的** —— 属于解读，不是事实。"
        "★ 情报通道，**永不进结论**。"),
    "limit_up_sentiment": SourceSpec(
        "limit_up_sentiment", "打板情绪", "S3", "no_second_source", False,
        "⛔ **实测失效**：逐日查 5 个交易日全部返回全 0，"
        "而同日涨停题材归因有 44~72 条 —— **是它自己的数据源坏了，不是市场没涨停**。"
        "→ **改用 `market_breadth`（我们自己从通达信封算的）**。"),
    "industry_reports": SourceSpec(
        "industry_reports", "行业研报", "S3", "no_second_source", False,
        "★ 情报，**永不进结论** —— 卖方研报有系统性乐观偏差。"),
    "research_sina": SourceSpec(
        "research_sina", "新浪研报列表", "S3", "no_second_source", False,
        "★ 情报，**永不进结论**。"),
    "hot_concept": SourceSpec(
        "hot_concept", "个股热点概念（当下）", "S3", "definitional", False,
        "⚠️ 与 `concept_blocks` 的区别：那个是**固定分类归属**，这个是**当下热度关联**。"
        "**两者都不能进结论。**"),
    "irm_sz": SourceSpec(
        "irm_sz", "深市互动易", "S1", "primary_only", True,
        "★ **公司自己回答投资者提问** —— S1 级，比媒体转述可靠。"
        "⚠️ 但仍是**公司自述**，涉及经营数据必须回财报核对。"),
    # ── 2026-10-12 补：akshare 换源通道（东财 clist 被封后的替代）──
    "industry_cninfo": SourceSpec(
        "industry_cninfo", "巨潮行业分类", "S0", "primary_only", True,
        "★★ **巨潮（交易所指定披露平台）的行业分类** —— **法定披露级**。"
        "比东财/同花顺的自家分类更硬：那两家说「我们认为它属于这个行业」，"
        "巨潮说「按规定它属于这个行业」。"),
    "industry_pe_cninfo": SourceSpec(
        "industry_pe_cninfo", "行业市盈率（巨潮）", "S2", "primary_only", True,
        "★ 法定披露平台数据，含加权/中位/算术三种口径 —— **用哪种必须说清**。"
        "⚠️ date 参数**必须是报告期**（0331/0630/0930/1231），"
        "传普通日期会报一个看不出原因的错。"),
    "company_profile": SourceSpec(
        "company_profile", "公司概况（巨潮）", "S0", "primary_only", True,
        "★ 法定披露级。用于替代挂掉的东财 `stock_info`。"
        "⚠️ 涉及经营数据仍须回财报核对。"),
    "broker_gold": SourceSpec(
        "broker_gold", "券商月度金股", "S3", "no_second_source", False,
        "★ 情报通道，**永不进结论**。"),
    "announcement_vendor": SourceSpec(
        "announcement_vendor", "巨潮公告（vendor 路径）", "S0", "primary_only", True,
        "⚠️ 与已接线的 `announcement`（cninfo 直连）**是同一个源**，"
        "本桥绑定**未接线**（重复，不需要接）。"),
}


def spec_of(kind: str) -> Optional[SourceSpec]:
    return SOURCES.get(kind)


def can_enter_conclusion(kind: str) -> bool:
    """这个 kind 的数据能不能进结论。

    ★ 保守默认：**没登记的一律 `False`** ——
      新接一个源时忘了标画像，会**默认进不了结论**，而不是默认能进。
    """
    s = SOURCES.get(kind)
    return bool(s and s.can_enter_conclusion)


def unverifiable_kinds() -> list[str]:
    """不能跨源验证的 kind（必须标注口径、不进结论）。"""
    return sorted(k for k, v in SOURCES.items()
                  if v.verifiability in ("no_second_source", "definitional"))


def audit() -> dict:
    """给 /status 用的总览。"""
    by_level: dict[str, int] = {}
    for v in SOURCES.values():
        by_level[v.level] = by_level.get(v.level, 0) + 1
    return {
        "总数": len(SOURCES),
        "可进结论": sum(1 for v in SOURCES.values() if v.can_enter_conclusion),
        "不可进结论": sum(1 for v in SOURCES.values() if not v.can_enter_conclusion),
        "按等级": dict(sorted(by_level.items())),
        "不可跨源验证": unverifiable_kinds(),
    }
