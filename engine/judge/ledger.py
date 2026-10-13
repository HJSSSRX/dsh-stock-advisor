# -*- coding: utf-8 -*-
"""决策台账 —— **唯一能验证"判断准不准"的机制。**

═══════════════════════════════════════════════════════════════════
为什么判断层需要这个
═══════════════════════════════════════════════════════════════════
「茅台能不能买」**没有标准答案**，所以没法用单元测试验证"判断对不对"。

但可以：**把判断记下来，等结果出来再算。**

    判断时：记下 [日期 | 代码 | 方向 | 依据 | 期限 | 基准]
    到期后：用真实行情结算 → 它对了吗？

═══════════════════════════════════════════════════════════════════
★★★ 指标口径（别人的血教训，我们照抄这条纪律）
═══════════════════════════════════════════════════════════════════
TradingAgents-astock v0.5.2 犯过的错：

    「v0.5.2 曾把 direction_accuracy 叫"胜率"，
      导致给 Sell 之后股价下跌（**判断正确**）被记成失败。」

所以本模块严格区分两组指标：

  ★ 衡量「判断准不准」的（我们只认这个）：
      direction_accuracy  方向正确率
          看多 → 必须**跑赢基准**才算对
          看空 → 必须**跑输基准**才算对
          观望 → **不计入**（没有方向，谈不上对错）

  ⚠️ 只描述「标的怎么走」的（**与判断对错无关**）：
      up_rate            上涨的比例
      outperform_rate    跑赢基准的比例
      平均涨跌幅

  **后一组绝不能拿来当"准确率"报给客户。**
"""
from __future__ import annotations

import json
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

# ── 台账存放位置（与缓存同区，落盘不丢）──
_LEDGER_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_ledger"
)
LEDGER_PATH = os.path.join(_LEDGER_DIR, "decisions.jsonl")

# ══════════════════════════════════════════════════════════════════
# 方向
# ══════════════════════════════════════════════════════════════════
BULL = "看多"
BEAR = "看空"
HOLD = "观望"
DIRECTIONS = (BULL, BEAR, HOLD)

# 判定用的阈值：跑赢/跑输基准多少才算"赢了"。
# ★ 不设 0 —— 必须**明显**才行，否则 0.001% 的差异也算对，那是噪音。
EDGE_PCT = 1.0          # 默认 1 个百分点


def _d(x: Any) -> Optional[date]:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ══════════════════════════════════════════════════════════════════
@dataclass
class Decision:
    """一条判断。**记下来的那一刻，未来就定了 —— 不许事后改。**"""
    made_on: str                 # 判断日（分析用的 as_of）
    code: str                    # 标的
    name: str = ""               # 名称（给人看）
    direction: str = HOLD        # 看多 / 看空 / 观望
    thesis: str = ""             # 一句话结论
    confidence: float = 0.0      # 自报置信度 0~1
    horizon_days: int = 60       # 期限（自然日）
    benchmark: str = "000300"    # 基准（默认沪深300）
    evidence_ids: list[str] = field(default_factory=list)  # 依据的 eid
    blockers: list[str] = field(default_factory=list)      # 已知的缺口
    entry_price: Optional[float] = None      # 判断时的价（结算要用）
    bench_price: Optional[float] = None      # 判断时的基准点位

    # ── 结算后回填 ──
    settled: bool = False
    settled_on: str = ""
    exit_price: Optional[float] = None
    bench_exit: Optional[float] = None
    ret_pct: Optional[float] = None           # 标的涨跌 %
    bench_pct: Optional[float] = None         # 基准涨跌 %
    alpha_pct: Optional[float] = None         # 超额 %
    correct: Optional[bool] = None            # ★ 判断对错（唯一有意义的那个）
    note: str = ""

    # ------------------------------------------------------------------
    @property
    def due_on(self) -> Optional[date]:
        d0 = _d(self.made_on)
        return d0 + timedelta(days=self.horizon_days) if d0 else None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(line: str) -> "Decision":
        raw = json.loads(line)
        known = {f for f in Decision.__dataclass_fields__}   # noqa: SLF001
        return Decision(**{k: v for k, v in raw.items() if k in known})


