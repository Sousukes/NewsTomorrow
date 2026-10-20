import { describe, expect, it } from 'vitest';
import { buildServiceWorker, OWN_ORIGINS_TOKEN, PRECACHE_TOKEN, VERSION_TOKEN } from './precache';

/**
 * precache 注入（E8 / P1-78）。
 *
 * 这条链路错了**不会有任何运行时报错**，只会表现为"离线打不开"——所以必须是单测。
 * 三个坑分别对应：
 * 1. 占位符没替换干净 → SW 里残留字面量，`PRECACHE_URLS` 是 undefined，
 *    `install` 里 `undefined.map` 直接抛，SW 装不上（且只在浏览器控制台可见）；
 * 2. 只替换了第一处版本号 → 缓存键与比较用的版本号不一致，旧缓存永不清理；
 * 3. 清单里混入手写裸路径 → hash 名对不上，永远命不中。
 */

// 模板里版本号出现 **3 次**：一次定义、两次被引用（缓存键与目录名）。
// 只换第一处的恶果正是这两处引用对不上——旧缓存永不清理。
const template = [
  `const VERSION = '${VERSION_TOKEN}';`,
  `const PRECACHE_URLS = ${PRECACHE_TOKEN};`,
  `const OWN_ORIGINS = ${OWN_ORIGINS_TOKEN};`,
  `const KEY_A = 'sw-precache-${VERSION_TOKEN}';`,
  `const KEY_B = '${VERSION_TOKEN}/x';`,
].join('\n');

describe('pwa/precache', () => {
  it('占位符全部被替换，产物里不留字面量', () => {
    const out = buildServiceWorker(template, ['/assets/index-abc.js'], 'b1', ['https://api.x.com']);
    expect(out).not.toContain(PRECACHE_TOKEN);
    expect(out).not.toContain(OWN_ORIGINS_TOKEN);
    expect(out).toContain('const OWN_ORIGINS = ["https://api.x.com"];');
    expect(out).toContain('const PRECACHE_URLS = ["/assets/index-abc.js"];');
  });

  it('版本号的每一处都被替换（只换第一处会让缓存键与版本号不一致）', () => {
    const out = buildServiceWorker(template, [], 'v9', []);
    // 定义 + 两处引用都必须换成同一个值
    expect(out.match(/v9/g) ?? []).toHaveLength(3);
    expect(out).toContain("'sw-precache-v9'");
    expect(out).toContain("'v9/x'");
  });

  it('清单按传入顺序原样进入（顺序影响 install 时的并发，不去重不排序）', () => {
    const urls = ['/index.html', '/assets/a.js', '/assets/b.css'];
    const out = buildServiceWorker(template, urls, 'b1', []);
    expect(out).toContain(JSON.stringify(urls));
  });

  it('空清单合法（极小部署也产出一个能装的 SW，只是首次离线不可读）', () => {
    const out = buildServiceWorker(template, [], 'b1', []);
    expect(out).toContain('const PRECACHE_URLS = [];');
  });

  it('模板缺占位符时**抛错**而不是悄悄产出残缺 SW', () => {
    // 静默产出的 SW 会缓存空清单、或 OWN_ORIGINS 缺失导致跨源图片全漏——
    // 那种失效在运行时毫无线索，必须炸在构建期
    expect(() => buildServiceWorker('const a = 1;', ['/x'], 'b1')).toThrow(/占位符/);
    expect(() => buildServiceWorker(`const A = ${PRECACHE_TOKEN};`, ['/x'], 'b1')).toThrow(
      /占位符/,
    );
  });

  it('跨域 API 部署时把代理源注进去（否则配图一张都缓存不到）', () => {
    const out = buildServiceWorker(template, [], 'b1', ['https://api.example.com']);
    expect(out).toContain('["https://api.example.com"]');
  });

  it('真实模板也能被正确注入（防模板被改成别的占位符写法）', async () => {
    const { default: swSource } = await import('./sw-template.js?raw');
    const out = buildServiceWorker(swSource, ['/index.html'], 'b7', []);
    expect(out).not.toContain('__PRECACHE__');
    expect(out).not.toContain('__VERSION__');
    expect(out).toContain("const VERSION = 'b7'");
    // 缓存键是模板字符串引用 VERSION 变量（不是再写一遍版本号）——由定义处保证一致
    expect(out).toContain('const PRECACHE = `sw-precache-${VERSION}`');
  });
});
