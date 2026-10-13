#!/usr/bin/env node
/**
 * stockctl 桥 —— 把 `python -m stockctl …` 包成一条与机器无关的命令。
 *
 *   node scripts/stockctl.mjs warm
 *   node scripts/stockctl.mjs ask "五粮液怎么样" --json
 *   node scripts/stockctl.mjs check --answer 回答.txt --source 引擎输出.txt
 *
 * 参数与直接跑 `python -m stockctl` **完全一致**，本文件只做两件事：
 *   ① 找到可用的 Python（见 lib/python.mjs）
 *   ② **原样透传退出码** —— 这一点不能省：`check` 就是靠退出码非 0 表达
 *      「回答里有数字找不到出处，不许发」。吞掉退出码等于拆掉唯一的机械保证。
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { resolvePython, candidates } from './lib/python.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, '..');
const ENGINE = path.join(PKG_ROOT, 'engine');

const argv = process.argv.slice(2);

function die(msg, code = 2) {
  process.stderr.write(msg.endsWith('\n') ? msg : msg + '\n');
  process.exit(code);
}

if (argv.length === 0 || argv[0] === '-h' || argv[0] === '--help') {
  process.stdout.write(
    `stockctl 桥（node scripts/stockctl.mjs …）\n\n` +
      `引擎目录 : ${ENGINE}\n` +
      `用法     : node scripts/stockctl.mjs <子命令> [参数…]\n` +
      `子命令   : check | ask | tieout | selftest | warm | profile | observe\n` +
      `环境变量 : DSH_STOCK_PYTHON=<python 可执行文件绝对路径>（可选，优先使用）\n\n` +
      `下一步   : node scripts/doctor.mjs   ← 先跑这个看环境齐不齐\n`,
  );
  process.exit(0);
}

if (!existsSync(ENGINE)) {
  die(
    `[stockctl 桥] 找不到引擎目录：${ENGINE}\n` +
      `这说明包安装不完整（engine/ 丢了）。重装本包，或检查 npm 是否把 engine/ 排除掉了。`,
  );
}

const py = resolvePython();
if (!py) {
  const tried = candidates()
    .map((c) => [c.cmd, ...c.preArgs].join(' '))
    .join('\n  · ');
  die(
    `[stockctl 桥] 没有找到可用的 Python（要求 3.10 及以上）。\n` +
      `已经逐个试过这些，都不行：\n  · ${tried}\n\n` +
      `怎么办（三选一）：\n` +
      `  1. 装一个 Python 3.10+：https://www.python.org/downloads/\n` +
      `  2. 已经有 Python，但不在 PATH 里 → 设环境变量后重试：\n` +
      `     Windows PowerShell : $env:DSH_STOCK_PYTHON="C:\\完整\\路径\\python.exe"\n` +
      `     macOS / Linux      : export DSH_STOCK_PYTHON=/完整/路径/python3\n` +
      `  3. 跑体检看详细报告：node "${path.join(HERE, 'doctor.mjs')}"\n`,
  );
}

// ★★★ 两处不能动，都是有实测理由的：
//   ① `cwd` 保持调用方的工作目录 —— 桥改 cwd 会让所有相对路径失效
//      （`check --answer 回答.txt` 直接 FileNotFoundError，实测撞到过）。
//   ② 用 PYTHONPATH 而不是改 cwd 来让 `-m stockctl` 可解析 —— 这样引擎
//      自己的缓存/数据路径仍然锚在它自己的 __file__ 上（data/cache.py:16
//      等全是这个写法，已逐个确认），两边互不干扰。
const env = { ...process.env };
env.PYTHONPATH = env.PYTHONPATH
  ? `${ENGINE}${path.delimiter}${env.PYTHONPATH}`
  : ENGINE;

const r = spawnSync(py.cmd, [...py.preArgs, '-m', 'stockctl', ...argv], {
  cwd: process.cwd(),
  env,
  stdio: 'inherit',
  windowsHide: true,
});

if (r.error) {
  die(`[stockctl 桥] 启动 Python 失败：${r.error.message}`);
}
// 退出码原样往上递 —— 这一行是 `check` 闸门能起作用的前提。
process.exit(typeof r.status === 'number' ? r.status : 1);
