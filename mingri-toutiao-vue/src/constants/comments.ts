/** 评论排序单一来源，取值须与后端 COMMENT_SORTS 一致（不一致会被 42201 拒绝）。 */
export const COMMENT_SORTS = [
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '最热' },
] as const;

/** 由 COMMENT_SORTS 推导的联合类型 */
export type CommentSort = (typeof COMMENT_SORTS)[number]['key'];

/** 与后端缺省值一致 */
export const DEFAULT_COMMENT_SORT: CommentSort = 'latest';
