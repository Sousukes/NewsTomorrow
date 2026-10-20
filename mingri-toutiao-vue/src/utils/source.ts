/**
 * 离线推导来源标识（真后端下发 rss_sources.key，前端只透传）。
 * 必须用哈希而非下标：数组顺序一变，已关注的 key 会指向别的源。
 */
export function mockSourceKey(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `src_${h.toString(36)}`;
}
