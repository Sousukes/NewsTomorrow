import { describe, expect, it } from 'vitest';
import swSource from './sw-template.js?raw';

/**
 * SW 模板本身的行为约定（E8 / P1-78）。
 *
 * 这些断言针对**模板源码**（注入逻辑由 `precache.spec.ts` 覆盖）：
 * 写操作不被缓存、回落带标记、缓存有上限——三条全是"错了也不会报错"的坑。
 */
describe('pwa/sw-template', () => {
  it('非 GET 一律不碰（写操作重发会重复计数，幂等键在服务端）', () => {
    expect(swSource).toContain("req.method !== 'GET'");
    expect(swSource).not.toContain("method === 'POST'");
  });

  it('离线回落打 X-Offline-Fallback 标记（UI 要能区分旧副本，不能假装在线）', () => {
    expect(swSource).toContain("headers.set('X-Offline-Fallback', '1')");
  });

  it('图片缓存有条数上限（无上限时几十张配图就是几百 MB）', () => {
    expect(swSource).toContain('IMG_MAX_ENTRIES');
    expect(swSource).toContain('async function trim(');
  });

  it('只处理"我们自己的源"：第三方与源站直连不被拦截', () => {
    expect(swSource).toContain('OWN_ORIGINS.includes(url.origin)');
    // 自己的源 = 注入的代理源 + 运行时 concat 的 SW 所在源
    expect(swSource).toContain('__OWN_ORIGINS__.concat(self.location.origin)');
  });

  it('页面导航断网时回落外壳（SPA 只有一个 html，回落它才有东西可显示）', () => {
    expect(swSource).toContain("cache.match('/index.html')");
  });

  it('旧版本缓存在 activate 时清理（不清则每个版本都留一份，Storage 无限增长）', () => {
    expect(swSource).toContain("k.startsWith('sw-precache-') && k !== PRECACHE");
  });

  it('更新由页面消息触发，而非自动 skipWaiting（自动激活会打断阅读）', () => {
    expect(swSource).toContain("event.data === 'SKIP_WAITING'");
  });

  it('API 走 network-first：先打网络，成功才写缓存', () => {
    const handler = swSource.slice(swSource.indexOf('async function apiHandler'));
    expect(handler.indexOf('await fetch(req)')).toBeLessThan(handler.indexOf('cache.match(req)'));
  });
});