# ══════════════════════════════════════════════════════════════════
# 读写
# ══════════════════════════════════════════════════════════════════
def record(dec: Decision) -> str:
    """★ 记一条判断。**追加式，永不覆盖** —— 台账的价值在于不可篡改。"""
    os.makedirs(_LEDGER_DIR, exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8", newline="\n") as f:
        f.write(dec.to_json() + "\n")
    return LEDGER_PATH


def load_all() -> list[Decision]:
    if not os.path.exists(LEDGER_PATH):
        return []
    out: list[Decision] = []
    with open(LEDGER_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Decision.from_json(line))
            except Exception:                        # noqa: BLE001
                continue
    return out


def rewrite(decs: Iterable[Decision]) -> None:
    """整体重写（**只用于结算回填**，不用于改历史判断）。"""
    os.makedirs(_LEDGER_DIR, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for d in decs:
            f.write(d.to_json() + "\n")
    os.replace(tmp, LEDGER_PATH)


# ══════════════════════════════════════════════════════════════════
# ★★ 结算：判断对了吗
# ══════════════════════════════════════════════════════════════════
def judge_correct(direction: str, alpha_pct: Optional[float],
                  edge: float = EDGE_PCT) -> Optional[bool]:
    """**本模块最重要的一行逻辑。**

        看多 → alpha > +edge  才算对
        看空 → alpha < -edge  才算对
        观望 → None（不计入 —— 没有方向，谈不上对错）

    ★ 这里如果写错，整个统计就废了（别人的原话：
      「给 Sell 之后股价下跌（判断正确）被记成失败」）。
    """
    if alpha_pct is None:
        return None
    if direction == BULL:
        return alpha_pct > edge
    if direction == BEAR:
        return alpha_pct < -edge
    return None                      # HOLD / 未知方向 → 不计入


def settle_one(dec: Decision, *, exit_price: float, bench_exit: float,
               on: Optional[str] = None) -> Decision:
    """用到期后的真实价格结算一条判断。"""
    if not dec.entry_price or not dec.bench_price:
        dec.note = (dec.note + " ｜ 缺入场价或基准点位，无法结算").strip(" ｜")
        return dec
    dec.ret_pct = round((exit_price / dec.entry_price - 1) * 100, 4)
    dec.bench_pct = round((bench_exit / dec.bench_price - 1) * 100, 4)
    dec.alpha_pct = round(dec.ret_pct - dec.bench_pct, 4)
    dec.correct = judge_correct(dec.direction, dec.alpha_pct)
    dec.exit_price = exit_price
    dec.bench_exit = bench_exit
    dec.settled = True
    dec.settled_on = on or date.today().isoformat()
    return dec


# ══════════════════════════════════════════════════════════════════
# ★★ 统计：口径必须写死在这里，不许调用方自己拼
# ══════════════════════════════════════════════════════════════════
@dataclass
class Stats:
    total: int = 0
    pending: int = 0
    settled: int = 0
    graded: int = 0                 # ★ 参与对错评定的（排除观望）
    correct_n: int = 0

    @property
    def direction_accuracy(self) -> Optional[float]:
        """★★ **唯一衡量判断准不准的指标。**

        分母是 `graded`（有方向的、已结算的），**不含观望**。
        """
        return (self.correct_n / self.graded) if self.graded else None

    # ⚠️ 以下三个**只描述标的怎么走，与判断对错无关** ——
    #    绝不许当"准确率"报给客户。
    up_rate: Optional[float] = None
    outperform_rate: Optional[float] = None
    avg_alpha_pct: Optional[float] = None

    def report(self, min_sample: int = 30) -> str:
        """给客户看的说法。**样本不足时明说，不做统计结论。**"""
        L: list[str] = []
        L.append(f"台账共 {self.total} 条判断：已结算 {self.settled}、未到期 {self.pending}")
        if self.graded == 0:
            L.append("**还没有可评定的判断**（观望不计入，或都还没到期）。")
            return "\n".join(L)
        if self.direction_accuracy is None:
            return "\n".join(L)
        acc = self.direction_accuracy
        L.append(f"★ **方向正确率**：{acc*100:.1f}%（{self.correct_n}/{self.graded}）"
                 "  ← 唯一衡量判断准不准的")
        if self.up_rate is not None:
            L.append(f"　（参考，**与判断对错无关**：标的上涨 {self.up_rate*100:.1f}%、"
                     f"跑赢基准 {self.outperform_rate*100:.1f}%、"
                     f"平均超额 {self.avg_alpha_pct:+.2f}pct）")
        if self.graded < min_sample:
            L.append(f"⚠️ **样本只有 {self.graded} 条（< {min_sample}），"
                     "统计意义不足，不能据此说「我们准」或「我们不准」**。")
        return "\n".join(L)


def summarize(decs: list[Decision]) -> Stats:
    st = Stats()
    rets, alphas, graded_correct = [], [], []
    for d in decs:
        st.total += 1
        if not d.settled:
            st.pending += 1
            continue
        st.settled += 1
        if d.ret_pct is not None:
            rets.append(d.ret_pct)
        if d.alpha_pct is not None:
            alphas.append(d.alpha_pct)
        if d.correct is not None:          # 观望 → None → 不计入
            st.graded += 1
            graded_correct.append(d.correct)
    st.correct_n = sum(1 for x in graded_correct if x)
    if rets:
        st.up_rate = sum(1 for x in rets if x > 0) / len(rets)
    if alphas:
        st.outperform_rate = sum(1 for x in alphas if x > 0) / len(alphas)
        st.avg_alpha_pct = round(statistics.fmean(alphas), 4)
    return st


# ══════════════════════════════════════════════════════════════════
# 校准曲线：说 70% 把握的事，是不是真的 70% 成了
# ══════════════════════════════════════════════════════════════════
def calibration(decs: list[Decision], bins: int = 5) -> list[dict]:
    """★ 置信度校准 —— 判断层自报的把握，和实际命中率对得上吗。

    对得上 = 它知道自己有多准；对不上 = 它的"把握"是随口说的。
    """
    pts = [(d.confidence, d.correct) for d in decs
           if d.settled and d.correct is not None and d.confidence > 0]
    if not pts:
        return []
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        grp = [c for f, c in pts if (lo < f <= hi) or (i == 0 and f <= hi)]
        if not grp:
            continue
        out.append({
            "置信度区间": f"{lo:.0%}~{hi:.0%}",
            "条数": len(grp),
            "自报均值": round(statistics.fmean(
                [f for f, _ in pts if (lo < f <= hi) or (i == 0 and f <= hi)]), 3),
            "实际命中率": round(sum(1 for c in grp if c) / len(grp), 3),
        })
    return out
