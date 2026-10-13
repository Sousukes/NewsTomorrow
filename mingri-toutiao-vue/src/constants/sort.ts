/**
 * 资讯排序方式**单一来源**（与后端 `app/ranking.py` 的 `SORT_MODES` 对齐）。
 *
 * 三模式的语义差异：
 * - `latest`：按发布时间倒序（原有行为，默认）
 * - `hot`：按热度分倒序（互动加权 × 时间衰减）
 * - `for-you`：热度 + 频道偏好加分；未登录或无行为数据时等价 `hot`
 *
 * 取值必须与后端一致，否则请求会被 422 拒绝。契约检查脚本会校验二者不漂移。
 */

export const SORT_MODES = [
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '热榜' },
  { key: 'for-you', label: '为你推荐' },
] as const;

/** 排序 key 联合类型（由 SORT_MODES 推导） */
export type SortMode = (typeof SORT_MODES)[number]['key'];

/** 默认排序：与后端 `sort` 缺省值一致 */
export const DEFAULT_SORT: SortMode = 'latest';

/** 需要登录才有意义的模式（未登录会自动退化为 hot，但 UI 上提示更友好） */
export const PERSONALIZED_SORT: SortMode = 'for-you';

const SORT_KEYS: SortMode[] = SORT_MODES.map((s) => s.key);

/** 类型守卫：任意字符串是否为合法排序方式 */
export function isSortMode(value: unknown): value is SortMode {
  return typeof value === 'string' && (SORT_KEYS as string[]).includes(value);
}

/** 归一化：非法/未知值回退到默认排序（URL 参数不可信时使用） */
export function toSort(value: unknown): SortMode {
  return isSortMode(value) ? value : DEFAULT_SORT;
}
