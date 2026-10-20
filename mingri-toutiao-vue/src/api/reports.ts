import { isReportReason, type ReportReason, type ReportTargetType } from '@/constants/reports';
import { USE_MOCK } from './config';
import { mockDelay, request } from './http';

/** autoHidden 不下发到 UI：回显等于把自动处置阈值告诉刷子。 */
export interface ReportResult {
  status: 'pending' | 'resolved' | 'dismissed';
  autoHidden: boolean;
}

/** 提交举报（需登录）。幂等由服务端保证，前端不做本地「已举报」状态。 */
export async function submitReport(
  targetType: ReportTargetType,
  targetId: string,
  reason: ReportReason,
): Promise<ReportResult> {
  if (USE_MOCK) {
    // mock 只保证**契约形状**：鉴权、目标存在性、理由白名单都按后端同口径校验，
    // 但阈值自动处置不模拟（需要 3 个不同账号，mock 没有真实账号体系，
    // 假装会隐藏反而让前端误以为链路已验）。
    if (!targetId) throw new Error('举报对象不存在或已删除');
    if (!isReportReason(reason)) throw new Error('举报理由不合法');
    return mockDelay({ status: 'pending', autoHidden: false }, 200);
  }

  const data = await request<{ status?: unknown; autoHidden?: unknown }>('/reports', {
    method: 'POST',
    body: { targetType, targetId, reason },
  });
  return {
    // 未知状态一律按 pending 处理：它不是"已处置"，不该被 UI 当作结果展示
    status:
      data?.status === 'resolved' || data?.status === 'dismissed'
        ? data.status
        : ('pending' as const),
    autoHidden: Boolean(data?.autoHidden),
  };
}
