/**
 * Python 解释器发现 —— 全部路径逻辑只在这一个文件里。
 *
 * 为什么需要它：引擎是 Python 写的，而 DSH 用户机器上「Python 叫什么、在哪」
 * 三者都不一样（Windows 上 `python` 可能是应用商店的空壳，报 "Python was not found"）。
 * 所以这里不猜，**逐个真跑一遍**，跑得通才算数。
 *
 * 优先级：环境变量 DSH_STOCK_PYTHON > PATH 上的 python3/python/py > 常见安装位置。
 */
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/** 最低可接受的 Python 版本。低于它就明确报错，不硬试。 */
export const MIN_PY = [3, 10];

/** 探测一个候选解释器；返回 {cmd, preArgs, version} 或 null。 */
function probe(cmd, preArgs = []) {
  let r;
  try {
    r = spawnSync(
      cmd,
      [...preArgs, '-c', 'import sys;print("%d.%d" % sys.version_info[:2])'],
      { encoding: 'utf8', windowsHide: true, timeout: 20000 },
    );
  } catch {
    return null;
  }
  if (r.error || r.status !== 0) return null;
  const v = String(r.stdout || '').trim();
  const [maj, min] = v.split('.').map(Number);
  if (!Number.isFinite(maj) || !Number.isFinite(min)) return null;
  if (maj < MIN_PY[0] || (maj === MIN_PY[0] && min < MIN_PY[1])) return null;
  return { cmd, preArgs, version: v };
}

/** Windows 上枚举常见安装位置里的 python.exe（含用户级与系统级）。 */
function windowsLikelyPaths() {
  const roots = [
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python'),
    process.env.LOCALAPPDATA || '',
    'C:\\',
    path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Python'),
  ];
  const found = [];
  for (const root of roots) {
    if (!root || !existsSync(root)) continue;
    let entries;
    try {
      entries = readdirSync(root);
    } catch {
      continue;
    }
    for (const name of entries) {
      if (!/^Python\d/i.test(name)) continue;
      const exe = path.join(root, name, 'python.exe');
      if (existsSync(exe)) found.push(exe);
    }
  }
  return found;
}

/** 候选列表（只生成候选，不保证可用）。 */
export function candidates() {
  const out = [];
  const pushed = new Set();
  const push = (cmd, preArgs = []) => {
    const key = cmd + '\u0000' + preArgs.join(',');
    if (pushed.has(key)) return;
    pushed.add(key);
    out.push({ cmd, preArgs });
  };

  if (process.env.DSH_STOCK_PYTHON) push(process.env.DSH_STOCK_PYTHON);

  if (process.platform === 'win32') {
    push('python3');
    push('python');
    push('py', ['-3']);
    for (const exe of windowsLikelyPaths()) push(exe);
  } else {
    push('python3');
    push('python');
  }
  return out;
}

/** 找到第一个真能跑的解释器；找不到返回 null。 */
export function resolvePython() {
  for (const c of candidates()) {
    const ok = probe(c.cmd, c.preArgs);
    if (ok) return ok;
  }
  return null;
}

/** 检查引擎需要的第三方包是否齐全。返回 {pkg: version|null}。 */
export function checkDeps(py, pkgs) {
  const code = pkgs
    .map((p) => `print("${p}=" + getattr(__import__("${p}"), "__version__", "?"))`)
    .join('\n');
  const r = spawnSync(py.cmd, [...py.preArgs, '-c', code], {
    encoding: 'utf8',
    windowsHide: true,
    timeout: 60000,
  });
  const found = {};
  for (const p of pkgs) found[p] = null;
  for (const line of String(r.stdout || '').split('\n')) {
    const i = line.indexOf('=');
    if (i > 0) {
      const name = line.slice(0, i).trim();
      if (name in found) found[name] = line.slice(i + 1).trim();
    }
  }
  return found;
}

/** 给用户的「一条可以照抄的命令」。 */
export function installHint(py, missing) {
  const pkgs = missing.join(' ');
  const pyCmd = [py.cmd, ...py.preArgs].join(' ');
  return `${pyCmd} -m pip install ${pkgs}`;
}
