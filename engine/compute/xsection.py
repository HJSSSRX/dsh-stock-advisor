# -*- coding: utf-8 -*-
"""横截面层 —— 打分器要的「行业内 + 规模档内的百分位」从哪来。

═══════════════════════════════════════════════════════════════════════════
关键事实（实测）
═══════════════════════════════════════════════════════════════════════════
东财 clist 一次就能拿到**全市场 5562 只**的：
    f9=市盈率  f23=市净率  f20=总市值  f8=换手率  f100=行业名

于是打分器 8 项里的 3 项（EP / BP / TURN20）**可以直接算横截面百分位**，
不需要逐只取财报。

**其余 5 项的诚实状态：**
    IVOL / RESMOM —— 需要因子回归，**本层不做**
    ILLIQ / REV20 —— 需要逐只日线，**本层不做**（取数太慢，宁可标未实现）
    FSCORE        —— Piotroski 原文未取得，**按规矩不许凭记忆写**

★ 打分器 `judge/score.py` 本来就能处理缺项：**缺的会进 `excluded` 并声明**。
  所以「只上 3 项」不是妥协 —— 它是**如实声明后的降级**。

═══════════════════════════════════════════════════════════════════════════
★★ 百分位的口径必须写清楚（否则「9% 分位」是个没有意义的数）
═══════════════════════════════════════════════════════════════════════════
优先顺序（**每一级都要能说出来用的是哪一级**）：

    ① 同行业 + 同规模档    最可比
    ② 同行业              行业可比，规模不可比
    ③ 全市场              最弱，只在①②样本不足时用

样本不足（< MIN_PEERS）就**返回 None**，不给数 —— 宁可不给，不给假的。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

MIN_PEERS = 20          # 同类样本少于这个数就不给百分位
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_cache")

_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_FIELDS = "f12,f13,f14,f9,f23,f20,f21,f8,f100"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://quote.eastmoney.com/"}


@dataclass
class Row:
    code: str
    name: str
    industry: str
    pe: Optional[float]
    pb: Optional[float]
    mktcap: Optional[float]      # 元
    turnover: Optional[float]    # %


@dataclass
class XSection:
    as_of: str
    rows: list = field(default_factory=list)
    source: str = "eastmoney clist"

    # ── 规模档：用全市场市值四分位切 ──
    _q: tuple = ()

    def _quantiles(self):
        if self._q:
            return self._q
        caps = sorted(r.mktcap for r in self.rows if r.mktcap)
        if len(caps) < 40:
            return ()
        n = len(caps)
        self._q = (caps[n // 4], caps[n // 2], caps[3 * n // 4])
        return self._q

    def bucket_of(self, mktcap: Optional[float]) -> str:
        """规模档：微/小/中/大。★ 用**全市场**四分位，不是拍脑袋的绝对值。"""
        q = self._quantiles()
        if not q or mktcap is None:
            return "?"
        for name, edge in zip(("微", "小", "中"), q):
            if mktcap < edge:
                return name
        return "大"

    def peers(self, industry: str, bucket: str, need: int = MIN_PEERS) -> tuple:
        """取同类样本，返回 (样本列表, 用的是哪一级)。**逐级降级，且必须说出来。**"""
        lv1 = [r for r in self.rows if r.industry == industry and self.bucket_of(r.mktcap) == bucket]
        if len(lv1) >= need:
            return lv1, f"①同行业({industry})+同规模档({bucket})"
        lv2 = [r for r in self.rows if r.industry == industry]
        if len(lv2) >= need:
            return lv2, (f"②同行业({industry}) —— 同规模档只有 {len(lv1)} 只，不足 {need}")
        if len(self.rows) >= need:
            return self.rows, (f"③全市场 —— ②同行业只有 {len(lv2)} 只，不足 {need}")
        return [], f"样本不足（同行业 {len(lv2)} 只 < {need}）"

    def percentile(self, value: Optional[float], getter, *, industry: str,
                   mktcap: Optional[float] = None) -> tuple:
        """返回 `(百分位 或 None, 级别说明)`。**无参数中位秩法，抗极值。**"""
        if value is None:
            return None, "该指标本身没有值"
        bucket = self.bucket_of(mktcap)
        pool, level = self.peers(industry, bucket)
        vals = [v for v in (getter(r) for r in pool) if v is not None]
        if len(vals) < MIN_PEERS:
            return None, f"{level}｜有效值只有 {len(vals)} 个 < {MIN_PEERS}"
        below = sum(1 for v in vals if v < value)
        equal = sum(1 for v in vals if v == value)
        pct = (below + 0.5 * equal) / len(vals)
        return min(1.0 - 1e-9, max(1e-9, pct)), f"{level}｜样本 {len(vals)} 只"


# ══════════════════════════════════════════════════════════════════
def _fetch_page(pn: int, pz: int = 100) -> tuple:
    q = {"pn": str(pn), "pz": str(pz), "po": "1", "np": "1", "fltt": "2", "invt": "2",
         "fid": "f20", "fs": _FS, "fields": _FIELDS}
    req = urllib.request.Request(_URL + "?" + urllib.parse.urlencode(q), headers=_H)
    d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    dd = d.get("data") or {}
    return dd.get("diff") or [], int(dd.get("total") or 0)


def _num(v) -> Optional[float]:
    if v in (None, "", "-", "--"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f == 0 else f          # ★ 0 是哨兵值（不适用），不是真值


def load(max_pages: int = 60, use_cache: bool = True) -> XSection:
    """取全市场横截面。★ 缓存当日快照，**并报告用的是缓存还是实时**。"""
    day = time.strftime("%Y%m%d")
    path = os.path.join(CACHE_DIR, f"xsection_{day}.json")
    if use_cache and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            return XSection(as_of=d["as_of"], rows=[Row(**r) for r in d["rows"]],
                            source="eastmoney clist（**当日缓存**）")
        except Exception:                                    # noqa: BLE001
            pass
    rows, total = [], 0
    for pn in range(1, max_pages + 1):
        diff, total = _fetch_page(pn)
        if not diff:
            break
        for r in diff:
            code = str(r.get("f12", ""))
            if len(code) != 6:
                continue
            rows.append(Row(code=code, name=str(r.get("f14", "")).strip(),
                            industry=str(r.get("f100", "") or "未分类").strip(),
                            pe=_num(r.get("f9")), pb=_num(r.get("f23")),
                            mktcap=_num(r.get("f20")), turnover=_num(r.get("f8"))))
        if len(rows) >= total:
            break
    as_of = time.strftime("%Y-%m-%d")
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"as_of": as_of,
                       "rows": [vars(r) for r in rows]}, fh, ensure_ascii=False)
    except Exception:                                        # noqa: BLE001
        pass
    return XSection(as_of=as_of, rows=rows)


def find(xs: XSection, code: str) -> Optional[Row]:
    c = str(code).zfill(6)
    return next((r for r in xs.rows if r.code == c), None)
