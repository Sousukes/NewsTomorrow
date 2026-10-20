/**
 * 把 SW 模板转成可用的 Service Worker 源码（E8 / P1-78）。
 *
 * 抽成纯函数而不是塞进 Vite 插件：**这是整条链路上最容易出错的一步**——
 * 若清单没被正确注入，SW 会缓存一份空清单或上一批的 hash 名，而这种失效
 * 在运行时毫无报错（只是"离线打不开"）。能被单测直接调用，错了立刻红。
 *
 * ⚠️ 顺序敏感：先替换 precache 占位符，再用全局正则换版本号。
 * 反过来也行，但**绝不能用 `String.replace` 的字符串形式换版本号**——
 * 它只替换第一处，模板里若出现第二次（如缓存键拼接）就会漏。
 */

/** 模板里的占位符。命名刻意不可能与真实内容撞上 */
export const PRECACHE_TOKEN = '__PRECACHE__';
export const VERSION_TOKEN = '__VERSION__';
export const OWN_ORIGINS_TOKEN = '__OWN_ORIGINS__';

export function buildServiceWorker(
  template: string,
  urls: string[],
  version: string,
  ownOrigins: string[] = [],
): string {
  // 两个占位符都必须替换：缺任何一个都说明模板被改过。静默产出一份
  // `PRECACHE_URLS` 为 undefined 或 `OWN_ORIGINS` 缺失的 SW，
  // 比构建失败难查得多——后者至少会告诉你哪里错了。
  for (const token of [PRECACHE_TOKEN, OWN_ORIGINS_TOKEN]) {
    if (!template.includes(token)) {
      throw new Error(`SW 模板缺少占位符 ${token}，拒绝产出残缺的 Service Worker`);
    }
  }
  return template
    .replace(PRECACHE_TOKEN, JSON.stringify(urls))
    .replace(OWN_ORIGINS_TOKEN, JSON.stringify(ownOrigins))
    .replace(new RegExp(VERSION_TOKEN, 'g'), version);
}
