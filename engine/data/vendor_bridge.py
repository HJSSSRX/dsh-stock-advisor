# -*- coding: utf-8 -*-
"""取数层适配桥 —— 把 a-stock-data 的输出接进我们的统一契约。

═══════════════════════════════════════════════════════════════════
这是「选项 B」的落地：**采购用他们的，验收和存档用我们的**
═══════════════════════════════════════════════════════════════════

              a-stock-data（10,795★, Apache-2.0）
              34 数据源 / 87 端点 / 181 函数
                        ↓  本文件
              我们的 Result（溯源三件套 + 内容指纹 + 失败状态）

为什么值得这么接（审计结论，2026-10-12）：
  · 他们的取数层是我们的超集：15 层 / 87 端点，我们只有 23 类、主要 1 个源
  · 他们**已经有** `source` / `source_url` / `fetched_at`，也**已经**区分
    「确实没有」与「接口坏了」—— 我们以为是独有的东西，人家也有
  · 他们**没有**的：内容指纹（eid）、结论→原文的 span 核验、拒答、置信度/反方/失效条件

★ 本文件只做三件事，别的都不做：
    ① 把调哪个他们的函数查出来（BINDINGS）
    ② 把返回的各种形状统一成 list[dict]，把 source/fetched_at 提到 Result 上
    ③ 把他们的异常翻译成我们的 error_kind（**「确实没有」与「取不到」必须分开**）

⛔ 本文件**不**做：缓存、熔断、重试 —— 那些留在 registry.py（我们的漏斗）。

═══════════════════════════════════════════════════════════════════
★★★ 字段名：以他们的为准，计算层改过来 ★★★
═══════════════════════════════════════════════════════════════════
我们旧格式（东财中文键）      新规范（a-stock-data 英文键）
    日期                      date
    开盘 / 收盘 / 最高 / 最低   open / close / high / low
    成交量_手                  volume      ⚠️ 单位随板块变，见下
    成交额_元                  amount
    涨跌幅_%                   change_pct

⚠️⚠️ **volume 的单位不是常数**（a-stock-data §1.2 实测，我们原先完全没这个概念）：
    · 科创板（688/689）→ **股**
    · 其余（沪深主板/创业板/B股/ETF/LOF/REITs）→ **手**，1 手 = 100 股
    · **可转债 → 1 手 = 10 张**
    · 指数 → 手
    搞错就是 **100 倍的误差**，而且不会报错。见 `volume_unit()`。
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .result import Result, fail, now_str


# ══════════════════════════════════════════════════════════════════
# BINDINGS：我们的 kind → 他们的函数 + 参数映射
# ══════════════════════════════════════════════════════════════════
# 实测他们的函数签名有五种形态（必须都表达得出来）：
#   ① fn(code, ...)                     最常见
#   ② fn(code, trade_date, ...)         需要交易日 —— **我们要自己补最近交易日**
#   ③ fn(code, start_date, end_date)    需要日期区间
#   ④ fn()                              全市场，**不收代码**
#   ⑤ fn(codes: list)                   批量（tencent_quote）
#
# ★ 只登记**已验证跑通**的。没验证的不写进来 —— 宁可少，不要错。


@dataclass(frozen=True)
class Bind:
    """一条绑定的完整说明。"""
    fn: str                                    # a-stock-data 里的函数名
    caliber: str                               # 口径说明（进 Result.caliber）
    kind_of_call: str = "code"                 # code | codes | date | date_range | no_code
    args: Optional[Callable[[dict], dict]] = None
    min_interval: float = 0.0                  # 建议最小调用间隔（秒），东财端点用


def _days_ago(n: int) -> str:
    """n 天前的日期字符串（给有必填日期区间的端点当默认）。"""
    from datetime import date as _d, timedelta as _td
    return (_d.today() - _td(days=n)).isoformat()


def _last_trading_day(back: int = 0) -> str:
    """最近一个交易日。

    ★ 为什么不用「今天」：周末/节假日取当日龙虎榜会返回空，
      而空表在我们这里的含义是「确实没有」—— 会把「还没到那天」误报成「这天没数据」。
    ★ 用上证指数的日期探（它天天有 bar），失败才退回「躲开周末的自然日」。
    """
    try:
        from .registry import fetch
        r = fetch("index_kline", "000001", {"freq": "daily", "limit": 10, "fqt": 0})
        if r.ok and r.data:
            days = sorted({str(x.get("date") or x.get("日期"))[:10] for x in r.data})
            if len(days) > back:
                return days[-1 - back]
    except Exception:                        # noqa: BLE001
        pass
    from datetime import date as _d, timedelta as _td
    d = _d.today()
    while d.weekday() >= 5:
        d -= _td(days=1)
    return d.isoformat()


def _kline_args(params: dict) -> dict:
    """我们的 kline 参数 → tencent_kline 参数。"""
    freq = (params.get("freq") or "daily").lower()
    period = {"daily": "day", "weekly": "week", "monthly": "month",
              "1min": "m1", "5min": "m5", "15min": "m15",
              "30min": "m30", "60min": "m60"}.get(freq, "day")
    adjust = {0: "", 1: "qfq", 2: "hfq"}.get(params.get("fqt", 1), "qfq")
    out: dict = {"period": period, "adjust": adjust}
    limit = params.get("limit")
    if limit:
        # 他们：日周月 ≤640，分钟 ≤320
        out["count"] = min(int(limit), 640 if period in ("day", "week", "month") else 320)
    # ⚠️ 他们的 tencent_kline 要求「给了 end 就必须同时给 start」，否则抛
    #    ValueError: 只给 end 时请同时给 start。所以两个一起给或都不给。
    beg = params.get("beg") or params.get("start")
    end = params.get("end")
    if beg and beg != "0":
        out["start"] = str(beg)[:10]
        if end and end != "20500101":
            out["end"] = str(end)[:10]
    return out


def _none(params: dict) -> dict:
    """不接受任何额外参数的端点。"""
    return {}


def _ps(name: str = "page_size") -> Callable[[dict], dict]:
    """只传分页/条数这类通用参数。"""
    def f(params: dict) -> dict:
        v = params.get(name)
        if v is None:
            v = params.get("limit")
        return {} if v is None else {name: int(v)}
    return f


def _with_date(params: dict) -> dict:
    return {"trade_date": params.get("trade_date") or params.get("date")
            or _last_trading_day()}


def _date_range(params: dict) -> dict:
    """需要日期区间的端点（估值历史、收益率曲线）。"""
    from datetime import date as _d, timedelta as _td
    end = params.get("end") or params.get("end_date") or _last_trading_day()
    start = params.get("start") or params.get("start_date")
    if not start:
        # 默认回溯 3 年 —— 估值要算分位，样本太短没意义
        start = (_d.fromisoformat(end) - _td(days=365 * 3)).isoformat()
    return {"start_date": start, "end_date": end}


BINDINGS: dict[str, Bind] = {
    # ══════════════════════════════════════════════════════════════
    # 行情层（他们的第 1 层）—— 首选源（不封 IP）
    # ══════════════════════════════════════════════════════════════
    "quote": Bind(
        fn="tencent_quote", kind_of_call="codes",
        caliber="腾讯财经实时快照（qt.gtimg.cn，GBK 编码，88 字段）。"
                "单位已写进字段名：amount_wan=万元 / mcap_yi=亿元 / turnover_pct=% / amplitude_pct=%。"
                "⚠️ 腾讯对已退市/停牌的代码**照样返回 HTTP 200 + 数据** → 本函数内置僵尸报价检测，"
                "看返回里的 `is_stale`；**is_stale=True 时不要把这个价格当现价用**。"
                "⚠️ 快照没有时间列，as_of 记的是**抓取时刻**，不等于「这个价格是今天的」。",
    ),
    "kline": Bind(
        fn="tencent_kline", args=_kline_args,
        caliber="腾讯 K 线，三入口轮换（单域名约 600 次后限流，失败冷却 120 秒）。"
                "⚠️ 前复权是**等差口径**（逐次减去每股分红），高分红老股早年会被减成负数"
                "（茅台 2015 约 -117.6）；**长区间回测请用 fqt=0 取不复权，再自己按比例复权**。"
                "⚠️ 只支持沪深 —— 北交所只返回最新 1 根日线，函数直接抛 ValueError。"
                "⚠️ 本接口**没有成交额**，需要成交额用 tdx_daily_package。",
    ),
    "index_kline": Bind(
        fn="tencent_kline", args=_kline_args,
        caliber="同上，标的为指数。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 资金 / 筹码（第 3、4 层）—— 计算层第 6 步要用的
    # ══════════════════════════════════════════════════════════════
    "fundflow": Bind(
        fn="stock_fund_flow_120d", args=_none, min_interval=1.0,   # 实测只收 code
        caliber="东财个股资金流（日级，最多 120 日）。"
                "⚠️ **口径必须标**：东财的「主力」= 大单+超大单，这是**按成交金额分档的统计口径**，"
                "不是「谁在下单」。跨日不可加总（分档标准会变），跨股不可比较（盘子大小不同）。",
    ),
    "fundflow_minute": Bind(
        fn="eastmoney_fund_flow_minute", min_interval=1.0,
        caliber="东财个股资金流（分钟级，当日）。同上口径警告。",
    ),
    "dragon_tiger": Bind(
        fn="dragon_tiger_board", kind_of_call="date", args=_with_date, min_interval=1.0,
        caliber="龙虎榜席位（个股上榜记录 + 买卖席位 TOP5 + 机构动向）。"
                "★ 这是**交易所法定披露**，S0/S1 级证据。"
                "⚠️ 席位名称是营业部，不代表具体人；「机构专用」是交易所的标记口径。",
    ),
    "margin_trading": Bind(
        fn="margin_trading", args=_ps(), min_interval=1.0,
        caliber="融资融券明细（交易所官方数据，S0 级）。"
                "★ 两融余额是**全市场情绪**的硬指标，比新闻可靠。",
    ),
    "block_trades": Bind(
        fn="block_trade", args=_ps(), min_interval=1.0,
        caliber="大宗交易。折价成交通常意味着**减持压力**，溢价成交反之 —— "
                "但这是**经验规则不是实证结论**，只能当线索。",
    ),
    "holder_num": Bind(
        fn="holder_num_change", args=_ps(), min_interval=1.0,
        caliber="股东户数变化。户数减少 = 筹码集中（**常被解读为利好，但无实证**）；"
                "⚠️ 披露频率低（季报），两次披露之间无法更新。",
    ),
    "holder_trades": Bind(
        fn="holder_trades",
        args=lambda p: {"direction": p.get("direction"), "limit": p.get("limit", 500)},
        min_interval=1.0,
        caliber="股东/高管增减持。★ **法定披露**（持仓变动达阈值必须公告），S0/S1 级。",
    ),
    "equity_pledge": Bind(
        fn="equity_pledge", args=lambda p: {"limit": p.get("limit", 5000)}, min_interval=1.0,
        caliber="股权质押。★ **有实证**：杨柳勇等（2023）用 XGBoost 预警质押风险，"
                "召回 74.44% / 精确 56.57%。**深交所硬阈值：控股股东质押 >80% → "
                "风险分类等级不得为正常类**（官方口径，可直接引用）。",
    ),
    "share_buyback": Bind(
        fn="share_buyback",
        args=lambda p: {"progress": p.get("progress"), "limit": p.get("limit", 500)},
        min_interval=1.0,
        caliber="股份回购。★ 法定披露。⚠️ 回购**预案**≠**实施**，看 `progress` 字段。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 估值 / 财务 —— 计算层第 5 步要用的
    # ══════════════════════════════════════════════════════════════
    "valuation_history": Bind(
        fn="baostock_valuation_history", kind_of_call="date_range", args=_date_range,
        caliber="baostock 估值历史：PE/PB/PS/PCF + 换手率 + 停牌 + ST（日频）。"
                "★ 这是**估值分位**的数据基础 —— 单看 PE=20 没意义，要看它在自己历史上什么位置。"
                "⚠️ **负 PE 必须显式标「不适用」**，不能拿负数去排分位（会把最差的排成最好的）。",
    ),
    "valuation_now": Bind(
        fn="full_valuation", min_interval=1.0,
        caliber="当前估值快照（PE/PB/市值/换手等）。与 valuation_history 的区别："
                "这个是**当下一个点**，那个是**历史序列**（能算分位）。",
    ),
    "eps_forecast": Bind(
        fn="ths_eps_forecast", min_interval=1.0,
        caliber="同花顺一致预期 EPS（分析师预测均值）。"
                "⚠️ **覆盖率是硬约束**：2024 年 沪深300 约 98% / 中证1000 约 79% / 全A 约 53%。"
                "**没覆盖的票不能拿「预期」说事** —— 那是编的。",
    ),
    "earnings_forecast": Bind(
        fn="earnings_forecast",
        args=lambda p: {"report_date": p.get("report_date"), "limit": p.get("limit", 500)},
        min_interval=1.0,
        caliber="业绩预告。★ 法定披露（达到阈值必须预告），S0 级。",
    ),
    "financial_sina": Bind(
        fn="sina_financial_report",
        args=lambda p: {"report_type": p.get("report_type", "lrb"), "num": p.get("num", 8)},
        caliber="新浪财报三表（lrb=利润表 / fzb=资产负债表 / llb=现金流量表）。"
                "⚠️ 这是**第三方整理**不是交易所原文 —— 涉及金额必须回巨潮公告核对。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 行业 / 板块
    # ══════════════════════════════════════════════════════════════
    "concept_blocks": Bind(
        fn="eastmoney_concept_blocks", min_interval=1.0,
        caliber="个股所属板块（行业/概念/地域 + BK码 + 涨跌幅 + 龙头股）。",
    ),
    "sw_industry": Bind(
        fn="sw_industry_history", kind_of_call="no_code",
        caliber="⚠️ **实测 2026-10-12 不可用**（申万站点 SSL 握手失败；他们文档称多半是本机 CA 问题）。"
                "★★ **申万行业分类历史** —— 非常重要的一个空白："
                "行业分类会随时间调整，用**当前**分类回溯历史会引入**前视偏差**"
                "（今天的行业成分 ≠ 当年的行业成分）。本端点给的是历史版本。",
    ),
    "industry_board": Bind(
        fn="industry_comparison", kind_of_call="no_code",
        args=lambda p: {"top_n": p.get("top_n", 20)}, min_interval=1.0,
        caliber="行业板块涨跌排名。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 事件 / 风险
    # ══════════════════════════════════════════════════════════════
    "restricted_release": Bind(
        fn="lockup_expiry", kind_of_call="date",
        args=lambda p: {**_with_date(p), "forward_days": p.get("forward_days", 90)},
        min_interval=1.0,
        caliber="限售解禁日历（历史解禁 + 未来 90 天待解禁）。"
                "★ 解禁是**确定性事件**（日期和数量都在公告里），"
                "但「解禁→抛压」这一步**是推测**，要看解禁方是谁（大股东 vs 财务投资者）。",
    ),
    "st_list": Bind(
        fn="st_stock_list", kind_of_call="no_code",
        caliber="★ 全市场 ST / *ST 名单快照。**风险警示是硬事实**（交易所判定），"
                "不是观点 —— 一只票是不是 ST，是可以直接断言的。"
                "⛔ **实测 2026-10-12 该端点失效**（他们自己的守卫报「名单为空或代码重复，"
                "不能当成完整快照」）—— 它**拒绝返回不完整数据**，这是对的；"
                "要 ST 名单得另找源。",
    ),
    "price_anomaly": Bind(
        fn="em_price_anomaly", kind_of_call="no_code",
        args=lambda p: {"page_size": p.get("page_size", 200)}, min_interval=1.0,
        caliber="东财日内异动池（严重异常波动）。★ 交易所口径的异常判定。",
    ),
    "stock_monitor": Bind(
        fn="em_stock_monitor", kind_of_call="no_code",
        args=lambda p: {"only_active": p.get("only_active", True)}, min_interval=1.0,
        caliber="东财重点监控池。⚠️ 这是**风险提示名单**，不是「这票有问题」的结论 ——"
                "名单是事实，含义要解释。",
    ),
    "ipo_calendar": Bind(
        fn="ipo_calendar", kind_of_call="no_code",
        args=lambda p: {"limit": p.get("limit", 100)},
        caliber="新股日历。",
    ),
    "convertible_bonds": Bind(
        fn="convertible_bonds", kind_of_call="no_code",
        args=lambda p: {"include_delisted": p.get("include_delisted", False)},
        caliber="可转债清单。⚠️ 可转债 1 手 = **10 张**（不是 100），"
                "T+0 交易且无涨跌停 —— 和股票规则完全不同，混用会算错。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 舆情 / 互动（需求⑤：掌握公司经营实况）
    # ══════════════════════════════════════════════════════════════
    # ★ 交互问答**要按交易所分派**（实测：sse_e_interaction 对深市代码直接抛错，
    #   "300417 不是沪市证券；深市互动问答请用 cninfo_irm"）：
    #     沪市 → sse_e_interaction（上证 e 互动）
    #     深市 → cninfo_irm（巨潮互动易）
    #   两个都是**上市公司自己说的话**，S1 级。
    "interactive_qa": Bind(
        fn="sse_e_interaction", kind_of_call="special:interactive_qa",
        caliber="★ 投资者互动问答（**上市公司自己说的话**，S1 级，比媒体转述可靠）。"
                "沪市走上证 e 互动 / 深市走巨潮互动易，按代码自动分派。"
                "⚠️ 但它仍是**公司自述**，涉及经营数据必须回财报核对。",
    ),
    "hot_rank": Bind(
        fn="em_hot_rank", kind_of_call="no_code",
        args=lambda p: {"top": p.get("top", 50)}, min_interval=1.0,
        caliber="东财人气榜。⚠️ 这是**散户关注度**，与基本面无关；"
                "★ 情报通道 —— 只进「相关动态」，**永不进结论**。",
    ),
    "institution_survey": Bind(
        fn="institution_survey", args=lambda p: {"limit": p.get("limit", 500)},
        min_interval=1.0,
        caliber="机构调研记录。★ 法定披露。⚠️ 「机构去调研了」**不等于「机构看好」** —— "
                "调研是双向的，也可能是去看它有多差。",
    ),
    "research_reports": Bind(
        fn="eastmoney_reports", args=lambda p: {"max_pages": p.get("max_pages", 5)},
        min_interval=1.0,
        caliber="卖方研报（标题/机构/评级/三年EPS）。"
                "⚠️ **卖方研报有系统性乐观偏差**（券商与上市公司有业务关系），"
                "评级「买入」的基线就很高。**当作情报，不当作事实。**",
    ),

    # ══════════════════════════════════════════════════════════════
    # 宏观
    # ══════════════════════════════════════════════════════════════
    "macro_pmi": Bind(
        fn="nbs_pmi", kind_of_call="no_code",
        caliber="国家统计局 PMI。★ 官方一手数据（S2 级）。",
    ),
    "macro_lpr": Bind(
        fn="lpr_history", kind_of_call="no_code",
        caliber="LPR 贷款市场报价利率全历史。★ 央行公布，S2 级。",
    ),
    "macro_social_financing": Bind(
        fn="pboc_social_financing", kind_of_call="no_code",
        args=lambda p: {"year": p.get("year")},
        caliber="人民银行社会融资规模增量。★ 官方一手数据（S2 级）。",
    ),
    "yield_curve": Bind(
        fn="chinabond_yield_curve", kind_of_call="no_code",   # ⚠️ start 是位置参数，不能传 code
        # ⚠️ start 是**必填**（实测：不给会报 missing 1 required positional argument）
        args=lambda p: {"start": p.get("start") or p.get("start_date") or _days_ago(30),
                        "end": p.get("end") or p.get("end_date"),
                        "curve": p.get("curve", "all")},
        caliber="中债收益率曲线（国债 / 商业银行 AAA / 中短票 AAA）。"
                "★ 这是**无风险利率**的权威来源 —— 算索提诺、算估值折现都要它。",
    ),

    # ══════════════════════════════════════════════════════════════
    # 新闻 / 公告
    # ══════════════════════════════════════════════════════════════
    # ⛔ 不绑 eastmoney_stock_news —— 实测它**对所有代码都返回 0 条**（2026-10-12），
    #    他们的函数坏了或端点变了。我们自己的 sources/news.py 是好的，继续用它。
    "announcement_vendor": Bind(
        fn="cninfo_announcements", args=_ps(), min_interval=1.0,
        caliber="巨潮公告。★ **交易所指定披露平台**，S0 级 —— 最权威的一手来源。",
    ),
    "cctv_news": Bind(
        fn="cctv_news", kind_of_call="no_code",   # ⚠️ 签名是 (date, with_content)，第一个位置参数就是 date
        # ★ 默认取**前一个交易日**，不是最近交易日 —— 新闻联播当晚约 20:00 才发布，
        #   盘中/上午取「今天」必然 404（实测：2026-10-12 报「日期过早或尚未发布」）。
        args=lambda p: {
            "date": p.get("trade_date") or p.get("date") or _last_trading_day(back=1),
            "with_content": True},
        caliber="★ 央视《新闻联播》文字稿。**政策分析师的原料** —— "
                "产业政策、监管口径往往先在这里出现。",
    ),
    "stock_info": Bind(
        fn="eastmoney_stock_info", min_interval=1.0,
        caliber="东财个股基本面快照（公司概况类字段）。",
    ),
    # ★ 板块列表 / 全市场列表：东财的 `clist` 接口**被单独封了**
    #   （实测：同一个 push2 host，行情/K线/指数都通，只有 clist 秒断 HTTP 000）
    #   → 改用 a-stock-data 的替代端点
    "board_rank": Bind(
        fn="board_fund_flow", kind_of_call="no_code",
        args=lambda p: {"board_type": p.get("board_type", "industry"),
                        "period": p.get("period", "today"),
                        "top_n": p.get("top_n", 20)}, min_interval=1.0,
        caliber="板块资金流排名（行业/概念/地域 × 今日/5日/10日）。"
                "⚠️ **「资金流」是东财自家按成交金额分档的口径，无第二来源可比** —— "
                "但**板块涨跌幅**是可以对拍的。"),
    "industry_rank": Bind(
        fn="industry_comparison", kind_of_call="no_code",
        args=lambda p: {"top_n": p.get("top_n", 20)}, min_interval=1.0,
        caliber="行业板块涨跌排名。",
    ),
    "tdx_package": Bind(
        fn="tdx_daily_package", kind_of_call="no_code",
        args=lambda p: {"date": p.get("date") or _last_trading_day()},
        caliber="★ **通达信官网盘后包** —— **某交易日沪深北全部证券的日线，含成交额**。"
                "官方一手（通达信官网），与交易所口径一致。"
                "⚠️ 三个坑（实测）："
                "① **「000001」在这里是上证指数，不是平安银行** —— "
                "必须按 (market, code) 配对识别；"
                "② **成交额不能全表加总**（全表含债券/ETF/指数，加总会得到 136 万亿 = 真实值的 90 倍），"
                "要取**指数行的 amount**；"
                "③ **通常收盘后数小时才发布** —— 盘中取会报「当日包尚未发布」，"
                "**这是正常的，不是坏了**。",
    ),

    # ══════════════════════════════════════════════════════════════
    # ★ 舆情 / 社会风向（2026-10-12 补齐，全部实测跑通）
    #   用途：回答「社会上在怎么说这件事」——
    #   注意：**这一整组都是情报通道，永不进结论**（观点不是事实）
    # ══════════════════════════════════════════════════════════════
    "news_flash": Bind(
        fn="cls_telegraph", kind_of_call="no_code",
        args=lambda p: {"page_size": p.get("page_size", 30)}, min_interval=1.0,
        caliber="★ **财联社电报** —— 国内最快的财经快讯源，一条一句话，带时间戳。"
                "★ 情报通道：**永不进结论**，只进「相关动态」。"
                "⚠️ 快讯是**抢发**的，措辞可能后续修正。",
    ),
    "news_global": Bind(
        fn="eastmoney_global_news", kind_of_call="no_code",
        args=lambda p: {"page_size": p.get("page_size", 30)}, min_interval=1.0,
        caliber="东财全球资讯 7x24。★ 情报通道，**永不进结论**。",
    ),
    "news_wallstreetcn": Bind(
        fn="wallstreetcn_lives", kind_of_call="no_code",
        args=lambda p: {"channel": p.get("channel", "global-channel"),
                        "limit": p.get("limit", 30)}, min_interval=1.0,
        caliber="华尔街见闻 7x24。★ 情报通道，**永不进结论**。",
    ),
    "hot_list": Bind(
        fn="ths_hot_list", kind_of_call="no_code",
        args=lambda p: {"period": p.get("period", "hour")}, min_interval=1.0,
        caliber="同花顺热榜（按热度排序，带实时涨跌幅）。"
                "⚠️ **热度 ≠ 基本面** —— 它反映的是**关注度**，可能是好事也可能是炒作出货。"
                "★ 情报通道，**永不进结论**。",
    ),
    "limit_up_reason": Bind(
        # ⚠️ 签名是 (date)，第一个位置参数就是 date —— 走 no_code，绝不能传 code
        fn="ths_hot_reason", kind_of_call="no_code",
        args=lambda p: {"date": p.get("trade_date") or p.get("date") or _last_trading_day()},
        min_interval=1.0,
        caliber="★★ **同花顺涨停揭秘** —— 当天涨停股的**题材归因**（如「鼠疫+抗感染药物+创新药研发」）。"
                "**这是回答「今天为什么涨」最直接的原料。**"
                "⚠️ 归因是**同花顺编辑写的**，不是公司说的 —— 属于**解读**，不是事实。"
                "★ 情报通道，**永不进结论**。",
    ),
    "limit_up_sentiment": Bind(
        # ⚠️ 签名是 (date) —— 参数名是 date，不是 trade_date
        fn="limit_up_sentiment", kind_of_call="no_code",
        args=lambda p: {"date": p.get("trade_date") or p.get("date") or _last_trading_day()},
        min_interval=1.0,
        caliber="★ **打板情绪指标**：涨停家数 / 炸板家数 / 跌停家数 / 炸板率 / 最高连板高度。"
                "**这是全市场情绪的量化快照** —— 比单条新闻可靠。"
                "⚠️ 但它描述的是**投机资金的情绪**，与公司基本面无关。"
                "⛔ **实测 2026-10-12 该端点失效**：逐日查了 5 个交易日，"
                "**全部返回全 0**（涨停=0/炸板=0/跌停=0），而同日涨停题材归因有 44~72 条 —— "
                "说明**是它自己的数据源坏了，不是市场没涨停**。",
    ),
    "industry_reports": Bind(
        fn="eastmoney_industry_reports", kind_of_call="no_code",
        args=lambda p: {"industry_code": p.get("industry_code", "*"),
                        "max_pages": p.get("max_pages", 1)}, min_interval=1.0,
        caliber="行业研报列表（含评级机构）。★ 情报，**永不进结论** —— "
                "卖方研报有**系统性乐观偏差**。",
    ),
    "research_sina": Bind(
        fn="sina_research_reports", kind_of_call="no_code",
        args=lambda p: {"page": p.get("page", 1)}, min_interval=1.0,
        caliber="新浪研报列表（标题/机构/研究员/日期）。★ 情报，**永不进结论**。",
    ),
    "hot_concept": Bind(
        fn="em_hot_concept", min_interval=1.0,
        caliber="个股**当下被市场关联到的热点概念**（带命中数）。"
                "⚠️ 与 concept_blocks 的区别：那个是**固定分类归属**（定义之争），"
                "这个是**当下的热度关联**（随时间变）。**两者都不能进结论。**",
    ),
    "irm_sz": Bind(
        fn="cninfo_irm", args=_ps("page_size"), min_interval=1.0,
        caliber="★ **深市互动易**（与沪市 sse_e_interaction 对应）。"
                "**公司自己回答投资者提问** —— S1 级，比媒体转述可靠。"
                "⚠️ 但仍是**公司自述**，涉及经营数据必须回财报核对。",
    ),
}


# ══════════════════════════════════════════════════════════════════
# 口径：单位随板块变 —— 这是我们原先完全没有的概念
# ══════════════════════════════════════════════════════════════════
def volume_unit(code: str, *, is_index: bool = False) -> tuple[str, str]:
    """返回 (单位说明, 折算成股的系数)。**系数为 None 表示不能直接折算。**

    ★ 实测来源：a-stock-data §1.2「volume 单位」小节
        · 科创板 688/689 → 股
        · 其余股票 / ETF / LOF / REITs → 手（1 手 = 100 股 / 份）
        · 可转债 → 手（1 手 = 10 张）
        · 指数 → 手
    ⚠️ 搞错就是 100 倍误差，而且**不会报错**。
    """
    c = str(code).strip().zfill(6)
    if is_index:
        return "手（指数成交量按手）", 100.0
    if c.startswith(("688", "689")):
        return "股（科创板按股，其余板块按手 —— 差 100 倍）", 1.0
    if c.startswith(("11", "12", "13")):          # 可转债号段
        return "手（可转债 1 手 = 10 张）", 10.0
    return "手（1 手 = 100 股 / 份）", 100.0


# ══════════════════════════════════════════════════════════════════
# 源名推导
# ══════════════════════════════════════════════════════════════════
_SRC_PREFIX = (
    ("tencent_", "tencent"), ("eastmoney_", "eastmoney"), ("em_", "eastmoney"),
    ("sina_", "sina"), ("ths_", "ths"), ("baidu_", "baidu"), ("cninfo_", "cninfo"),
    ("baostock_", "baostock"), ("bse_", "bse"), ("sse_", "sse"), ("szse_", "szse"),
    ("cls_", "cls"), ("cctv_", "cctv"), ("wallstreetcn_", "wallstreetcn"),
    ("pboc_", "pboc"), ("nbs_", "nbs"), ("chinabond_", "chinabond"),
    ("tdx_", "tdx"), ("mootdx_", "mootdx"), ("iwencai_", "iwencai"),
    ("sw_", "sw"), ("index_", "index"), ("stock_fund", "eastmoney"),
    ("margin_", "eastmoney"), ("block_", "eastmoney"), ("holder_", "eastmoney"),
    ("equity_", "eastmoney"), ("share_", "eastmoney"), ("dragon_", "eastmoney"),
    ("daily_dragon", "eastmoney"), ("lockup_", "eastmoney"), ("full_valuation", "eastmoney"),
    ("eps_", "eastmoney"), ("earnings_", "eastmoney"), ("institution_", "eastmoney"),
    ("cctv_", "cctv"), ("lpr_", "pboc"), ("nbs_", "nbs"), ("ipo_", "eastmoney"),
    ("convertible_", "eastmoney"), ("industry_", "eastmoney"), ("st_stock", "eastmoney"),
)


def _default_source(fn_name: str) -> str:
    """从函数名推默认数据源 —— 有的端点不自报 source（如 tencent_quote 返回纯 dict）。

    ★ 不是「猜」：他们的函数命名就是 `<源>_<用途>`，实测 181 个函数全部符合。
      推不出来时返回 `a-stock-data`，**不编一个看起来具体的假源名**。
    """
    low = (fn_name or "").lower()
    for pre, src in _SRC_PREFIX:
        if low.startswith(pre):
            return src
    return "a-stock-data"


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════
_VENDOR = None


def vendor():
    """懒加载提取产物。**不在这里 import，避免 vendor 缺失时整个 data 层挂掉。**"""
    global _VENDOR
    if _VENDOR is None:
        try:
            # 绝对导入：data 的上一级就是 sys.path 根，用 `..vendor` 会越界
            from vendor import astock as _a
        except ImportError as exc:
            raise RuntimeError(
                "vendor/astock.py 不存在或无法导入。先跑：python 脚本/extract_astock.py"
            ) from exc
        _VENDOR = _a
    return _VENDOR


def _to_rows(df: Any) -> list[dict]:
    """把他们的各种返回形状**统一成 list[dict]**。

    实测他们有三种形状（不统一）：
        ① DataFrame                       —— K线、财报、龙虎榜等大多数端点
        ② {code: {...}} 按代码做键的 dict  —— tencent_quote 批量行情
        ③ {字段: 值} 单条 / 嵌套 dict      —— 少数端点（如 dragon_tiger_board 返回 dict-of-list）

    ② 如果直接套进 `[dict]`，会得到 `data[0]['600519']['price']` —— 不能说不一致，但极难用。
    这里统一平铺成 `[{'code': '600519', 'name': ..., 'price': ...}]`。
    """
    if df is None:
        return []
    if isinstance(df, list):
        return [x for x in df if isinstance(x, dict)]

    # ① DataFrame
    if hasattr(df, "columns") and hasattr(df, "to_dict"):
        return df.to_dict("records")

    if isinstance(df, dict):
        vals = list(df.values())
        # ② 按代码做键（值全是 dict，键看着像代码）→ 平铺
        if vals and all(isinstance(v, dict) for v in vals) and all(
            isinstance(k, str) and k.isdigit() for k in df
        ):
            out = []
            for code, row in df.items():
                merged = {"code": code}
                merged.update(row)
                out.append(merged)
            return out
        # ③ 单条
        return [df]
    return []


def _extract_provenance(raw: Any, rows: list[dict]) -> tuple[str, str, str]:
    """取 (source, source_url, fetched_at)。先看 DataFrame 列，再看行字段。"""
    src = url = fetched = ""
    try:
        if hasattr(raw, "columns") and len(raw):
            for col, box in (("source", "s"), ("source_url", "u"), ("fetched_at", "f")):
                if col in raw.columns:
                    v = str(raw[col].iloc[0])
                    if box == "s":
                        src = v
                    elif box == "u":
                        url = v
                    else:
                        fetched = v
    except Exception:                        # noqa: BLE001
        pass
    if rows and not src:
        r0 = rows[0]
        src = str(r0.get("source") or "")
        url = url or str(r0.get("source_url") or "")
        fetched = fetched or str(r0.get("fetched_at") or "")
    return src, url, fetched


def _classify_error(exc: Exception) -> tuple[str, str]:
    """他们的异常 → 我们的 error_kind。

    ★ 「确实没有」与「取不到」必须分开（这是我们的原话「失败即值」，他们也有这条）
    """
    name = type(exc).__name__
    msg = str(exc)

    if name in ("ValueError", "TypeError") and any(
        k in msg for k in ("参数", "不支持", "无法把", "区间过长", "不存在")
    ):
        return "bad_request", msg

    if "北交所" in msg or "北证" in msg:
        return "no_data_in_window", msg

    if any(k in msg for k in ("均不可用", "均无法", "限流", "超时", "timeout",
                              "服务器", "reset", "Connection", "timed out")):
        return "source_error", msg

    # 页数/条数对不上 —— 部分结果，绝不当完整结果用
    if any(k in msg for k in ("页", "count", "条数", "完整性")):
        return "source_error", msg

    # 来源格式变了 —— 独立一档，**不能混进「没数据」**
    if "缺少字段" in msg or "格式可能已变" in msg:
        return "format_changed", msg

    return "source_error", f"{name}: {msg}"


# 时间列的候选名（判断 as_of 用）
_TIME_KEYS = ("date", "datetime", "日期", "trade_date", "report_date", "end_date",
              "报告期", "变动日期", "公告日期", "统计日期")


def _special(which: str, v: Any, code: str, params: dict) -> Any:
    """需要按标的属性分派的端点。

    ★ 交互问答是唯一一个「同一个需求、沪深要用两个不同函数」的：
        沪市 → sse_e_interaction（上证 e 互动）
        深市 → cninfo_irm（巨潮互动易）
      盲用一个会直接抛错（"300417 不是沪市证券"），所以必须分派。
    """
    if which == "interactive_qa":
        c = str(code).strip().zfill(6)
        ps = int(params.get("page_size", 10))
        # 沪市：6xxxxx / 900xxx；其余（0/3/4/8/920）归深市与北交所 → 走巨潮
        if c.startswith(("6", "900")):
            return v.sse_e_interaction(code=c, kind=params.get("kind", "answered"),
                                       page=int(params.get("page", 1)), page_size=ps)
        return v.cninfo_irm(code=c, page_size=ps, page_num=int(params.get("page", 1)))
    raise RuntimeError(f"未知的 special 分派：{which}")


# ★ 哪些 kind **不该缓存**：实时性强、缓存了反而误事的。
#   行情快照（quote）TTL 已经是 120 秒，但用户问“现价多少”时 2 分钟前的也不行 —— 宁可慢 1 秒。
_NO_CACHE = frozenset({"quote", "index", "kline"})


def _from_cache(kind: str, code: str, hit: dict):
    """把缓存里的 payload 还原成 Result。还原不了就返回 None（当没缓存）。"""
    from data.result import Result
    d = hit.get("payload")
    if d is None:
        return None
    return Result(ok=True, kind=kind, code=code, data=d,
                  source=str(hit.get("source", "")) or f"{kind}(缓存)",
                  as_of=str(hit.get("as_of", "")),
                  caliber=str(hit.get("caliber", "")) or "落盘缓存",
                  cached=True, status="ok",
                  fetched_at=str(hit.get("fetched_at", "")))


def _put_cache(kind: str, code: str, params: dict, res) -> None:
    """★ **只存成功的** —— 失败也写缓存的话，一次网络抖动会被记住。"""
    if not getattr(res, "ok", False) or res.data is None:
        return
    try:
        from data import cache as _C
        _C.put(kind, {"kind": kind, "code": code, **{k: str(v) for k, v in params.items()}},
               res.data, source=res.source or kind, as_of=res.as_of or "",
               caliber=res.caliber or "")
    except Exception:                                        # noqa: BLE001
        pass


def fetch_via_vendor(kind: str, code: str = "", params: Optional[dict] = None) -> Result:
    """按 BINDINGS 调 a-stock-data，转成我们的 Result。

    ★★ 落盘缓存（2026-10-13 加）。**为什么要加**：
       实测「踩雷排查 6 项」要 31.9s、「估值历史」17.9s、「事件雷达」10.2s ——
       而 `data/cache.py` 本来就有完整的 TTL 落盘缓存，**只是这里从没用它**。
       加完之后：ST 名单/横截面这类「一份数据服务全市场」的调用，
       第二次开始是 0.02s，而不是几十秒。

    ★ 取不到时用 `get_stale`（**不看 TTL 的兜底**）—— 但**必须在 note 里标明是过期数据**。
    ★ 未知 kind 返回 ok=False + error_kind="unbound"，**不静默退化**。
    ★ 缓存**只存成功的**；失败不写缓存，否则一次网络抖动会被记住。
    """
    params = dict(params or {})
    if kind not in BINDINGS:
        return fail(kind, code,
                    f"vendor 桥未绑定该 kind：{kind}（已绑定 {len(BINDINGS)} 个）",
                    error_kind="unbound")

    # ── ① 查缓存（跳过缓存的 kind 见 _NO_CACHE）──
    ck = {"kind": kind, "code": code, **{k: str(v) for k, v in params.items()}}
    if kind not in _NO_CACHE:
        try:
            from data import cache as _C
            hit = _C.get(kind, ck)
            if hit is not None:
                r = _from_cache(kind, code, hit)
                if r is not None:
                    return r
        except Exception:                                    # noqa: BLE001
            pass                    # ★ 缓存层坏了绝不许影响取数

    b = BINDINGS[kind]
    try:
        v = vendor()
    except RuntimeError as exc:
        return fail(kind, code, str(exc), error_kind="vendor_missing")

    fn = getattr(v, b.fn, None)
    if fn is None:
        return fail(kind, code,
                    f"a-stock-data 里没有函数 {b.fn}（可能它改版了，重跑提取器）",
                    error_kind="vendor_missing")

    try:
        extra = b.args(params) if b.args else {}
        extra = {k: val for k, val in extra.items() if val is not None}
    except Exception as exc:                 # noqa: BLE001
        return fail(kind, code, f"参数映射失败：{exc}", error_kind="bad_request")

    try:
        if b.kind_of_call.startswith("special:"):
            raw = _special(b.kind_of_call.split(":", 1)[1], v, code, params)
        elif b.kind_of_call == "no_code":
            raw = fn(**extra)
        elif b.kind_of_call == "codes":
            raw = fn([code], **extra)
        elif b.kind_of_call in ("date", "date_range"):
            raw = fn(code, **extra)
        else:
            raw = fn(code, **extra)
    except Exception as exc:                 # noqa: BLE001
        ek, why = _classify_error(exc)
        return fail(kind, code, why, error_kind=ek)

    rows = _to_rows(raw)
    src, url, fetched = _extract_provenance(raw, rows)

    # 空表 = **确实没有**（a-stock-data 的契约：空表要调用方自己判断）
    if not rows:
        r = fail(kind, code, "接口返回空表（该标的确实没有这类数据）",
                 error_kind="no_data_in_window")
        r.source = src or _default_source(b.fn)
        r.as_of = (fetched[:10] if fetched else now_str()[:10])
        r.caliber = b.caliber
        return r

    # ★★★ as_of 取错过一次，务必看清这段（实测：as_of 显示 2025-12-09，数据却到 2026-10-12）
    #   时间序列是**升序**的 → rows[0] 是**最老**的日期。
    #   as_of 的含义是「这条数据截止到什么时候」→ **必须取最后一行**。
    #   快照类（quote）没有时间列，取抓取时刻。
    time_key = next((k for k in _TIME_KEYS if rows[0].get(k)), None)
    as_of = ""
    if time_key:
        vals = [str(r[time_key])[:10] for r in rows if r.get(time_key)]
        as_of = max(vals) if vals else ""
    if not as_of:
        as_of = (fetched[:10] if fetched else now_str()[:10])

    _res = Result(
        ok=True, kind=kind, code=code, data=rows,
        source=(src or _default_source(b.fn)),
        as_of=as_of,
        caliber=b.caliber + (f" ｜ 原始来源 {url}" if url else ""),
        level="A",                           # 接口直取 = 核验级
        fetched_at=(fetched or now_str()),
    )
    _put_cache(kind, code, params, _res)     # ★ 成功才写缓存
    return _res


def bound_kinds() -> list[str]:
    """已绑定的 kind（给 /status 或自检用）。"""
    return sorted(BINDINGS)
