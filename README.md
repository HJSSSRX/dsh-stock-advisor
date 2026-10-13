# dsh-stock-advisor

给 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 加一个**「A 股参谋」模式**。

一句话说清它是什么：**它帮你把该看的都看了，但不替你下决心。**

---

## ⚠️ 先说清楚它不做什么

- **不给方向性建议。** 不说该买、该卖、目标价、能不能涨。
- **不是投资建议。** 花钱的是你，承担后果的也是你。
- 它给的是：**可核验的事实 + 该看的清单 + 你没问但该知道的坑**。

这不是谦虚，是设计。理由写在 [`skills/stock-advisor/SKILL.md`](skills/stock-advisor/SKILL.md) 里，
那份文件是这个项目的**灵魂**，代码可以重写，它不行。

---

## 它由三块组成

| 块 | 是什么 | 在哪 |
|---|---|---|
| **纪律** | 496 行的行为准则：十条铁律、情报三分类、回答结构、最坏情况必须主动说 | `skills/stock-advisor/SKILL.md` |
| **模式** | 一个 DSH preset，挂上一整套工具并注入参谋人格 | `cordis.patch.yml` |
| **引擎** | Python 取数 + 计算 + 核验 + **数字溯源闸门** | `engine/` |

---

## 装之前你需要什么

