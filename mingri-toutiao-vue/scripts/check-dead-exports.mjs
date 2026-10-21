/**
 * 死导出扫描：找出「export 了却没有任何外部引用」的符号（P1-87 / D9）。
 *
 * 为什么需要它：导出即承诺公共面。一个没有消费者的导出不会报错、不会告警，
 * 只会让人以为"这东西能从外面调"——`clearStepUpToken` 们就是这么攒下来的
 * （D9 清点时一次性捞出 3 个）。肉眼扫全仓不现实，固化成脚本才可重复。
 *
 * 用法：node scripts/check-dead-exports.mjs
 *   - 函数类死导出 → **计入失败**（exit 1）：这类几乎一定是该收窄的；
 *   - 类型类（interface / type）→ 只报告，不计失败：它们常常是"给未来消费者"
 *     或与同文件内的实现配套，误报率高于函数类，交给人工判断。
 *
 * 判据是"本文件之外零引用"。注意它**看不到动态引用**（字符串拼出来的调用、
 * 模板里用 `<component :is>` 之类），所以命中项要人工确认一遍再删——
 * 脚本是放大器，不是判决书。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../src', import.meta.url));

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|vue)$/.test(name)) out.push(p);
  }
  return out;
}

const files = walk(ROOT);
const corpus = new Map(files.map((f) => [f, readFileSync(f, 'utf8')]));

const unused = [];
for (const [file, text] of corpus) {
  // 测试与 story 不是消费方：它们引用符号是为了测它，不代表生产代码在用
  if (file.endsWith('.spec.ts') || file.includes('.stories.')) continue;

  const decls = [
    ...text.matchAll(/^export (?:async )?function ([A-Za-z0-9_]+)/gm),
    ...text.matchAll(/^export const ([A-Za-z0-9_]+)/gm),
    ...text.matchAll(/^export interface ([A-Za-z0-9_]+)/gm),
    ...text.matchAll(/^export type ([A-Za-z0-9_]+)/gm),
  ];

  for (const m of decls) {
    const name = m[1];
    // 同文件内自引用的次数（排除定义行本身）
    const selfRefs = (text.match(new RegExp(`\\b${name}\\b`, 'g')) ?? []).length - 1;
    let external = 0;
    for (const [other, otherText] of corpus) {
      if (other === file) continue;
      if (new RegExp(`\\b${name}\\b`).test(otherText)) external += 1;
    }
    if (external === 0) {
      unused.push({
        // 只打相对路径：绝对路径在 Windows 下又长又带反斜杠，读起来费劲
        file: file.slice(ROOT.length + 1).replaceAll('\\', '/'),
        name,
        selfRefs,
        kind: m[0].includes('function') ? 'function' : 'type',
      });
    }
  }
}

const deadFns = unused.filter((u) => u.kind === 'function');
const deadTypes = unused.filter((u) => u.kind === 'type');

console.log(`扫描 ${files.length} 个文件（排除 spec/stories）`);
for (const u of deadFns)
  console.log(`  [函数] ${u.file}: ${u.name}（本文件自引用 ${u.selfRefs} 次）`);
for (const u of deadTypes) console.log(`  [类型] ${u.file}: ${u.name}`);

console.log(`\n死导出：函数类 ${deadFns.length} 个，类型类 ${deadTypes.length} 个`);
if (deadFns.length) {
  console.log('函数类死导出应收窄为模块内可见（或补上导出理由的注释）。');
  process.exit(1);
}
console.log('无函数类死导出。');
