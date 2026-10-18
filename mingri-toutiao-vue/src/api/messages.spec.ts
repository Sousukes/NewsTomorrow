import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 消息 API 层（`src/api/messages.ts`）——此前 0% 覆盖。
 *
 * 与评论层同样的三条契约（裸数组 / 缺失字段归一 / normalize），
 * 外加两个**容易静默出错**的点：
 * - `fetchUnreadCount` 的 `Number(data ?? 0)`：后端返回 null/字符串时不能变 NaN
 *   （角标显示 "NaN" 比不显示更糟）；
 * - `markAllMessagesRead` 必须是 POST `/messages/read-all`——路径写错会 404，
 *   而用户只会看到"全部已读点了没反应"。
 */

vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

const request = vi.fn();
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  mockDelay: async (v: unknown) => v,
}));

import {
  fetchMessages,
  fetchMessagesPage,
  fetchUnreadCount,
  markAllMessagesRead,
  markMessageRead,
} from '@/api/messages';

const raw = {
  id: 'm1',
  type: 'system',
  title: '标题',
  summary: '摘要',
  time: '刚刚',
  read: false,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('fetchMessagesPage', () => {
  it('兼容裸数组响应', async () => {
    request.mockResolvedValueOnce([raw]);
    const page = await fetchMessagesPage();
    expect(page.list).toHaveLength(1);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });

  it('兼容 {list} 并透传游标', async () => {
    request.mockResolvedValueOnce({ list: [raw], hasMore: true, nextCursor: 'm2' });
    const page = await fetchMessagesPage('cur', 10);
    const [path, opts] = request.mock.calls[0] as [string, { query: Record<string, unknown> }];
    expect(path).toBe('/messages');
    expect(opts.query).toEqual({ cursor: 'cur', limit: 10 });
    expect(page.nextCursor).toBe('m2');
    expect(page.hasMore).toBe(true);
  });

  it('缺字段归一：不会留 undefined 让翻页逻辑失真', async () => {
    request.mockResolvedValueOnce({});
    const page = await fetchMessagesPage();
    expect(page.list).toEqual([]);
    expect(page.hasMore).toBe(false);
    expect(page.nextCursor).toBeNull();
  });
});

describe('fetchMessages', () => {
  it('只取列表', async () => {
    request.mockResolvedValueOnce({ list: [raw] });
    const list = await fetchMessages();
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe('标题');
  });
});

describe('fetchUnreadCount', () => {
  it('正常数字原样返回', async () => {
    request.mockResolvedValueOnce(3);
    await expect(fetchUnreadCount()).resolves.toBe(3);
  });

  it('返回 null 时归 0（否则角标显示 NaN，比不显示更糟）', async () => {
    request.mockResolvedValueOnce(null);
    const n = await fetchUnreadCount();
    expect(n).toBe(0);
    expect(Number.isNaN(n)).toBe(false);
  });

  it('返回字符串数字时也转成数字', async () => {
    request.mockResolvedValueOnce('7');
    await expect(fetchUnreadCount()).resolves.toBe(7);
  });

  it('返回对象（异常形状）不会变成 NaN 显示', async () => {
    request.mockResolvedValueOnce({ count: 2 });
    const n = await fetchUnreadCount();
    expect(Number.isNaN(n)).toBe(false);
  });
});

describe('markMessageRead / markAllMessagesRead', () => {
  it('单条已读走 PATCH 且带上 id', async () => {
    request.mockResolvedValueOnce(undefined);
    await markMessageRead('m1');
    const [path, opts] = request.mock.calls[0] as [string, { method: string }];
    expect(path).toBe('/messages/m1');
    expect(opts.method).toBe('PATCH');
  });

  it('全部已读走 POST /messages/read-all（路径写错会 404，用户只看到"点了没反应"）', async () => {
    request.mockResolvedValueOnce(undefined);
    await markAllMessagesRead();
    const [path, opts] = request.mock.calls[0] as [string, { method: string }];
    expect(path).toBe('/messages/read-all');
    expect(opts.method).toBe('POST');
  });
});
