# -*- coding: utf-8 -*-
"""观察层 —— **记录真实使用，但绝不改变结论**。

═══════════════════════════════════════════════════════════════════════════
为什么要有它（这是主人原话，写在最前面）
═══════════════════════════════════════════════════════════════════════════
> 「当前一整套内容，是我一个不炒股的人做的，没有真实的体验和内容，
>   **用户的使用操作、意图，恐怕才是能真正完善这个系统**」

这句话是对的，而且它指向一个残酷的事实：

    这套系统里每一条规格，来源都是论文、法规、竞品源码、和我们自己的推理。
    **"真实用户在某个地方卡住了"—— 零条。**

所以这一层的任务不是"优化体验"，是**采集真相**。

═══════════════════════════════════════════════════════════════════════════
三条纪律（写在代码里，不写在文档里）
═══════════════════════════════════════════════════════════════════════════
    ① **原话一字不改** —— 他说的每个字都是线索，包括错别字和口语
    ② **append-only** —— 历史不许改。改了就失去"当时到底是什么样"的信息
    ③ **只写不读** —— 观察数据**不进入任何计算路径**。
       它只产出给人看的报告，绝不参与"这条是什么"。

★★ 为什么记**序列**比记单条重要：
   单条「看看这个」看不出意图。
   但 `看看这个 → 再问了两融 → 又问了停牌 → 最后没问财务`
   这个序列说明：**他要的是"这只票现在安全吗"，不是"这只票值多少钱"**。
   **意图藏在序列里，不在单条里。**
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

from .profile import ROOT

OBS_DIR = os.path.join(ROOT, "observations")

# ── 结局分类（**诊断用的，不是评价用户的**）────────────────────
OUTCOME_FAIL = ("answered", "refused_unrecognized", "refused_comparison",
                "refused_news", "refused_not_stock", "error", "abandoned")


@dataclass
class Observation:
    """一次提问的完整记录。"""
    ts: str = ""
    session: str = ""
    seq: int = 0                  # ★ 本次会话内的第几问（序列靠它串起来）
    raw: str = ""                 # ★ 用户原话，**一字不改**
    resolved: str = ""            # 我们把它理解成了什么（qtype | code），失败则空
    outcome: str = "answered"
    reason: str = ""              # 失败/拒答时的原因
    prev_raw: str = ""            # 上一问的原话
    gap_sec: float = 0.0          # 与上一问间隔（秒）——卡壳时长
    notes: str = ""               # 观察者备注（人工补）

    def render(self) -> str:
        return (f"[{self.ts}] #{self.seq} 「{self.raw}」\n"
                f"    理解成：{self.resolved or '（没理解）'}"
                f"｜结局：{self.outcome}"
                + (f"｜原因：{self.reason[:60]}" if self.reason else ""))


def _session_path(session: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]", "_", session)
    return os.path.join(OBS_DIR, f"{safe}.jsonl")


class Observer:
    """记录器。**它只有 `record()` 和给人看的汇总，没有别的出口。**

    ★ 特别注意：本类**不提供**任何"根据历史调整行为"的方法。
      那是设计上的拒绝，不是还没做。
    """

    def __init__(self, session: str):
        self.session = session
        self._seq = 0
        self._last: Observation = None
        self._load_last()

    def _load_last(self):
        p = _session_path(self.session)
        if not os.path.exists(p):
            return
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._last = Observation(**json.loads(line))
        except Exception:                                    # noqa: BLE001
            pass                     # ★ 记录层坏了也不许影响主流程
        if self._last:
            self._seq = self._last.seq

    def record(self, raw: str, *, resolved: str = "", outcome: str = "answered",
               reason: str = "", notes: str = "") -> Observation:
        """★ 记一次提问。**原话一字不改地存。**"""
        if outcome not in OUTCOME_FAIL:
            outcome = "answered"
        self._seq += 1
        now = time.time()
        obs = Observation(
            ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            session=self.session, seq=self._seq, raw=str(raw),
            resolved=resolved, outcome=outcome, reason=reason,
            prev_raw=(self._last.raw if self._last else ""),
            gap_sec=(round(now - self._last_ts(), 1) if self._last else 0.0),
            notes=notes,
        )
        self._append(obs)
        self._last = obs
        return obs

    def _last_ts(self) -> float:
        try:
            return time.mktime(time.strptime(self._last.ts, "%Y-%m-%d %H:%M:%S"))
        except Exception:                                    # noqa: BLE001
            return time.time()

    def _append(self, obs: Observation) -> None:
        """**append-only** —— 只往后加，不改前面。"""
        os.makedirs(OBS_DIR, exist_ok=True)
        with open(_session_path(self.session), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(obs), ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════════
# 给人看的报告 —— 观察层的**唯一出口**
# ══════════════════════════════════════════════════════════════════
def load_session(session: str) -> list:
    p = _session_path(session)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(Observation(**json.loads(line)))
                except Exception:                            # noqa: BLE001
                    pass
    return out


def summarize(session: str) -> str:
    """★ 把一次会话整理成**给人看的报告**。

    它回答三个问题：
      ① 他在哪些地方**卡住了**（拒答/放弃）
      ② 他**重复问了**什么（第一次没答到点子上）
      ③ 他的**提问序列**透露出什么意图
    """
    obs = load_session(session)
    if not obs:
        return f"（会话 {session} 没有记录）"
    L = [f"═══ 会话观察：{session} ═══", f"共 {len(obs)} 次提问"]
    L.append("")
    L.append("── 提问序列（★ 意图藏在序列里）──")
    for o in obs:
        mark = "  " if o.outcome == "answered" else "✗ "
        L.append(f"{mark}{o.seq:>3}. 「{o.raw}」 → {o.resolved or '（没理解）'}")
        if o.outcome != "answered":
            L.append(f"        └─ {o.outcome}｜{o.reason[:70]}")
    # ① 卡住的地方
    stuck = [o for o in obs if o.outcome != "answered"]
    L.append("")
    L.append(f"── ① 卡住 {len(stuck)} 次（**每一条都是设计缺口**）──")
    for o in stuck:
        L.append(f"  · #{o.seq} 「{o.raw}」 → {o.outcome}")
        if o.reason:
            L.append(f"      {o.reason[:90]}")
    # ② 重复问
    L.append("")
    L.append("── ② 重复问的（第一次没答到点子上）──")
    seen, dup = {}, []
    for o in obs:
        key = re.sub(r"[\s，。？！,.]", "", o.raw)
        if key in seen:
            dup.append((seen[key], o))
        else:
            seen[key] = o
    if dup:
        for a, b in dup:
            L.append(f"  · #{a.seq}「{a.raw}」 → 到 #{b.seq} 又问了一遍「{b.raw}」")
    else:
        L.append("  （无）")
    # ③ 间隔 —— 卡壳时长的信号
    slow = [o for o in obs if o.gap_sec > 300]
    L.append("")
    L.append(f"── ③ 长时间停顿 {len(slow)} 次（>5 分钟，可能是他在别处找答案）──")
    for o in slow[:8]:
        L.append(f"  · #{o.seq} 停 {o.gap_sec/60:.1f} 分钟后问「{o.raw}」")
    L.append("")
    L.append("═══ 以上只是记录。**它不参与任何计算，也不改变任何结论。** ═══")
    return "\n".join(L)


def all_sessions() -> list:
    if not os.path.isdir(OBS_DIR):
        return []
    return sorted(f[:-6] for f in os.listdir(OBS_DIR) if f.endswith(".jsonl"))
