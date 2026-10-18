import { isReportReason, type ReportReason, type ReportTargetType } from '@/constants/reports';
import { USE_MOCK } from './config';
import { mockDelay, request } from './http';

/**
 * 举报结果。
 *
 * `autoHidden` 表示**本次提交是否触发了阈值自动处置**（内容已被软隐藏/下架）。
 * 前端刻意**不向举报人展示**这个字段：一旦回显「已处理」，等于把阈值告诉了刷子，
 * 也给了被举报人反向锁定举报人的线索（设计文档 §六）。
 * 保留在返回值里是为了日志与将来的举报进度查询，不是给当前 UI 用的。
 */
export interface ReportResult {
  status: 'pending' | 'resolved' | 'dismissed';
  autoHidden: boolean;
}

/**
 * POST /api/reports —— 提交举报（**需登录**）。
 *
 * 幂等语义由服务端保证：同一用户对同一目标重复举报只更新理由、不新增行。
 * 因此前端**不需要**本地去重或「已举报过」状态——重复点只是换个理由，
 * 反而符合「我改主意了」的真实意图。
 */
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
