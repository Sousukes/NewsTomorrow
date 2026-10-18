/**
 * 评论排序方式**单一来源**（与后端 `app/routers/comments.py` 的 `COMMENT_SORTS` 对齐）。
 *
 * 语义：
 * - `latest`：按 `created_at` 倒序（默认，讨论区"新鲜度"优先）
 * - `hot`：按 `likes` 倒序，`created_at` + `id` 兜底（让有价值的讨论浮上来）
 *
 * 取值必须与后端一致，否则请求会被 422/42201 拒绝。契约检查脚本会打真实接口校验。
 * 回复**不参与**排序切换：讨论先后是回复唯一合理的顺序，按赞排会把对话打散。
 */
export const COMMENT_SORTS = [
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '最热' },
] as const;

/** 排序 key 联合类型（由 COMMENT_SORTS 推导） */
export type CommentSort = (typeof COMMENT_SORTS)[number]['key'];

/** 默认排序：与后端缺省值一致 */
export const DEFAULT_COMMENT_SORT: CommentSort = 'latest';
