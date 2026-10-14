#!/usr/bin/env node
/**
 * dsh-stock-advisor 手动安装器 —— 不碰 pnpm / git / 网络，只用文件操作。
 *
 * 什么时候用它：
 *   `dsh plugin --profile <名> add <包>` 装不上时（没 pnpm、没 git、连不上 GitHub…）。
 *   它做的是同一件事，只是绕开了包管理器和网络。
 *
 * 用法：
 *   node manual-install.mjs --profile web
 *   node manual-install.mjs --profile web --source /path/to/dsh-stock-advisor
 *   node manual-install.mjs --profile web --uninstall
 *
 * 默认 --source 是**本脚本所在目录的上一级**（也就是包根），
 * 所以直接把整个文件夹拷给对方、让他跑这一行就行。
 */
import {
  cpSync, existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, readdirSync, statSync,
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PKG_NAME = 'dsh-stock-advisor';
const MARKER = 'id: stock-advisor-skills';   // 用来判断「装没装过」

// ── 参数 ────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = { profile: '', source: '', dshHome: '', uninstall: false, force: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === '--profile' || a === '-p') out.profile = next();
    else if (a === '--source' || a === '-s') out.source = next();
    else if (a === '--dsh-home') out.dshHome = next();
    else if (a === '--uninstall') out.uninstall = true;
    else if (a === '--force') out.force = true;
    else if (a === '-h' || a === '--help') out.help = true;
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const HERE = path.dirname(fileURLToPath(import.meta.url));

if (args.help || !args.profile) {
  process.stdout.write(`dsh-stock-advisor 手动安装器

用法:
  node manual-install.mjs --profile <profile名> [--source <插件目录>] [--dsh-home <目录>]
  node manual-install.mjs --profile <profile名> --uninstall

参数:
  --profile,-p   DSH profile 名（就是 ~/.dsh/profiles/ 下的文件夹名，Web 界面一般是 web）
  --source,-s    插件目录，默认 = 本脚本所在目录的上一级
  --dsh-home     DSH 家目录，默认 $DSH_HOME 或 ~/.dsh
  --uninstall    卸载（只删插件目录 + 摘掉接线块，原始文件有备份）
  --force        目标已存在时也覆盖

它做了什么:
  1. 把插件目录整个复制到 <profile>/node_modules/${PKG_NAME}/
  2. 把接线块插进 <profile>/cordis.patch.yml（会先备份）
  3. 完事重启 DSH 即可

它不做什么:
  不跑 pnpm、不跑 git、不联网、不改你的全局配置、不碰其他插件。
`);
  process.exit(args.help ? 0 : 2);
}

const log = (s) => process.stdout.write(s + '\n');
const fail = (s) => { process.stderr.write('\n[×] ' + s + '\n'); process.exit(1); };

// ── 定位 DSH 家目录与 profile ────────────────────────────────────────────
const dshHome = args.dshHome
  || process.env.DSH_HOME
  || path.join(os.homedir(), '.dsh');

const profileDir = path.join(dshHome, 'profiles', args.profile);
if (!existsSync(profileDir)) {
  const avail = existsSync(path.join(dshHome, 'profiles'))
    ? readdirSync(path.join(dshHome, 'profiles')).filter((n) => statSync(path.join(dshHome, 'profiles', n)).isDirectory())
    : [];
  fail(`找不到 profile：${profileDir}\n` +
    (avail.length ? `    这台机器上有这些 profile：${avail.join(' / ')}\n` : '') +
    `    用 --profile <名字> 指定一个，或 --dsh-home <DSH家目录> 指定位置。`);
}
log(`[1/4] profile  : ${profileDir}`);

const source = path.resolve(args.source || path.join(HERE, '..'));
if (!existsSync(path.join(source, 'package.json')) || !existsSync(path.join(source, 'cordis.patch.yml'))) {
  fail(`--source 不像插件目录（缺 package.json 或 cordis.patch.yml）：${source}`);
}
let pkgName = '';
try { pkgName = JSON.parse(readFileSync(path.join(source, 'package.json'), 'utf8')).name || ''; } catch { /* 下面报 */ }
if (pkgName !== PKG_NAME) {
  fail(`--source 的 package.json 里 name 是 "${pkgName}"，期望 "${PKG_NAME}"。\n` +
    `    确认你指向的是插件根目录（里面有 package.json / cordis.patch.yml / engine / scripts）。`);
}
log(`[2/4] 插件源 : ${source}`);

// ── 目标位置 ────────────────────────────────────────────────────────────
const target = path.join(profileDir, 'node_modules', PKG_NAME);
const patchFile = path.join(profileDir, 'cordis.patch.yml');
const SKIP = new Set(['.git', '_cache', '_pdf_cache', '__pycache__', 'node_modules']);

function copyTree(from, to) {
  mkdirSync(to, { recursive: true });
  for (const name of readdirSync(from)) {
    if (SKIP.has(name)) continue;
    const s = path.join(from, name);
    const d = path.join(to, name);
    const st = statSync(s);
    if (st.isDirectory()) copyTree(s, d);
    else if (st.isFile()) cpSync(s, d);
  }
}

// ── 卸载分支 ────────────────────────────────────────────────────────────
if (args.uninstall) {
  const backup = patchFile + '.bak-manual-install';
  let how = '';
  if (existsSync(backup)) {
    // ★ 首选：直接用安装时留的备份还原 —— 这是「回到装之前」最可靠的做法，
    //   比按字符串裁剪去猜哪里是接线块的开头要稳得多。
    writeFileSync(patchFile, readFileSync(backup, 'utf8'), 'utf8');
    how = `已用安装时的备份还原（${path.basename(backup)}）`;
  } else {
    // 没有备份就按标记裁剪，并保证剩下的是一份合法 YAML（至少要有一行 []）
    const lines = readFileSync(patchFile, 'utf8').split('\n');
    let start = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes('dsh-stock-advisor')) { start = Math.max(0, i - 1); break; }
    }
    let rest = (start >= 0 ? lines.slice(0, start) : lines).join('\n').replace(/\s*$/, '\n');
    if (!/^\s*-\s/m.test(rest)) rest = rest + '[]\n';
    writeFileSync(patchFile, rest, 'utf8');
    how = '没有找到备份，已按标记裁剪并补回 [] 占位';
  }
  if (existsSync(target)) rmSync(target, { recursive: true, force: true });
  log(`[✔] 已卸载：插件目录已删，${how}`);
  process.exit(0);
}

