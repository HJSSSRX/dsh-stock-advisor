#!/usr/bin/env node
/**
 * 环境体检 —— 装机后先跑这个。
 *
 *   node scripts/doctor.mjs
 *
 * 它只**看**，不改任何东西：报告 Node / Python / 依赖包 / DSH 版本 / 引擎目录
 * 各自是否就绪；缺依赖时打印**一条可以直接照抄的命令**，由使用者自己决定跑不跑。
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { resolvePython, candidates, checkDeps, installHint, MIN_PY } from './lib/python.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(HERE, '..');
const ENGINE = path.join(PKG_ROOT, 'engine');

/** 引擎真正 import 的必装包（缺了会直接崩）。 */
const REQUIRED = ['requests', 'pandas', 'numpy'];
/** 可选包：缺了只是少几路数据源，引擎会用 gaps 如实报「没取到」。 */
const OPTIONAL = ['akshare', 'mootdx', 'baostock', 'pypdf'];

const lines = [];
const ok = (s) => lines.push(`  [ OK ] ${s}`);
const bad = (s) => lines.push(`  [FAIL] ${s}`);
const warn = (s) => lines.push(`  [WARN] ${s}`);

let failures = 0;

// ── 1. Node ────────────────────────────────────────────────────────────
lines.push('Node');
lines.push(`  版本 ${process.version}`);
lines.push('');

// ── 2. 引擎目录 ────────────────────────────────────────────────────────
lines.push('引擎目录');
if (existsSync(path.join(ENGINE, 'stockctl.py'))) {
  ok(ENGINE);
} else {
  failures++;
  bad(`找不到 ${path.join(ENGINE, 'stockctl.py')} —— 包安装不完整`);
}
lines.push('');

// ── 3. Python ──────────────────────────────────────────────────────────
lines.push(`Python（要求 >= ${MIN_PY.join('.')}）`);
const py = resolvePython();
if (py) {
  ok(`使用 ${[py.cmd, ...py.preArgs].join(' ')}  →  ${py.version}`);
} else {
  failures++;
  bad('没有找到可用的 Python。逐个试过：');
  for (const c of candidates()) lines.push(`         · ${[c.cmd, ...c.preArgs].join(' ')}`);
  lines.push('       装一个：https://www.python.org/downloads/');
  lines.push('       或指定：DSH_STOCK_PYTHON=<python 绝对路径>');
}
lines.push('');

// ── 4. 依赖包 ──────────────────────────────────────────────────────────
let missing = [];
lines.push('Python 依赖');
if (py) {
  const req = checkDeps(py, REQUIRED);
  missing = REQUIRED.filter((p) => !req[p]);
  for (const p of REQUIRED) {
    if (req[p]) ok(`${p} ${req[p]}`);
    else {
      failures++;
      bad(`${p} 未安装（必装）`);
    }
  }
  const opt = checkDeps(py, OPTIONAL);
  for (const p of OPTIONAL) {
    if (opt[p]) ok(`${p} ${opt[p]}`);
    else warn(`${p} 未安装（可选，缺了会少几路数据源）`);
  }
} else {
  warn('Python 未就绪，跳过依赖检查');
}
lines.push('');

// ── 5. DSH 版本（往上找 node_modules/@deepseek-ai/dsh）─────────────────
lines.push('DSH');
const dshPkg = findUpward(PKG_ROOT, path.join('node_modules', '@deepseek-ai', 'dsh', 'package.json'));
if (dshPkg) {
  try {
    const v = JSON.parse(readFileSync(dshPkg, 'utf8')).version;
    ok(`DSH ${v}（${dshPkg}）`);
    if (v !== '0.2.0-rc.2') {
      warn('本插件只在 DSH 0.2.0-rc.2 上验证过；版本不符时 preset 骨架引用的包名可能对不上。');
    }
  } catch {
    warn(`读不出 DSH 版本：${dshPkg}`);
  }
} else {
  warn('没找到 DSH 包（如果你是从 DSH profile 外跑这个脚本，属正常）');
}
lines.push('');

function findUpward(start, rel) {
  let dir = start;
  for (let i = 0; i < 6; i++) {
    const p = path.join(dir, rel);
    if (existsSync(p)) return p;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

// ── 汇总 ───────────────────────────────────────────────────────────────
process.stdout.write('\n' + lines.join('\n') + '\n\n');
process.stdout.write('═'.repeat(64) + '\n');
if (failures === 0) {
  process.stdout.write('结论：环境就绪。\n\n');
  process.stdout.write('下一步（每天第一次用之前跑一次，缓存热时约 2 秒）：\n');
  process.stdout.write(`  node "${path.join(HERE, 'stockctl.mjs')}" warm\n\n`);
  process.stdout.write('然后就能问了（第一次问一只全新的票要等 1~2 分钟，在下它的年报）：\n');
  process.stdout.write(`  node "${path.join(HERE, 'stockctl.mjs')}" ask "五粮液怎么样" --json\n`);
} else {
  process.stdout.write(`结论：有 ${failures} 项没就绪，先处理上面标 [FAIL] 的。\n\n`);
  if (missing.length && py) {
    process.stdout.write('缺的依赖可以照抄这一条装：\n');
    process.stdout.write('  ' + installHint(py, missing) + '\n');
  }
}
process.stdout.write('═'.repeat(64) + '\n');

process.exit(failures === 0 ? 0 : 1);
