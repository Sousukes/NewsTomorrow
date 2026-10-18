/**
 * 来源标识（E6b）的**离线推导**。
 *
 * 真后端由 `rss_sources.key` 下发（`NewsItem.sourceKey`），前端只透传；
 * 但离线演示（内置 mock，不起后端）没有源表，需要给种子数据一个稳定 key，
 * 否则「关注」在默认演示模式下完全不可用。
 *
 * 规则必须**确定**：同一名称每次得到同一 key（用哈希而非自增下标，
 * 否则数组顺序一变，已关注的 key 就指向别的源了）。
 */
export function mockSourceKey(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return `src_${h.toString(36)}`;
}
