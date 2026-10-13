# 架构

这份文档只回答一个问题：**这东西为什么长这样。**

> 纪律本身不在这里 —— 纪律的唯一 owner 是
> [`skills/stock-advisor/SKILL.md`](../skills/stock-advisor/SKILL.md)。
> 复制一份到这里就会有两个 owner，那是 bug，不是文档。见「一票否决」一节。

---

## 一、四层，各管一件事

```
┌─────────────────────────────────────────────────────────────┐
│ ① 纪律层   skills/stock-advisor/SKILL.md                     │
│    十条铁律 · 情报三分类 · 回答结构 · 最坏情况必须主动说        │
│    ← 这是项目的灵魂。代码可以重写，它不行。                     │
├─────────────────────────────────────────────────────────────┤
│ ② 接线层   cordis.patch.yml                                  │
│    把 ① 挂进 skill 目录 + 注册一个 preset（模式）              │
│    ← 纯声明，**没有一行宿主插件代码**                          │
├─────────────────────────────────────────────────────────────┤
│ ③ 桥接层   scripts/stockctl.mjs + lib/python.mjs             │
│    找 Python · 透传退出码 · 不改工作目录                       │
│    ← 换一台机器唯一需要变的东西，就这一层                       │
├─────────────────────────────────────────────────────────────┤
│ ④ 引擎层   engine/（Python）                                  │
│    取数 → 计算 → 核验 → 判读 → 闸门                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、一次提问的完整数据流

```
用户：「茅台怎么样」
   │
   ├─ ① 模型先加载 skill，拿到纪律
   │
   ├─ node scripts/stockctl.mjs warm            ← 每天第一次
   │     └─ 预热「一份数据服务全市场」的三样：全市场名单 / 横截面 / ST 名单
   │
   ├─ node scripts/stockctl.mjs ask "茅台怎么样" --json
   │     │
   │     ├─ data/      取数（34 个数据源，带 source / as_of / caliber 三元组）
   │     ├─ evidence/  内容指纹 eid + span 核验 + PDF 一手来源
   │     ├─ compute/   横截面 · 事件雷达 · 踩雷排查 · 三表勾稽 · 技术指标
   │     ├─ judge/     确定性打分（8 项等权因子，每项带 DOI）
   │     └─ → JSON：{ facts, minefield, events, tieout, computed, claim, market, gaps, notices }
   │
   ├─ 模型据此写回答
   │
   └─ node scripts/stockctl.mjs check --answer 回答.txt --source 引擎输出.txt
         │
         ├─ 退出码 0  → 可以发
         └─ 退出码非0 → 列出「找不到出处的数字」→ 改掉再跑
```

**最后那一步是整个项目的地基。** 提示词可以被忽略，退出码不能被忽略。

---

## 三、为什么桥这一层必须存在

见 [`scripts/lib/python.mjs`](../scripts/lib/python.mjs) 的文件头。三个硬理由：

| 问题 | 不解决会怎样 |
|---|---|
| Windows 上 `python` 可能是应用商店的空壳 | 第一条命令就报 `Python was not found` |
| 有人装在 `C:\Python312`，有人装在 `%LOCALAPPDATA%\Programs\Python` | 写死哪条都是错的 |
| **`check` 靠退出码表达「不许发」** | 中间加一层进程时吞掉退出码 = **拆掉唯一的机械保证** |

所以桥只做三件事，而且每件都有测试守着：

1. **找 Python**：逐个**真跑一遍**才算数（不是查 PATH 里有没有），优先级
   `DSH_STOCK_PYTHON` → PATH → 常见安装位置
2. **透传退出码**：`process.exit(r.status)` —— 这一行不能删
3. **不改工作目录**：用 `PYTHONPATH` 让 `-m stockctl` 可解析，而不是 `cd` 进引擎目录。
   因为改 cwd 会让调用方的相对路径全部失效（`check --answer 回答.txt` 直接 FileNotFoundError，
   这是实测撞到过的 bug，不是假想）。

---

## 四、为什么没有宿主插件代码

DSH 的插件不一定需要 `lib/index.js`。官方文档写得很清楚
（`dsh-app-boot` 的 `profile` 模块注释）：

> Bundles are npm packages whose manifest declares
> `"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }`
> …
> Resolution does not require the package to export `./package.json`.

本插件的两处 insert **指向的都是已存在的包**：

- `@deepseek-ai/dsh-skill-filesystem` —— 挂载包内 `skills/`
- `@deepseek-ai/dsh-agent-preset` —— 注册模式

所以不需要自己的运行时。**少一层代码 = 少一处随 DSH 版本漂移而坏掉的地方。**

### `!!js` 为什么只能写在「行内 config」

`cordis-plugin-loader` 的注释（`src/index.ts:106-112`）：

> Tree carriers (Group, Include) keep their configs literal: their entry and patch
> lists hold other rows' configs, whose `!!js` expressions belong to those rows' own fibers.

也就是说：**patch 顶层的 `!!js` 拿不到正确作用域，行内 config 的才会在那一行自己的
fiber 里求值。** 本插件的两条 `!!js` 都在行内 `config:` 下。

`!!js` 的求值实现（`src/config/utils.ts:5-9`）是：

```js
new Function('ctx', 'expr', 'with (ctx) { return eval(expr) }')
```

所以 `baseUrl` 是可用标识符 —— 这也正是 DSH 官方默认配置里挂载
`@deepseek-ai/dsh-agent-preset/skills` 用的写法。本插件那个 `createRequire(baseUrl).resolve(...)`
是**逐字照搬**它的，不是自创。

> 这个文件里有两条 `!!js`，它们是「仓库里不含任何写死路径」的**全部依据**。
> 所以它们有独立的单元测试：`scripts/verify-paths.mjs`，
> 它**逐字复制 loader 的求值器**来跑，还带一个「包不存在时必须报可诊断的错」的反例。

---

## 五、信任边界

这一节是这个项目最该被挑剔的地方，所以写清楚它**不**保证什么。

| 边界 | 本项目的态度 |
|---|---|
| **隐私** | `observe` 层记用户**原话**，按设计就不该离开本机。`.gitignore` 同时封了 `observations/` 和 `*.jsonl`。 |
| **路径** | 仓库里**零**本机绝对路径。这条由 `scripts/verify-paths.mjs` 和发布前的 grep 双重把关。 |
| **许可证** | `engine/vendor/astock.py` 是 Apache-2.0 的衍生文件，署名与改动声明在 `NOTICE`，原文在 `THIRD_PARTY_LICENSES/`。**它不受 MIT 覆盖。** |
| **不可核验的情报** | 永不进结论，只陈列并明标。这一条写在纪律里，是纪律第 4、5 条。 |
| **数字** | 每个数字必须能在引擎输出里找到（`check` 闸门）。 |

### 本插件**不**做的事

- 不自己算任何数字（引擎算，它只搬运）
- 不判断方向（打分器给，且打分器只实现了 3 项因子，其余如实标未实现）
- 不替你下决心

---

## 六、一票否决（改代码前先读这一节）

**`skills/stock-advisor/SKILL.md` 是纪律的唯一 owner。**

不要在 README、架构文档、注释里**复制**它的条文。
复制 = 两处会各自漂移 = 以后你不知道哪条是真的。

引用它就够了：写「见 SKILL.md 第 N 条」。

同样地：

- **缓存的唯一写入口**是 `engine/data/cache.py`
- **Python 解释器发现的唯一 owner** 是 `scripts/lib/python.mjs`
- **退出码语义的唯一 owner** 是 `scripts/stockctl.mjs`

要改这些行为，改 owner，不要在别处打补丁。
