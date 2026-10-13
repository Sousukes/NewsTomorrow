import { USE_MOCK } from './config';
import { mockDelay, request } from './http';
import { VALIDATE_RESPONSES, validateMessage, warnIssues } from './validate';
import type { Message } from '@/types';

function normalize(raw: Record<string, unknown>): Message {
  const r = raw as Record<string, unknown>;
  const item: Message = {
    id: String(r.id ?? ''),
    type: (r.type ?? 'system') as Message['type'],
    title: String(r.title ?? ''),
    summary: String(r.summary ?? ''),
    newsId: (r.newsId ?? undefined) as string | undefined,
    time: String(r.time ?? ''),
    read: Boolean(r.read ?? false),
  };
  if (VALIDATE_RESPONSES) {
    const issues = validateMessage(item);
    if (issues.length) warnIssues('message', issues);
  }
  return item;
}

/** 离线兜底的示例消息（与后端 MESSAGES 种子保持一致的语义） */
const MOCK_MESSAGES: Message[] = [
  {
    id: 'm1',
    type: 'system',
    title: '欢迎使用明日头条',
    summary: '关注你感兴趣的频道，第一时间获取重要资讯推送。',
    time: '刚刚',
    read: false,
  },
  {
    id: 'm2',
    type: 'comment',
    title: '有人评论了你关注的资讯',
    summary: '观察者小张：国产航母编队训练报道角度很扎实。',
    newsId: 'n4',
    time: '1小时前',
    read: false,
  },
  {
    id: 'm3',
    type: 'recommend',
    title: '今日热点速览',
    summary: '暴雨蓝色预警、楼市新政、国产大飞机新航线。',
    newsId: 'n3',
    time: '2小时前',
    read: false,
  },
  {
    id: 'm4',
    type: 'system',
    title: '账号安全提醒',
    summary: '建议定期检查登录设备，异常登录将及时通知你。',
    time: '1天前',
    read: true,
  },
];

/** 消息一页（后端已改为游标分页，M2） */
export interface MessagePage {
  list: Message[];
  hasMore: boolean;
  nextCursor: string | null;
}

/** GET /messages —— 消息列表（带 token 时 read 标记准确） */
export async function fetchMessagesPage(cursor?: string, limit = 30): Promise<MessagePage> {
  if (USE_MOCK) {
    return mockDelay({
      list: MOCK_MESSAGES.map((m) => ({ ...m })),
      hasMore: false,
      nextCursor: null,
    });
  }
  const data = await request<{
    list?: unknown[];
    hasMore?: boolean;
    nextCursor?: string | null;
  }>('/messages', { query: { cursor, limit } });
  const arr = Array.isArray(data) ? data : (data?.list ?? []);
  return {
    list: (arr as Record<string, unknown>[]).map(normalize),
    hasMore: Boolean(data?.hasMore),
    nextCursor: data?.nextCursor ?? null,
  };
}

/** 便捷封装：只取列表 */
export async function fetchMessages(): Promise<Message[]> {
  return (await fetchMessagesPage()).list;
}

/** GET /messages/unread-count —— 未读数 */
export async function fetchUnreadCount(): Promise<number> {
  if (USE_MOCK) return mockDelay(MOCK_MESSAGES.filter((m) => !m.read).length);
  const data = await request<unknown>('/messages/unread-count');
  return Number(data ?? 0);
}

/** PATCH /messages/:id —— 标记已读 */
export async function markMessageRead(id: string): Promise<void> {
  if (USE_MOCK) return mockDelay(undefined, 80);
  await request(`/messages/${id}`, { method: 'PATCH' });
}

/** POST /messages/read-all —— 全部已读 */
export async function markAllMessagesRead(): Promise<void> {
  if (USE_MOCK) return mockDelay(undefined, 80);
  await request('/messages/read-all', { method: 'POST' });
}
