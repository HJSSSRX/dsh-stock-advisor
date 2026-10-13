# -*- coding: utf-8 -*-
"""情报通道加工层 —— 新闻/舆情的"规矩"

采集在 `sources/news.py`，本模块负责把原始新闻变成**能安全进产品**的东西：

  ① SimHash 去重（64 位 + 汉明距离阈值）→ 解决"3 家转载 ≠ 3 个独立利好"
  ② 独立性折减系数 → 让去重真正影响权重，不只是少显示一条
  ③ 三时间字段（发布 / 首次采集 / 事发）→ 防前视偏差、防旧闻翻新
  ④ 三条隔离硬规则（写进代码，不写进提示词）→ 保证情报永不冒充结论

依据：`设计\信息真伪判断-调研.md`（89 个出处）、`设计\新闻舆论政策-应用设计.md`

★ 本模块的产出**永远不进"结论"字段**，只进"相关动态"。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

# ══════════════════════════════════════════════════════════════════
# ① SimHash 去重
# ══════════════════════════════════════════════════════════════════
def _tokens(text: str) -> list[str]:
    """中文用字符二元组（不依赖 jieba），英文数字按词切。

    调研给的工程共识：SimHash 64 位 + 汉明距离阈值 k=3。
    """
    t = re.sub(r"\s+", "", text or "")
    chars = [c for c in t if "\u4e00" <= c <= "\u9fff"]
    grams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    return grams + words


def simhash64(text: str) -> int:
    """经典 SimHash：每个 token 哈希成 64 位，逐位加权投票。"""
    tokens = _tokens(text)
    if not tokens:
        return 0
    bits = [0] * 64
    for tk in tokens:
        h = int(hashlib.md5(tk.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(64):
            bits[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if bits[i] > 0:
            out |= (1 << i)
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def cluster(items: list[dict], threshold: int = 3,
            key_fields: tuple[str, ...] = ("标题",)) -> list[dict]:
    """把"同一件事的多条报道"聚成一簇，并给出**可信度加成**。

    ★ 两层匹配（只做一层不够，实测踩到）：
      层 1 **SimHash + 汉明距离 ≤3** —— 抓"同一篇通稿被转载"（标题近乎相同）
      层 2 **事件签名相同** —— 抓"不同媒体各自报道同一件事"
            签名 = 关键数字 + 报告期关键词（如 "109.85万元|2026年|中报"）

    ★ 关于"独立性"：这里的方向和直觉相反，必须说清楚 ——
      · **转载**（同一来源被多家复制）：**不给任何加成**（这正是"3 家转载 ≠ 3 个独立利好"）
      · **独立报道**（不同媒体各自采写同一事件）：**给有限加成**，且**有上限**
        系数 = 1 + 0.2×(k-1)，上限 1.5 —— 独立来源越多确实更可信，但不是线性叠加
      （子代理原设计为 k=2→0.30、k≥3→0.15 的"折减"，那是把两种情形混为一谈；
        折减只该用于转载，独立报道应当是"有限加分"。此处为自己的判断，需实测校准。）
    """
    for it in items:
        text = " ".join(str(it.get(f, "")) for f in key_fields) + " " + str(it.get("摘要", ""))
        it["_simhash"] = simhash64(text)
        it["_sig"] = _event_signature(text)

    clusters: list[list[dict]] = []
    match_kind: list[str] = []
    for it in items:
        placed = False
        for idx, cl in enumerate(clusters):
            head = cl[0]
            if hamming(it["_simhash"], head["_simhash"]) <= threshold:
                cl.append(it); placed = True
                if match_kind[idx] == "new":
                    match_kind[idx] = "near_dup"
                break
            if _same_event(it["_sig"], head["_sig"]):
                cl.append(it); placed = True
                if match_kind[idx] == "new":
                    match_kind[idx] = "same_event"
                break
        if not placed:
            clusters.append([it]); match_kind.append("new")

    out: list[dict] = []
    for idx, cl in enumerate(clusters):
        k = len(cl)
        medias = [c.get("来源", "") for c in cl]
        same_media = len(set(medias)) < k
        kind = match_kind[idx]
        repost = (kind == "near_dup") or same_media

        if repost:
            factor = 1.0                      # 转载：零加成
            label = "转载（同一来源）"
        elif k >= 2:
            factor = round(min(1.0 + 0.2 * (k - 1), 1.5), 2)
            label = f"独立报道×{k}"
        else:
            factor = 1.0
            label = "独家"

        for c in cl:
            c["簇编号"] = idx
            c["簇内条数"] = k
            c["疑似转载"] = repost
            c["独立性系数"] = factor
            c["独立性说明"] = label
            c.pop("_simhash", None)
            c.pop("_sig", None)
            out.append(c)
    return out


# ── 事件签名：关键数字 + 报告期（用于层 2 匹配）────────────────────
#    ★ 实测踩到的坑：不同媒体对同一个报告期写法不同 ——
#      "上半年" / "半年度" / "中报" / "半年报" 说的是同一件事，
#      不做归一就永远匹配不上（第一次实现就是这么失败的）。
_SIG_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|亿元|万股|亿股|万|亿|元|%)")
_PERIOD_RAW = {
    "H1": ("上半年", "半年度", "半年报", "中报", "半年"),
    "Q1": ("一季度", "一季报", "第一季度"),
    "Q2": ("二季度", "二季报", "第二季度"),
    "Q3": ("三季度", "三季报", "第三季度"),
    "Q4": ("四季度", "四季报", "第四季度"),
    "FY": ("年报", "年度", "全年", "业绩快报", "业绩预告"),
}


def _event_signature(text: str) -> tuple[set[str], set[str]]:
    """返回 (数字集合, 归一化报告期集合)。"""
    t = text or ""
    nums = {m.group(1) + m.group(2) for m in _SIG_NUM.finditer(t)}
    pers = {key for key, words in _PERIOD_RAW.items() if any(w in t for w in words)}
    return nums, pers


def _same_event(a: tuple[set[str], set[str]], b: tuple[set[str], set[str]]) -> bool:
    """同一事件的判定：

    - 有共同报告期 **且** 有共同数字 → 同一事件（财报类新闻的典型情形）
    - 无报告期时，需 ≥2 个共同数字（更保守，避免误并）
    """
    an, ap = a
    bn, bp = b
    if not an or not bn:
        return False
    common_nums = an & bn
    if not common_nums:
        return False
    if ap & bp:
        return True
    return len(common_nums) >= 2


# ══════════════════════════════════════════════════════════════════
# ② 三时间字段（防前视 / 防旧闻翻新）
# ══════════════════════════════════════════════════════════════════
def stamp_times(items: list[dict], first_seen: Optional[str] = None) -> list[dict]:
    """给每条打上三个时间：

    - 发布时间：新闻自己标的时间
    - 首次采集时间：我们第一次见到它的时间（**回测只能用它，不能用发布时间**）
    - 事发时间：新闻里描述的事件发生时间（能从正文里抽就抽，抽不到为空）
    """
    now = first_seen or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for it in items:
        it["发布时间"] = it.get("时间", "")
        it["首次采集"] = now
        it["事发时间"] = _extract_event_time(it.get("摘要", "") or "")
        # 时效：距首发多久（供上层做衰减）
        it["距今小时"] = _hours_since(it["发布时间"])
    return items


def _hours_since(ts: str) -> Optional[float]:
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        return round((datetime.now() - t).total_seconds() / 3600, 1)
    except Exception:                                        # noqa: BLE001
        return None


_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月")


def _extract_event_time(text: str) -> str:
    """从正文摘要里粗抽事件时间（A 股新闻里常见"X年X月"表述）。"""
    m = _MONTH_RE.search(text or "")
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""


# ══════════════════════════════════════════════════════════════════
# ③ 声明类型分类（决定它能不能进"事实"通道）
# ══════════════════════════════════════════════════════════════════
_RUMOR_KW = ("传", "据悉", "爆料", "独家获悉", "疑似", "或将", "有望", "酝酿",
             "网传", "市场消息", "小作文", "据接近", "人士称",
             "发帖称", "发文称", "朋友圈", "直播间", "表态")
_FORECAST_KW = ("预计", "预告", "预测", "目标价", "指引", "展望")
_OPINION_KW = ("认为", "建议", "看好", "看空", "评级", "观点", "分析认为", "解读")
_RULE_KW = ("规定", "条例", "办法", "通知", "监管", "证监会", "交易所", "政策", "公告编号")
_HARD_FACT_KW = ("业绩", "订单", "并购", "收购", "重组", "被查", "违规", "立案",
                 "人事", "董事长", "总经理", "辞职", "增持", "减持", "解禁", "质押")


def classify_claim(text: str) -> str:
    """返回 fact / rumor / forecast / opinion / rule 之一。

    ⚠️ 这是**关键词启发式**，不是分类模型 —— 阈值与词表必须用自建评测集校准
       （调研结论：这一层不要训练模型，按时间切分会崩塌；但规则要能解释、能改）。
    """
    t = text or ""
    if any(k in t for k in _RULE_KW):
        return "rule"
    if any(k in t for k in _RUMOR_KW):
        return "rumor"
    if any(k in t for k in _FORECAST_KW):
        return "forecast"
    if any(k in t for k in _OPINION_KW):
        return "opinion"
    return "fact"


def needs_s0_confirmation(text: str) -> bool:
    """是否属于"必须有法定披露佐证，否则要标注未获证实"的硬事实类断言。"""
    return any(k in (text or "") for k in _HARD_FACT_KW)


# ══════════════════════════════════════════════════════════════════
# ④ 三条隔离硬规则
# ══════════════════════════════════════════════════════════════════
def apply_isolation(items: list[dict]) -> list[dict]:
    """给每条打上"能不能进结论"的判定。**这是代码 if，不是提示词。**

    规则 1：S3 及以下永不进"结论"
    规则 2：业绩/订单/并购/被查/人事 类断言，无 S0/S1 佐证 → 必须标"未获公司公告证实"
    规则 3：S3 及以下不进 evidence_ids（即不能作为结论的证据）

    ★ 另外要求：必须是**强相关**且**非批量稿** —— 否则"610只个股流通市值不足20亿元"
      这种把个股当数据点堆进去的格式化稿会被当成这家公司的新闻（实测踩到）。
    """
    for it in items:
        grade = str(it.get("媒体分级", "S4"))
        rank = int(grade[1]) if len(grade) > 1 and grade[1].isdigit() else 4
        claim = classify_claim(it.get("标题", "") + " " + str(it.get("摘要", "") or ""))
        it["声明类型"] = {"fact": "事实类", "rumor": "传闻类",
                          "forecast": "预测类", "opinion": "观点类",
                          "rule": "规则类"}[claim]

        relevant = (it.get("相关性") == "强") and not it.get("疑似批量稿")

        # 规则 3：能不能当证据（支撑结论的原料）
        it["可作证据"] = bool(relevant and rank <= 2 and claim in ("fact", "rule"))
        # 规则 1：能不能进结论（更严：只认官方媒体及以上）
        it["可进结论"] = bool(it["可作证据"] and rank <= 1)

        # 规则 2
        if claim == "rumor" or (needs_s0_confirmation(it.get("标题", "")) and rank >= 3):
            it["必须标注"] = "未获公司公告证实"
        elif rank >= 3:
            it["必须标注"] = "未逐条核实"
        else:
            it["必须标注"] = ""
    return items


# ══════════════════════════════════════════════════════════════════
# ⑤ 生成给客户看的"相关动态"区块（永不混进结论）
# ══════════════════════════════════════════════════════════════════
def build_dynamic_section(items: list[dict], limit: int = 6) -> str:
    """输出手机端用的"相关动态"文本块。

    ★ 硬性：结论区与动态区必须物理隔开，且动态区自带免责说明。
      调研结论：事实与情报混排会拉低事实的可信度，客户也分不清哪句能信。
    """
    if not items:
        return ""
    rows = [i for i in items if i.get("相关性") == "强" and not i.get("疑似批量稿")]
    # 排序：能进结论的在前；同组内**新的在前**（原来按旧→新排是错的，实测发现）
    rows.sort(key=lambda r: (bool(r.get("可进结论")), str(r.get("发布时间", ""))), reverse=True)
    if not rows:
        return ""

    lines = ["─────────────────────────────",
             "【相关动态】⚠️ 以下为情报通道，未逐条核实，请与上面的结论分开看"]
    for r in rows[:limit]:
        tag = r.get("媒体分级", "")
        note = r.get("必须标注", "")
        extra = []
        if r.get("同簇条数", 1) > 1:
            extra.append(r.get("独立性说明", ""))
        if note:
            extra.append(note)
        extra.append("权重 %.2f" % r.get("权重", 0))
        suffix = ("　—　" + "；".join(extra)) if extra else ""
        lines.append(f"  · {r.get('发布时间', '')[:10]} [{tag}] {r.get('标题', '')[:44]}{suffix}")
    lines.append("  ⚠️ 这些消息与股价的关系是并列展示，**不构成因果证明**")
    lines.append("─────────────────────────────")
    return "\n".join(lines)


def process(items: list[dict]) -> dict:
    """一站式：去重 → 打时间 → 分类 → 隔离判定。"""
    items = cluster(items)
    items = stamp_times(items)
    items = apply_isolation(items)
    reps = representatives(items)
    return {
        "items": items,
        "representatives": reps,
        "dynamic_section": build_dynamic_section(reps),
        "可进结论条数": sum(1 for i in reps if i.get("可进结论")),
        "仅动态条数": sum(1 for i in reps if not i.get("可进结论")),
        "原始条数": len(items),
        "事件数": len(reps),
    }


# ══════════════════════════════════════════════════════════════════
# ⑥ 每簇只算一次 + 权重打分
# ══════════════════════════════════════════════════════════════════
#    ★ 关键语义（不做这一步，去重就白做了）：
#      簇内 2 条**不能各算一遍** —— 否则 1.2 × 2 = 2.4，比两条独立新闻还重。
#      正确做法：一个事件 = 一条代表 + 一个簇级加成系数。
_SRC_WEIGHT = {0: 1.00, 1: 0.80, 2: 0.60, 3: 0.35, 4: 0.15}
_TYPE_WEIGHT = {"fact": 1.00, "rule": 1.00, "forecast": 0.70, "opinion": 0.40, "rumor": 0.30}
# ★ 相关性权重（实测踩到）：权威媒体发的"市场统计稿"标题里没有个股名，
#   但正文把该股当数据点列进去 —— 不乘相关性系数，它的权重会虚高到盖过真新闻。
_REL_WEIGHT = {"强": 1.00, "弱": 0.25}
# 半衰期（小时）：传闻衰减最快，规则类几乎不衰减
_HALF_LIFE = {"rumor": 6, "opinion": 24, "forecast": 72, "fact": 720, "rule": 8760}


def _rank_of(item: dict) -> int:
    g = str(item.get("媒体分级", "S4"))
    return int(g[1]) if len(g) > 1 and g[1].isdigit() else 4


def score_item(item: dict) -> float:
    """单个事件的权重 = 源权威度 × 独立性 × 相关性 × 时效衰减 × 声明类型。

    ★ 全部因子都是**可复现的确定性计算** —— 不交给模型判断。
    ★ 批量稿直接归零：它不是"这条消息有多可信"的问题，而是"它压根不是这家公司的消息"。
    """
    if item.get("疑似批量稿"):
        return 0.0
    w_src = _SRC_WEIGHT.get(_rank_of(item), 0.15)
    w_ind = float(item.get("独立性系数", 1.0))
    w_rel = _REL_WEIGHT.get(item.get("相关性", "弱"), 0.25)
    hl = _HALF_LIFE.get(item.get("_claim_key", "fact"), 720)
    hrs = item.get("距今小时")
    w_time = 0.5 ** (hrs / hl) if isinstance(hrs, (int, float)) else 1.0
    w_type = _TYPE_WEIGHT.get(item.get("_claim_key", "fact"), 1.0)
    return round(w_src * w_ind * w_rel * w_time * w_type, 4)


def representatives(items: list[dict]) -> list[dict]:
    """每个事件簇只保留**一条代表**（同簇内选最权威、最新的），并挂上簇级权重。"""
    by_cluster: dict[int, list[dict]] = {}
    for it in items:
        by_cluster.setdefault(it.get("簇编号", 0), []).append(it)

    out: list[dict] = []
    for _, cl in by_cluster.items():
        cl.sort(key=lambda x: (_rank_of(x), str(x.get("发布时间", ""))), reverse=False)
        rep = dict(cl[0])                      # 最权威的那条当代表
        rep["同簇条数"] = len(cl)
        rep["同簇来源"] = [c.get("来源", "") for c in cl]
        rep["_claim_key"] = {
            "事实类": "fact", "传闻类": "rumor", "预测类": "forecast",
            "观点类": "opinion", "规则类": "rule",
        }.get(rep.get("声明类型", "事实类"), "fact")
        rep["权重"] = score_item(rep)
        out.append(rep)
    out.sort(key=lambda r: r.get("权重", 0), reverse=True)
    return out


# ══════════════════════════════════════════════════════════════════
# ⑦ 三类分档 —— **这是产品对外的口径**（用户钦定）
# ══════════════════════════════════════════════════════════════════
#   ① 可核验  公告 / 监管 / 官方数据 / 权威源的事实类   → 可作为**事实**呈现
#   ② 可计数  多家**独立**媒体报道                      → 可作为**统计事实**呈现
#   ③ 不可核验 自媒体 / 股吧 / 传闻 / 转载              → **只陈列，不进结论**
#
#   ★★ 为什么"可计数"也算一类可以呈现的东西：
#      「这条消息被 N 家独立媒体报道」这句话**本身是可核验的** ——
#      我们能数，数出来的就是事实。
#      它**不说消息是真是假**，只说**传播广度**。这个区分必须写在输出里。
#
#   ★★★ 三类**全部不参与方向判断** —— 这是情报通道的铁律。
#      本条由 `selftest_intel_class.py` 里的机械断言守着。
CLASS_I = "①可核验"
CLASS_II = "②可计数"
CLASS_III = "③不可核验"
CLASSES = (CLASS_I, CLASS_II, CLASS_III)


def to_class(it: dict) -> str:
    """把一条情报判进三类。**纯规则，可复现。**

    ★★ 两票否决（实测踩到才发现）：**相关性必须"强"、且不是批量稿** ——
       否则「食品饮料行业今日涨1.68%」「主力动向：特大单净流出32.12亿元」
       这种**大盘/行业统计稿**会因为是 S1 官方媒体 + 事实类，被判成「①可核验」。
       它们**压根不是这家公司的消息**，放进①就是把①变成垃圾桶。

       这两条判据 `apply_isolation` 里本来就有，我第一版忘了用 —— 补上。
    """
    # ★ 两票否决：不相关 / 批量稿 → 直接进 ③
    if it.get("相关性") != "强" or it.get("疑似批量稿"):
        return CLASS_III

    rank = _rank_of(it)
    claim = it.get("_claim_key") or {
        "事实类": "fact", "传闻类": "rumor", "预测类": "forecast",
        "观点类": "opinion", "规则类": "rule",
    }.get(it.get("声明类型", "事实类"), "fact")
    label = str(it.get("独立性说明", ""))
    k = int(it.get("簇内条数") or it.get("同簇条数") or 1)

    # ① 权威源的事实/规则类
    if claim in ("fact", "rule") and rank <= 1:
        return CLASS_I
    # ② 多家**独立**报道（★ 「转载」不算 —— 3 家转同一篇通稿 ≠ 3 个独立来源）
    if label.startswith("独立报道") and k >= 2:
        return CLASS_II
    return CLASS_III


def classify_all(reps: list) -> dict:
    """给每条代表打上三类标记，返回分档结果。"""
    buckets = {cls: [] for cls in CLASSES}
    for r in reps:
        c = to_class(r)
        r["三类"] = c
        buckets[c].append(r)
    return buckets


def build_class_section(reps: list, limit_per_class: int = 4) -> str:
    """★ 输出三类分档区。**它自带免责说明，且明写"不参与方向判断"。**"""
    if not reps:
        return ""
    buckets = classify_all(reps)
    n_i = len(buckets[CLASS_I])
    n_ii = len(buckets[CLASS_II])
    n_iii = len(buckets[CLASS_III])
    L = ["【情报·三类】（**与上面的结论分开看**）"]
    for cls, note in ((CLASS_I, "有官方/权威源 —— 可作为**事实**呈现"),
                      (CLASS_II, "多家**独立**报道 —— 可作为**统计事实**呈现，"
                                 "但**不说明消息真假**"),
                      (CLASS_III, "自媒体/传闻/转载 —— **只陈列，不进结论**")):
        rows = buckets[cls]
        L.append(f"  {cls}（{len(rows)} 条）：{note}")
        for r in rows[:limit_per_class]:
            tag = r.get("媒体分级", "")
            extra = []
            if r.get("簇内条数", 1) > 1 and r.get("独立性说明"):
                extra.append(str(r["独立性说明"]))
            if r.get("必须标注"):
                extra.append(str(r["必须标注"]))
            suffix = ("　—　" + "；".join(extra)) if extra else ""
            L.append(f"    · {str(r.get('发布时间',''))[:10]} [{tag}] "
                     f"{str(r.get('标题',''))[:46]}{suffix}")
        if len(rows) > limit_per_class:
            L.append(f"    · …另有 {len(rows)-limit_per_class} 条")
    L.append(f"  ⚠️ 三类**全部不参与方向判断**（情报通道铁律）——"
             f"它们只回答「市场上在说什么」，不回答「这消息是真是假」。")
    L.append(f"  ⚠️ 其中 ③ 的 {n_iii} 条**没有任何可核验依据**，"
             f"看到它们时请当作「有人在说」，不是「有这回事」。")
    return "\n".join(L)
