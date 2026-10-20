/**
 * 举报分类单一来源，须与后端 REPORT_REASONS 逐字一致（后端白名单校验，不符回 42206）。
 * 用预设分类而非自由输入：自由文本无法按类目聚合，复核队列就没法用。
 */
export const REPORT_REASONS = [
  '色情低俗',
  '政治敏感',
  '广告营销',
  '侵权',
  '谣言不实',
  '其他',
] as const;

/** 由 REPORT_REASONS 推导 */
export type ReportReason = (typeof REPORT_REASONS)[number];

/** 后端用 targetType 区分两类主键，targetId 必须原样透传（自行拼接会 404）。 */
export type ReportTargetType = 'comment' | 'news';

/** 类型守卫 */
export function isReportReason(value: unknown): value is ReportReason {
  return typeof value === 'string' && (REPORT_REASONS as readonly string[]).includes(value);
}
