# 取数层（`data/`）

> 唯一取数口。模型只能通过 `registry.fetch()` 拿数据；**每个数字都带来源、时间、口径和证据号**。

---

## ⚠️ 最重要的一条（2026-10-11 实测，绕不过去）

**东方财富做的是 TLS/JA3 指纹级拦截，不是 IP 封禁。**

同一个 URL 的三种结果：

| 客户端 | 结果 |
|---|---|
| `curl`（命令行） | **HTTP 000** ← 看起来像"IP 被封" |
| Python `requests`（urllib3 默认指纹） | `RemoteDisconnected` 连接被重置 |
| **`curl_cffi` 伪装 Chrome** | **HTTP 200** ✅ |

**结论**：
1. **不需要换 IP、不需要代理池** —— 只要用浏览器指纹的客户端
2. 这解释了 **akshare 为什么大面积失效**（它底层是 `requests`）
3. 所以本层的 HTTP 底座**强制**使用 `curl_cffi(impersonate="chrome")`，装不上才退化到 requests

### 补充：它是**软限流**，不是永久拉黑

实测第二轮：用 `curl` 以 2 秒间隔连打 10 分钟后，`push2` 对所有客户端都开始失败。
一度判断为"测进黑名单"，但**降速后立刻恢复**（同一次会话里，间隔拉到 2.5 秒后
`quote` 又以 132ms 正常返回）。

触发顺序是**渐进**的：
```
打得快 → 先挡非浏览器指纹（curl/requests 挂，curl_cffi 还能过）
继续打 → 连伪装指纹也掐（curl_cffi 报 curl: (56) Connection closed abruptly）
降速   → 自动恢复
```

**所以限流参数按"别让服务端把你当爬虫"来定，而不是按"防封 IP"**：
`push2` = 2.5 秒 / `push2his` = 2.0 秒 / `datacenter` = 2.5 秒 / `np-anotice` = 3.0 秒。
（全市场同步要翻 60 页 → 约 150 秒，属于每日任务，不能随用户提问触发。）

---

## 目录

```
data/
  result.py        统一返回结构 Result（含"失败即值"与证据号 eid）
  caliber.py       字段精度 / 复权口径 / 涨跌停单一真值 / secid 换算
  cache.py         落盘缓存（TTL 分级：快照 120s、日线 30min、财务 12h、分红 24h）
  registry.py      ★ 唯一取数口：路由 → 重试 → 熔断 → 缓存 → 统一返回
  sources/
    http.py         HTTP 底座：浏览器指纹 + 每主机最小间隔限流 + GBK 转码
    eastmoney.py    主源：行情/K线/资金流/财务/公告/板块/分红/全市场
    tencent.py      备源：行情（最轻，可批量）
    sina.py         备源：行情
    cninfo.py       官方公告源（巨潮，带 PDF 原文链接）
```

---

## 用法

```bash
# 命令行（在 stockharness/ 目录下）
python -m data.registry quote 600519
python -m data.registry kline 300417 freq=daily limit=250
python -m data.registry financial 300417
python -m data.registry announcement 300417
python -m data.registry dividend 600519
python -m data.registry health          # 各源健康度（落盘，跨进程可见）
python -m data.registry cache           # 缓存概况
```

```python
# 代码里
import sys; sys.path.insert(0, "stockharness")
from data.registry import fetch, health

r = fetch("quote", "300417")
if r.ok:
    print(r.data["现价"], r.source, r.as_of, r.caliber, r.eid)
else:
    print("取不到：", r.why())      # ← 必须如实告诉用户，不许推测

# 喂给模型的文本（自带来源/时间/口径；失败时自带"没取到"的说明）
print(r.tool_message())
```

---

## 三条纪律（写在类型里，不靠自觉）

1. **失败也是值**
   全源挂掉返回 `ok=False` + 每个源的尝试记录。**绝不返回空表、绝不返回 0**。
   （反例见 crewai-astock：财务取数失败把 ROE 兜底成 0 照原样输出，模型会读成"ROE 真的是 0%"。）

2. **降级要留痕**
   用了备源 → `fallback_used=True`；用了缓存 → `cached=True`。上层会明确告知用户。

3. **熔断不是可选项**
   连续 3 次失败 → 该源停用 15 分钟。健康度**落盘**（否则 CLI 每次新进程都归零，等于没熔断）。

---

## 实测记录的坑（都已处理）