// ── 安装：复制文件 ──────────────────────────────────────────────────────
if (existsSync(target) && !args.force) {
  log(`[3/4] 目标已存在，覆盖更新（想彻底重来加 --force，想删掉用 --uninstall）`);
}
rmSync(target, { recursive: true, force: true });
copyTree(source, target);
let n = 0;
(function count(d) { for (const f of readdirSync(d)) { const p = path.join(d, f); statSync(p).isDirectory() ? count(p) : n++; } })(target);
log(`[3/4] 已复制 : ${target}  (${n} 个文件)`);

// ── 安装：接线 ──────────────────────────────────────────────────────────
let patch = existsSync(patchFile) ? readFileSync(patchFile, 'utf8') : '';
writeFileSync(patchFile + '.bak-manual-install', patch, 'utf8');
log(`      patch 已备份 -> ${path.basename(patchFile)}.bak-manual-install`);

if (patch.includes(MARKER)) {
  log('[4/4] 接线块已存在，跳过（避免重复插入）');
} else {
  const block = readFileSync(path.join(source, 'cordis.patch.yml'), 'utf8');
  // ★ 关键：profile 自带的 patch 里有一行孤零零的 `[]`（空数组占位）。
  //   直接往后追加会变成 "[] 后面跟 - insert:"，那是**非法 YAML**，
  //   DSH 会报 "end of the stream or a document separator is expected"。
  //   所以先把那行摘掉。实测踩过这个坑。
  const lines = patch.split('\n');
  const kept = lines.filter((ln) => ln.trim() !== '[]');
  const removed = lines.length - kept.length;
  patch = kept.join('\n').replace(/\s*$/, '\n') + '\n' + block;
  writeFileSync(patchFile, patch, 'utf8');
  log(`[4/4] 接线块已插入${removed ? `（顺手摘掉了 ${removed} 行 [] 占位，否则 YAML 非法）` : ''}`);
}

log(`
────────────────────────────────────────────────────────────
装好了。接下来：

  1. 重启 DSH
  2. 新建任务时，模式选择器里应能看到「股票分析师」
     （没有的话，用 /stock-advisor 这个斜杠命令也能把纪律调起来）

还没装 Python？跑一下环境体检，它会告诉你差什么、给一条能照抄的命令：

  node "${path.join(target, 'scripts', 'doctor.mjs')}"

反悔了想删掉：

  node "${path.join(HERE, 'manual-install.mjs')}" --profile ${args.profile} --uninstall
────────────────────────────────────────────────────────────`);
