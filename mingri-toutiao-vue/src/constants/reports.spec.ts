import { describe, expect, it } from 'vitest';
import { REPORT_REASONS, isReportReason } from './reports';

/**
 * 举报分类白名单（E5）。
 *
 * 后端的 `REPORT_REASONS` 是权威来源，这里钉住的是**前端不擅自扩缩**：
 * 列表一旦与后端漂移，用户选中的理由会被 422/42206 拒绝，且只有点了举报的人才会遇到。
 * 逐字比对放在契约脚本（它会拿真实值打接口）；本文件守住"前端改动要有意识"。
 */
describe('constants/reports 举报分类', () => {
  it('分类文案与后端一致（顺序即弹窗展示顺序）', () => {
    expect([...REPORT_REASONS]).toEqual([
      '色情低俗',
      '政治敏感',
      '广告营销',
      '侵权',
      '谣言不实',
      '其他',
    ]);
  });

  it('类型守卫只认白名单内的字符串', () => {
    expect(isReportReason('广告营销')).toBe(true);
    expect(isReportReason('其他')).toBe(true);
  });

  it('拒绝白名单外/非字符串值（否则会被后端 42206 拒绝）', () => {
    expect(isReportReason('不存在的理由')).toBe(false);
    expect(isReportReason('')).toBe(false);
    expect(isReportReason(null)).toBe(false);
    expect(isReportReason(1)).toBe(false);
  });
});