| 需要 | 说明 |
|---|---|
| **DSH `0.2.0-rc.2`** | 只在这个版本上验证过。别的版本可能装得上，但 preset 骨架引用的包名对不上就会缺工具。 |
| **Node 22+** | DSH 自带，不用管。 |
| **Python 3.10+** | ⚠️ **要你自己装**（[python.org](https://www.python.org/downloads/)）。装了忘了勾 "Add to PATH" 也没关系，见下面的 `DSH_STOCK_PYTHON`。 |
| **能访问 A 股数据源** | 东财 / 新浪 / 巨潮这些**境内直连**即可，不需要翻墙。 |

---

## 安装

```bash
dsh plugin --profile web add "git+https://github.com/HJSSSRX/dsh-stock-advisor.git"
```

装完**重启 DSH**。然后在会话里用 `/stock-advisor` 切到「股票分析师」模式。

> 把 `web` 换成你自己的 profile 名（`dsh plugin --profile <名字> ...`）。

### 装完先体检（**这一步别跳**）

```bash
node "<插件目录>/scripts/doctor.mjs"
```

它会逐个真跑一遍，告诉你 Node / Python / 依赖包 / DSH 版本各自什么状态。
缺依赖时它会打印**一条可以直接照抄的命令**，装不装由你决定 —— 它不擅自改你的环境。

插件目录一般在：

```
~/.dsh/profiles/<你的 profile>/node_modules/dsh-stock-advisor
```

### Python 找不到怎么办

体检会告诉你它试过哪些、都不行。然后二选一：

```powershell
# Windows PowerShell
$env:DSH_STOCK_PYTHON="C:\完整\路径\python.exe"
```
```bash
# macOS / Linux
export DSH_STOCK_PYTHON=/完整/路径/python3
```

设完再跑一次体检。`DSH_STOCK_PYTHON` 优先级最高，桥会先用它。

---

## 怎么用

装好之后，**直接用大白话问**就行：

```
茅台怎么样
600519 有什么风险
南华期货最近有什么动静
```

模式切过去之后，模型的回答会带上：事实（每个数字带出处）+ 踩雷清单 + 事件雷达 + 缺口清单 + 最坏情况。

### 引擎也能单独跑

所有引擎命令都走一个 Node 桥，**参数和 `python -m stockctl` 完全一致**：

```bash
node "<插件目录>/scripts/stockctl.mjs" warm                            # 每天第一次（缓存热时约 2 秒）
node "<插件目录>/scripts/stockctl.mjs" ask "五粮液怎么样" --json
node "<插件目录>/scripts/stockctl.mjs" check --answer 回答.txt --source 引擎输出.txt
node "<插件目录>/scripts/stockctl.mjs" selftest
```

桥存在的唯一理由：**不同机器上 Python 叫什么、在哪，完全不一样**，而且
`python` 在 Windows 上可能是应用商店的空壳。桥自己去找，你不需要知道。

### ⏱ 关于等待时间（照实说，别被吓到）

| 情况 | 要等多久 |
|---|---|
| 每天第一次跑 `warm`（缓存已热） | 约 2 秒 |
| 每天第一次跑 `warm`（全冷） | 约 77 秒 |
| 问一只**以前问过**的票 | 6~7 秒 |
| 问一只**全新**的票 | **1~2 分钟** |

新票那 1~2 分钟不是在算，是在**下载并解析它那份年报 PDF**（两三百页）。躲不掉，但同一份文件第二次就快了。

---

## 它凭什么可信（这是这个项目真正的重点）

市面上 AI 炒股工具的常见问题是：**它说的数字你没法核。**

这个项目针对这一点做了三件事：

### 1 · 数字溯源闸门（机械强制，不是提示词里写"请勿编造"）

```
你写完回答 → 存成文件 → 跑 check
                            ↓
                退出码 0  → 可以发
                退出码非 0 → 它把「找不到出处的数字」逐条列出来 → 改掉再跑
```

**这一条是「模型不许自己算数」唯一的机械保证。** 提示词可以被忽略，退出码不能被忽略。

### 2 · 三态，不是两态

每个风险项有两种"没事"必须分开说：

```
命中  /  未发现  /  无法判定
                    ↑ 这一项本次根本没查，**不等于没事**
```

把「没查」说成「没问题」是最常见的一种骗人方式，这个项目专门防它。

### 3 · 情报分三类

| 类 | 例子 | 能当事实说吗 |
|---|---|---|
| ① 可核验 | 公告、监管处罚、交易所披露 | ✅ |
| ② 可计数 | 「N 家独立媒体报道」（不含转载） | ✅ 但只说明"有报道"，不说明真假 |
| ③ 不可核验 | 自媒体、股吧、传闻 | ⚠️ **只陈列、明标，永不进结论** |

---

## 兼容性（**已实测，不是推测**）

本插件往 profile 里插两样东西：一个 **skill**（纪律）和一个 **preset**（模式）。
实测结果：

| profile 类型 | skill | preset | 实测证据 |
|---|---|---|---|
| **`@deepseek-ai/dsh-web-app`**（日常用的 Web GUI） | ✅ | ✅ | 启动 `did not activate` 命中 **0**，无任何告警 |
| `@deepseek-ai/dsh-headless` | ✅ | ⚠️ | `did not activate` 命中 **1**：`pending (waiting for service: agentPresets)` |

**为什么**：preset 需要 `@deepseek-ai/dsh-agent-preset-registry` 提供的 `agentPresets` 服务，
而 **headless 档默认不挂载它**（web 档挂载）。

**影响**：在 headless 档上，你**拿不到「模式」切换**，但**纪律（skill）和引擎照样能用**
—— 而且它是**警告，不是崩溃**，不会把 DSH 拖坏。

**想修的话**：那个档里自己加上 `@deepseek-ai/dsh-agent-preset-registry` 即可（本插件不自作主张替你加，
因为重复挂载同一个服务可能报错）。

---

## 已知限制（**没有藏在这段**）

- **只覆盖 A 股个股。** 不含加密货币、外汇、美股，也不含期货合约本身。
  （但**期货公司**是在 A 股上市的个股，属于覆盖范围。）
- **只对 DSH `0.2.0-rc.2` 验证过。**
- **`engine/vendor/astock.py` 里有两个函数是坏的**：`iwencai_search` / `iwencai_query`
  引用了 `IWENCAI_KEY` / `IWENCAI_BASE`，而这两个常量**全文件没有定义**
  （从上游提取时丢了）。调到它们只会 `NameError`。
  引擎主流程不走这两个函数，所以不影响使用 —— 但**不修**：那个文件上游写着「请勿手改」，
  而且它一个月发三个版本，改了下次重新提取就没了。
- **踩雷清单覆盖 6 项**（ST / 质押 / 减持 / 解禁 / 业绩预告 / 商誉），
  **覆盖不了**：财务造假、大股东掏空、行业周期反转、政策突变。
- **方向判定只实现了 3 项因子**（EP / BP / 换手率分位），其余 5 项**如实标未实现**。
  所以它经常给「观望」—— 那是设计，不是故障。
- **首跑慢**（见上），这是取数成本，不是 bug。

---

## 目录结构

```
dsh-stock-advisor/
├── cordis.patch.yml                 ← 接线：挂 skill + 注册模式（纯声明，无宿主代码）
├── skills/stock-advisor/SKILL.md    ← ★ 纪律文件。这个项目的灵魂
├── engine/                          ← Python 引擎
│   ├── stockctl.py  ask.py          ← 命令行面
│   ├── advisor/ compute/ data/ evidence/ judge/
│   ├── vendor/astock.py             ← 第三方取数实现（Apache-2.0，见 NOTICE）
│   └── requirements.txt
├── scripts/
│   ├── stockctl.mjs                 ← Node 桥（找 Python + 透传退出码）
│   ├── doctor.mjs                   ← 环境体检
│   └── lib/python.mjs               ← 解释器发现，只在这一个文件里
├── tools/extract_astock.py          ← 上游升级时重跑它重新提取 vendor/astock.py
└── docs/
```

---

## 升级

```bash
dsh plugin --profile web remove dsh-stock-advisor
dsh plugin --profile web add "git+https://github.com/HJSSSRX/dsh-stock-advisor.git"
```

**引擎自己带的缓存不会跟着升级走**（在 `engine/_cache/` 和 `engine/_pdf_cache/` 下），
重装后第一次会重新下载。想留着就先备份这两个目录。

---

## 许可证

- **本项目**：MIT，见 [`LICENSE`](LICENSE)
- **`engine/vendor/astock.py`**：来自 [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（**Apache-2.0**），
  署名、改动声明与升级路径见 [`NOTICE`](NOTICE)，许可证原文在 [`THIRD_PARTY_LICENSES/`](THIRD_PARTY_LICENSES/)
  —— **该文件不受 MIT 许可覆盖。**

---

## 最后一句

这个项目的全部价值不在代码，在纪律。

代码会被人重写、模型会升级、会话会结束 ——
但只要 `skills/stock-advisor/SKILL.md` 还在，纪律就还在。

改那个文件之前先问一句：**这条删掉之后，它会不会开始骗人。**
