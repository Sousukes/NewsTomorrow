/** SW 模板的占位符（构建期注入；命名避免与真实内容撞上） */
export const PRECACHE_TOKEN = '__PRECACHE__';
export const VERSION_TOKEN = '__VERSION__';
export const OWN_ORIGINS_TOKEN = '__OWN_ORIGINS__';

/** 把清单与版本注入 SW 模板。缺占位符直接抛错——静默产出残缺 SW 无法排查。 */
export function buildServiceWorker(
  template: string,
  urls: string[],
  version: string,
  ownOrigins: string[] = [],
): string {
  for (const token of [PRECACHE_TOKEN, OWN_ORIGINS_TOKEN]) {
    if (!template.includes(token)) {
      throw new Error(`SW 模板缺少占位符 ${token}，拒绝产出残缺的 Service Worker`);
    }
  }
  return (
    template
      .replace(PRECACHE_TOKEN, JSON.stringify(urls))
      .replace(OWN_ORIGINS_TOKEN, JSON.stringify(ownOrigins))
      // 版本号必须全局替换：只换第一处会让缓存键与版本号不一致
      .replace(new RegExp(VERSION_TOKEN, 'g'), version)
  );
}
