# 手动安装（连不上 GitHub / 没有 pnpm 时用这个）

`dsh plugin --profile web add "git+https://github.com/..."` 要三样东西：
**pnpm、git、以及能连上 github.com**。缺任何一个都装不上。
国内网络环境下最后一条尤其常见。

**这份文档给的是完全绕开这三样的路子。**

---

## 你需要先拿到文件

三种拿法，任选：

1. **对方给你的压缩包**（`dsh-stock-advisor.zip`）—— 最省事，解压即用
2. 能上 GitHub：网页右上角 `Code → Download ZIP`
3. 能上但慢：走镜像，例如
   `https://gh-proxy.com/https://github.com/HJSSSRX/dsh-stock-advisor/archive/refs/heads/main.zip`

> ⚠️ 别把 `gh-proxy.com/api.github.com/...` 当成能用的 API —— 实测那个镜像会
> **注入它自己的账号**并**忽略你发的 Authorization**。只把它当**下载通道**用。

---

## 装（三步，一条命令）

```bash
node "<解压出来的目录>/tools/manual-install.mjs" --profile web
```

就这一条。它做的是：

```
[1/4] 定位 profile        ~/.dsh/profiles/web
[2/4] 检查插件源          确认 package.json 里 name 是 dsh-stock-advisor
[3/4] 复制文件            -> <profile>/node_modules/dsh-stock-advisor/   (106 个文件)
      patch 已备份        -> cordis.patch.yml.bak-manual-install
[4/4] 插入接线块          顺手摘掉那行 [] 占位（不摘的话 YAML 非法）
```

**然后重启 DSH。** 新建任务时模式选择器里应能看到「**股票分析师**」；
看不到也不要紧 —— 在任何会话里打 `/stock-advisor` 一样能把纪律调起来。

### 参数

| 参数 | 说明 |
|---|---|
| `--profile <名>` | **必填**。Web 界面一般是 `web`。名字就是 `~/.dsh/profiles/` 下的文件夹名 |
| `--source <目录>` | 插件目录。默认＝脚本所在目录的上一级，所以一般不用给 |
| `--dsh-home <目录>` | DSH 家目录。默认 `$DSH_HOME` 或 `~/.dsh` |
| `--force` | 目标已存在时也覆盖 |

---

## 卸

```bash
node "<插件目录>/tools/manual-install.mjs" --profile web --uninstall
```

它删掉插件目录，并**用安装时留的备份把 `cordis.patch.yml` 还原回去**。
（没有备份时退化为按标记裁剪，并保证补回 `[]` 使文件仍是合法 YAML。）

---

## 完全手工（不跑脚本也懂原理）

脚本做的就是这两件事，你手动做也一样：

**① 复制文件**

把整个插件文件夹放到：

```
~/.dsh/profiles/<你的 profile>/node_modules/dsh-stock-advisor/
```

（Windows 上是 `C:\Users\<你>\.dsh\profiles\web\node_modules\dsh-stock-advisor\`）

**② 改 `cordis.patch.yml`**

打开 `~/.dsh/profiles/<你的 profile>/cordis.patch.yml`。
它出厂时长这样：

```yaml
# Your patch layer for this dsh profile, applied after every bundle layer:
# a top-level YAML array of loader patch entries (id-targeted config
# overrides, disables, and insert lists; `!!js` expressions allowed).
[]
```

**把那行 `[]` 删掉**（★ 这一步是全部诀窍所在，见下面的坑 1），
然后把你拿到的 `cordis.patch.yml` 里的内容**整段贴在原来的位置**。

**③ 重启 DSH。**

---

## 常见错误对照表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `failed to parse overlay .../cordis.patch.yml: YAMLException: end of the stream or a document separator is expected` | ★ **你把接线块追加在了 `[]` 后面**。`[]` 是空数组，后面不能再跟 `- insert:` | 删掉那行 `[]`，或直接用 `tools/manual-install.mjs`（它自动处理） |
| 启动时 `warning: 1 entry did not activate` + `preset-stock-analyst ... waiting for service: agentPresets` | 你用的是 **headless 档**，它没有挂 `@deepseek-ai/dsh-agent-preset-registry` | 这是**警告不是崩溃**。纪律和引擎照常能用，只是没有「模式」可切。想修就在那个档里自己加上那个包 |
| `[stockctl 桥] 没有找到可用的 Python` | 没装 Python 3.10+，或不在 PATH 里 | `node scripts/doctor.mjs` 看详情；或设 `DSH_STOCK_PYTHON` 指向 python 可执行文件 |
| `ModuleNotFoundError: No module named 'pandas'` | 缺 Python 依赖 | `doctor.mjs` 会打印一条能照抄的 `pip install` 命令 |
| `dsh: command not found` | 没装 DSH，或不在 PATH | 先装 DSH；或直接用完整路径调 `node manual-install.mjs`（脚本本身不依赖 `dsh` 命令） |
| 装完看不到「股票分析师」模式 | 没重启 DSH，或档里没有 preset 服务 | 先重启；还是看不到就用 `/stock-advisor` 这个斜杠命令 |

---

## 装完先体检

```bash
node "<插件目录>/scripts/doctor.mjs"
```

它会逐个真跑一遍，告诉你 Node / Python / 依赖包 / DSH 版本各自什么状态，
缺什么就给一条能照抄的命令。**它只读不改**，不会擅自装东西。

---

## 为什么这个插件能手动装，别的插件不行

因为**这个插件里没有一行运行时 JS 代码**。

`cordis.patch.yml` 里的两处 `insert` 指向的都是 DSH **已经自带**的包
（`@deepseek-ai/dsh-skill-filesystem` 负责挂 `skills/`、`@deepseek-ai/dsh-agent-preset` 负责注册模式），
所以它不需要被 pnpm 解析依赖树，只要文件在磁盘上、接线接上就行。

官方文档也确认了这一点：bundle 只需要在 `package.json` 里声明
`"dsh": { "bundle": { "patch": "./cordis.patch.yml" } }`，
**解析不要求包导出 `./package.json`，也没要求入口点**。

手动装时你其实绕开了 `dsh.profile.bundles` 这个列表 ——
接线块直接进了 profile 自己的 patch 层，而那一层是最后应用的，效果一样。
