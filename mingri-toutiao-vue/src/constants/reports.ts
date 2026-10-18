/**
 * 举报分类**单一来源**（与后端 `app/config.py::REPORT_REASONS` 逐字对齐）。
 *
 * 后端对 `reason` 做**白名单校验**（不在列表内 → 422/42206），
 * 因此这里多一个字、少一个字都会被拒。契约检查脚本会拿列表里的值打一次接口，
 * 漂移会在 CI 阶段暴露，而不是等用户点了举报才发现。
 *
 * 为什么不在前端自由输入理由：自由文本需要人工分类，且会让「同一问题被拆成
 * 几十种说法」，复核队列无法按类目聚合；预设分类是自动处置阈值可用的前提。
 */
export const REPORT_REASONS = [
  '色情低俗',
  '政治敏感',
  '广告营销',
  '侵权',
  '谣言不实',
  '其他',
] as const;

/** 举报理由（由 REPORT_REASONS 推导，改列表即改类型） */
export type ReportReason = (typeof REPORT_REASONS)[number];

/**
 * 举报对象类型：评论 / 资讯。
 *
 * 后端用同一个 `targetId` 列存两类主键，靠 `targetType` 区分；
 * 前端必须原样透传，不能自行拼接（如 `comment:xxx`）——那会 404。
 */
export type ReportTargetType = 'comment' | 'news';

/** 类型守卫：运行时值是否落在白名单内（mock 与防御性分支共用） */
export function isReportReason(value: unknown): value is ReportReason {
  return typeof value === 'string' && (REPORT_REASONS as readonly string[]).includes(value);
}
