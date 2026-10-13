// 验证 cordis.patch.yml 里那两条 !!js 表达式。
//
// 跑法：node scripts/verify-paths.mjs   （package.json 里也有 npm run verify:paths）
//
// 为什么单独测：这两条表达式是「仓库里不含任何写死路径」的全部依据 ——
// 它们必须在**任何机器、任何安装目录**下算出那台机器上的真实路径。
// 而它们只有在 DSH 加载时才会被求值，如果那时才发现错，代价是要么装不上、
// 要么更糟：装上了但静默指向一个不存在的路径。
//
// 这里**逐字复制 DSH loader 的求值实现**
// （cordis-plugin-loader/src/config/utils.ts:5-9）：
//     new Function('ctx','expr', 'with (ctx) { return eval(expr) }')
// 所以测的就是真货，不是我另写的一套近似。
import { createRequire } from 'node:module';
import { mkdirSync, symlinkSync, existsSync, rmSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const PKG = 'dsh-stock-advisor';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');

// ── 造一个「假 profile」：一个只有 node_modules/dsh-stock-advisor 的目录 ──
const FAKE = path.join(HERE, '_jstest', 'fakeprofile');
if (existsSync(path.join(HERE, '_jstest'))) rmSync(path.join(HERE, '_jstest'), { recursive: true, force: true });
mkdirSync(path.join(FAKE, 'node_modules'), { recursive: true });
const link = path.join(FAKE, 'node_modules', PKG);
try {
  symlinkSync(REPO, link, 'junction');
} catch (e) {
  console.error('建符号链接失败：', e.message);
  process.exit(2);
}

const baseUrl = pathToFileURL(path.join(FAKE, 'cordis.yml')).href;

// ── 与 loader 相同的求值器 ────────────────────────────────────────────────
// eslint-disable-next-line no-new-func
const evaluate = new Function('ctx', 'expr', 'with (ctx) { return eval(expr) }');

const P1 = `process.getBuiltinModule('node:path').join(process.getBuiltinModule('node:path').dirname(process.getBuiltinModule('node:module').createRequire(baseUrl).resolve('${PKG}/package.json')),
'skills')`;

const P2 = `((B) => \`引擎桥：\${B}\`)(process.getBuiltinModule('node:path').join(process.getBuiltinModule('node:path').dirname(process.getBuiltinModule('node:module').createRequire(baseUrl).resolve('${PKG}/package.json')), 'scripts', 'stockctl.mjs'))`;

let bad = 0;
const check = (name, expr, want) => {
  let got;
  try {
    got = evaluate({ baseUrl }, expr);
  } catch (e) {
    console.log(`  [FAIL] ${name} —— 求值抛错：${e.message}`);
    bad++;
    return;
  }
  const ok = want ? got === want : got.includes('stockctl.mjs');
  console.log(`  [${ok ? ' OK ' : 'FAIL'}] ${name}`);
  console.log(`         得到：${got}`);
  if (!ok) {
    console.log(`         期望：${want || '含 stockctl.mjs'}`);
    bad++;
  }
};

console.log('baseUrl =', baseUrl);
console.log('');
check('① skills 目录', P1, path.join(REPO, 'skills'));
check('② 引擎桥路径（persona 模板字面量）', P2);

// ── 反例：包不可解析时，报错是否可诊断 ────────────────────────────────
console.log('');
const EMPTY = path.join(HERE, '_jstest', 'emptyprofile');
mkdirSync(EMPTY, { recursive: true });
try {
  evaluate({ baseUrl: pathToFileURL(path.join(EMPTY, 'cordis.yml')).href }, P1);
  console.log('  [FAIL] 反例：包不存在时竟然没抛错（说明表达式没真的去解析）');
  bad++;
} catch (e) {
  const m = String(e.message || e);
  const readable = /Cannot find|MODULE_NOT_FOUND|not found/i.test(m);
  console.log(`  [${readable ? ' OK ' : 'FAIL'}] 反例：包不存在时报错可诊断`);
  console.log(`         ${m.split('\n')[0]}`);
  if (!readable) bad++;
}

rmSync(path.join(HERE, '_jstest'), { recursive: true, force: true });
console.log('');
console.log(bad === 0 ? '✅ !!js 表达式验证通过' : `❌ 有 ${bad} 项没过`);
process.exit(bad === 0 ? 0 : 1);
