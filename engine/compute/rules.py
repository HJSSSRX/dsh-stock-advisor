# -*- coding: utf-8 -*-
"""A 股制度规则时间表 —— 计算层的地基。

★★★ 为什么必须自建 ★★★
开源里没有一家做对了。`hikyuu`（3,558★，C++ 指标引擎，自称"A 股制度原生支持"）
在自己的文档里承认：

    "跌停幅度按各板块**现行**规则取值，**不按历史日期分段** ——
     2020-08-24 之前的创业板实际为 10%、新股上市初期没有涨跌幅限制，
     这两种情形仍按现行幅度判定"

并且 **ST 的 5% 因缺历史 ST 标识"暂未处理"**。

→ 后果：拿创业板 2019 年的数据按 20% 判定涨跌停，会**系统性算错**。
   实测影响：判断"是否涨停"会漏判一大批（10% 就该封板的日子）。

本模块把规则**按日期分段**，并显式暴露"不确定"的情况。

来源：沪深北交易所交易规则历次修订。**凡没把握的一律标 uncertain，不猜。**
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Board(str, Enum):
    """板块。判定顺序：北交所 → 科创板 → 创业板 → 主板。"""
    MAIN = "main"          # 沪深主板（含原中小板，2021-04 已合并）
    GEM = "gem"            # 创业板 300xxx
    STAR = "star"          # 科创板 688xxx
    BSE = "bse"            # 北交所 8xxxxx / 4xxxxx / 920xxx
    UNKNOWN = "unknown"


class Status(str, Enum):
    """股票的监管状态。**历史 ST 标识我们拿不到**（见 uncertainty_note）。"""
    NORMAL = "normal"
    ST = "st"              # ST / *ST
    DELISTING = "delisting"  # 退市整理期
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LimitRule:
    """一个时段内某板块的涨跌幅限制。"""
    limit_pct: Optional[float]     # 0.20 = 20%；None = 无涨跌幅限制
    note: str = ""
    uncertain: bool = False        # 标记"这条规则我不确定"


# ══════════════════════════════════════════════════════════════════
# 一、板块判定（按代码段）
# ══════════════════════════════════════════════════════════════════
def board_of(code: str) -> Board:
    """按证券代码判板块。

    ★ 实测踩过的坑（见 data/caliber.py）：900xxx 是沪市 B 股，920xxx 是北交所，
      不能一律按首位数字判。
    """
    c = str(code).strip()
    if not c.isdigit() or len(c) != 6:
        return Board.UNKNOWN
    if c.startswith(("920", "430", "83", "87", "88")):
        return Board.BSE
    if c.startswith("688"):
        return Board.STAR
    if c.startswith("300") or c.startswith("301"):
        return Board.GEM
    if c.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return Board.MAIN
    return Board.UNKNOWN


# ══════════════════════════════════════════════════════════════════
# 二、涨跌幅规则（按日期分段）
# ══════════════════════════════════════════════════════════════════
# 关键时间点：
#   2019-07-22  科创板开市，±20%
#   2020-08-24  创业板注册制改革，±10% → ±20%
#   2021-11-15  北交所开市，±30%
_GEM_REFORM = date(2020, 8, 24)
_STAR_OPEN = date(2019, 7, 22)
_BSE_OPEN = date(2021, 11, 15)


def limit_pct(code: str, on: date | datetime | str,
              status: Status = Status.NORMAL,
              *, new_listing_days: Optional[int] = None) -> LimitRule:
    """某只票在某一天的涨跌幅限制。

    参数
    ----
    code            证券代码
    on              日期（date / datetime / "YYYY-MM-DD"）
    status          监管状态。**默认 normal —— 因为历史 ST 标识我们拿不到**，
                    如果要算 ST 的 5%，调用方必须显式传。
    new_listing_days 上市天数（>0 表示刚上市）。None = 不知道，会标 uncertain。

    ★ 新股上市初期无涨跌幅限制 —— 但**具体天数按板块与时段不同**
      （主板首日 44%/无限制、创业板/科创板前 5 日无限制…），
      我们没有权威的完整对照表 → **一律标 uncertain，不猜**。
    """
    d = _to_date(on)
    b = board_of(code)

    # ── 新股上市初期：不确定，显式暴露 ──
    if new_listing_days is not None and new_listing_days <= 5:
        return LimitRule(None, f"上市第 {new_listing_days} 天，处于新股上市初期",
                         uncertain=True)

    # ── ST：5%（沪深主板口径）──
    if status == Status.ST:
        return LimitRule(0.05, "ST/*ST 股票", uncertain=(b in (Board.BSE, Board.STAR)))

    if status == Status.DELISTING:
        return LimitRule(0.10, "退市整理期（沪深主板口径）", uncertain=True)

    if b == Board.MAIN:
        return LimitRule(0.10, "沪深主板 ±10%")

    if b == Board.GEM:
        if d >= _GEM_REFORM:
            return LimitRule(0.20, "创业板注册制后 ±20%（2020-08-24 起）")
        return LimitRule(0.10, "创业板注册制前 ±10%（2020-08-24 之前）")

    if b == Board.STAR:
        if d < _STAR_OPEN:
            return LimitRule(None, "科创板开市前（2019-07-22）不存在该板块", uncertain=True)
        return LimitRule(0.20, "科创板 ±20%")

    if b == Board.BSE:
        if d < _BSE_OPEN:
            return LimitRule(None, "北交所开市前（2021-11-15）", uncertain=True)
        return LimitRule(0.30, "北交所 ±30%")

    return LimitRule(None, "板块判定失败，无法给出涨跌幅", uncertain=True)


def _to_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)[:10].replace("/", "-")
    return datetime.strptime(s, "%Y-%m-%d").date()


# ══════════════════════════════════════════════════════════════════
# 三、涨停/跌停判定（含一字板、T 字板）
# ══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class BarLimit:
    """一根 K 线的涨跌停状态。"""
    limit_pct: Optional[float]
    upper: Optional[float]      # 涨停价
    lower: Optional[float]      # 跌停价
    is_limit_up: bool = False
    is_limit_down: bool = False
    is_one_word: bool = False   # 一字板：开=高=低=收=涨停价
    is_t_word: bool = False     # T 字板：开=收=涨停价，但盘中有回落
    rule_note: str = ""
    uncertain: bool = False


def classify_bar(code: str, on, prev_close: float, o: float, h: float, l: float, c: float,
                 *, status: Status = Status.NORMAL,
                 new_listing_days: Optional[int] = None) -> BarLimit:
    """判定一根 K 线是否涨停/跌停/一字/T 字。

    ★★ 价格必须**先复权对齐再喂进来**（一律用同一口径），否则算出的涨停价是错的。
    ★ 涨停价按「前收盘 × (1 + 幅度)」**四舍五入到分** —— 这是交易所口径。
    """
    r = limit_pct(code, on, status, new_listing_days=new_listing_days)
    if r.limit_pct is None or not prev_close:
        return BarLimit(r.limit_pct, None, None, rule_note=r.note, uncertain=r.uncertain)

    up = round(prev_close * (1 + r.limit_pct) + 1e-9, 2)
    dn = round(prev_close * (1 - r.limit_pct) + 1e-9, 2)

    is_up = abs(c - up) < 0.005
    is_dn = abs(c - dn) < 0.005
    one_word = is_up and abs(o - up) < 0.005 and abs(h - up) < 0.005 and abs(l - up) < 0.005
    t_word = is_up and abs(o - up) < 0.005 and not one_word

    return BarLimit(r.limit_pct, up, dn, is_up, is_dn, one_word, t_word,
                    rule_note=r.note, uncertain=r.uncertain)


# ══════════════════════════════════════════════════════════════════
# 四、其他制度约束（计算层到处都要用）
# ══════════════════════════════════════════════════════════════════
TRADING_RULES = {
    "T+1": "当日买入的股票次一交易日才能卖出（A 股股票；ETF/可转债部分品种为 T+0）",
    "最小变动单位": "0.01 元",
    "最小买入单位": "100 股（1 手）",
    "印花税": "卖出方单边 0.05%（2023-08-28 起减半征收）",
    "停牌": "停牌期间无成交，K 线缺失或记 0 —— **必须用 Beta×市场收益填充，不能填 0 也不能丢**",
}

# 计算层各处都要引用的口径声明
CALIBER_NOTES = {
    "复权": (
        "前复权基准随最新价漂移 —— **同一只票在不同日期算出的『历史最高价』不一样**。"
        "因此：算『距历史新高』必须用**后复权**；算价格位置可用前复权但必须写明。"
    ),
    # ★★★ 这条是实测踩出来的，而且很反直觉，必须写在最显眼处 ★★★
    "制度判定必须用不复权价": (
        "**涨跌停/一字板/封单这类『制度性判定』必须用不复权价（fqt=0），不能用复权价。**"
        "原因：涨跌停是基于**真实成交价**的制度；复权价被分红送股调整过，比例就变了。\n"
        "    实测（南华仪器 300417，创业板 ±20%）：\n"
        "      · 不复权 → 最大单日涨幅 +20.00% / +19.98% / +19.97%  ✅ 精确对上涨停价\n"
        "      · 前复权 → +20.53% / +20.43% / +20.36%              ❌ 全部超过 20%，判定为『非涨停』\n"
        "      · 后复权 → +17.63% / +17.46%                        ❌ 全部不到 20%\n"
        "    → 用错口径的结果是**涨停日一个都检测不出来**（实测：复权口径下 7 次涨停全漏）。\n"
        "同时要记住对偶的一条：**算收益/波动/回撤必须用复权价**，否则除权日会出现假暴跌。"
        "**这两条口径不能混用，而且要在输出里声明用的是哪一种。**"
    ),
    "停牌": (
        "CSMAR 记缺失、WIND 记 0 收益，**两者都错**（Tian 2019：不修正会让因子收益偏差约 192 bp/年）。"
        "正确做法：用 Beta×市场收益填充，并披露填充天数；>30 天长停牌单独标为风险事件。"
    ),
    "涨跌停截断": (
        "涨跌停造成收益截断 → **Pearson 相关性被系统性低估，Spearman 不受影响**。"
        "两者都算，差距大就说明有截断污染，要明确告诉用户。"
    ),
    "主力资金口径": (
        "监管层从未给统一标准。**东财**：≥100万元/50万股超大单 + ≥20万元/10万股大单；"
        "**同花顺**：≥20万元大单、<6万元散户 —— **两家阈值差一个量级，结论可能完全相反**。"
        "只能说「按〈来源〉口径计的主力净流入为 X」，**禁止说「机构在买」**。"
    ),
}
