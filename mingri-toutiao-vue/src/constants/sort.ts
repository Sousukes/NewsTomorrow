/** 资讯排序单一来源，取值须与后端 SORT_MODES 一致（不一致会被 422 拒绝）。 */

export const SORT_MODES = [
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '热榜' },
  { key: 'for-you', label: '为你推荐' },
] as const;

/** 由 SORT_MODES 推导的联合类型 */
export type SortMode = (typeof SORT_MODES)[number]['key'];

/** 与后端缺省值一致 */
export const DEFAULT_SORT: SortMode = 'latest';

/** 需要行为数据的模式；未登录时等价 hot */
export const PERSONALIZED_SORT: SortMode = 'for-you';

const SORT_KEYS: SortMode[] = SORT_MODES.map((s) => s.key);

/** 类型守卫 */
export function isSortMode(value: unknown): value is SortMode {
  return typeof value === 'string' && (SORT_KEYS as string[]).includes(value);
}

/** 非法值回退默认（URL 参数不可信） */
export function toSort(value: unknown): SortMode {
  return isSortMode(value) ? value : DEFAULT_SORT;
}