| # | 坑 | 处理 |
|---|---|---|
| 1 | 东财 `f43` 等字段**需除以 100** | `caliber.EM_QUOTE_FIELDS` 集中登记 |
| 2 | 腾讯/新浪返回 **GBK** | 底座强制转码 |
| 3 | 新浪**必须带 Referer** 否则 403 | 写死在源里 |
| 4 | **停牌股 `f43=0`** | 检测到 0 时改用 `f60`（昨收）并标 `⚠️停牌` |
| 5 | 复权口径**差 4 倍**（前复权 11.88 / 后复权 46.32） | 每条结果带 `caliber` |
| 6 | 前复权长期历史**可能为负** | 检测并告警 |
| 7 | 东财 `lmt` 参数在 `beg=0` 时**不生效**（要 5 根给 2843 根） | 用 `beg` 限定 + 本地切片兜底 |
| 8 | 取数窗口系数没算节假日（国庆后只取到 3 根） | 系数提到 3.5 倍 |
| 9 | 财务返回顺序**不是倒序** | 显式按报告期排序 |
| 10 | `920xxx`（北交所）以 9 开头，**不是沪市** | `market_of` 单独处理 |
| 11 | 熔断状态放内存里跨进程归零 | 落盘到 `_cache/_health.json` |

---

## 已验证可用（2026-10-11 实跑，23 类数据 23/23 通过）

### A. 行情类（`eastmoney` 源 · push2/push2his 家族）

| 数据 | 键名 | 内容 |
|---|---|---|
| 实时行情 | `quote` | 现价/涨跌幅/今开最高最低/成交量额/换手率/市值/PE/PB/振幅（**三源互备**：东财→腾讯→新浪） |
| K 线 | `kline` | 日/周/月/1·5·15·30·60 分钟，三种复权口径 |
| 资金流 | `fundflow` | 主力/超大单/大单/中单/小单净流入 + 主力净占比，历史 120 天 |
| 板块 | `board` / `board_members` | 496 个行业板块 + 成分股 |
| 全市场 | `universe` | 5922 只，含行业 + 地域 + **概念标签**（翻页约 150 秒，每日任务） |
| 指数 | `index` | 上证/深证/创业板 |

### B. 数据中心报表（`emdc` 源 · datacenter-web 家族）

| 数据 | 键名 | 用途 |
|---|---|---|
| 业绩报表 | `financial` | 营收/净利/ROE/毛利率/同比（**注：走 push2 家族，仍在 eastmoney 模块**） |
| **利润表** | `financial_income` | 完整利润表科目 |
| **资产负债表** | `financial_balance` | 总资产/负债/商誉/存货/应收/借款 |
| **现金流量表** | `financial_cashflow` | 经营/投资/筹资现金流净额 |
| 季度主要指标 | `quarterly_metrics` | EPS/BPS/ROE/毛利率/负债率（**长序列，估值分位要用**） |
| 股东户数 | `holder_num` | 户数变化 + 户均持股（**筹码集中度**） |
| 十大流通股东 | `top_free_holders` | 谁在拿、拿了多少、变动 |
| 高管持股变动 | `executive_trades` | 内部人买卖（**减持信号**） |
| 机构调研 | `org_survey` | 谁去调研了、什么时候 |
| 大宗交易 | `block_trades` | 成交价/折溢价/买卖方 |
| 限售解禁 | `restricted_release` | 解禁日/股数/市值（**A 股风险事件**） |
| 业绩预告 | `forecast` | 预告上下限/增幅/变动原因（**预期差来源**） |
| 行业估值 | `industry_valuation` | 行业整体估值（**"跟同行比"要用的**） |
| 分红送配 | `dividend` | 除权除息日/派息方案（复权计算输入） |

### C. 宏观（`emdc` 源）

`macro` + `indicator`：**CPI / PPI / PMI / GDP / M2 / 社融 / LPR / 房价 / 零售 / 失业率**
（登记表见 `sources/emdc.py` 的 `MACROS`，另有 30 张宏观报表可随时加）

### D. 公告（`cninfo` 源 · 官方）

`announcement` — 巨潮资讯官方披露，带 PDF 原文链接

---

## 关于"量"的说明

东财数据中心共 **137 张报表**（akshare 全库 **1141 个接口**），我们目前用了 **16 张**。
剩下的按需再加，加一张的代价约半小时（登记报表名 + 字段中文化 + 加一条路由）。
