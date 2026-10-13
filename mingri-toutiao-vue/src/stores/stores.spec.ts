import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

vi.mock('@/api/messages', () => ({
  fetchMessages: vi.fn(),
  fetchMessagesPage: vi.fn(),
  fetchUnreadCount: vi.fn(),
  markMessageRead: vi.fn(),
  markAllMessagesRead: vi.fn(),
}));

vi.mock('@/api/news', () => ({
  fetchNews: vi.fn(),
  fetchNewsPage: vi.fn(),
  fetchNewsById: vi.fn(),
  searchNews: vi.fn(),
}));

import { useMessagesStore } from '@/stores/messages';
import { useNewsStore } from '@/stores/news';
import * as msgApi from '@/api/messages';
import * as newsApi from '@/api/news';
import type { Message, NewsItem } from '@/types';

/** 每次返回新对象，避免 store 内的就地修改污染测试数据 */
const mockMessages = (): Message[] => [
  { id: 'm1', type: 'system', title: 'A', summary: 's1', time: '刚刚', read: false },
  { id: 'm2', type: 'comment', title: 'B', summary: 's2', time: '1小时前', read: false },
];

const newsItem = (id: string, n: number): NewsItem => ({
  id,
  title: `标题${id}`,
  source: '源',
  time: '1小时前',
  channel: 'recommend',
  variant: 'text',
  commentCount: n,
});

/** 构造一页（游标分页契约） */
const pageOf = (list: NewsItem[], hasMore = true, nextCursor: string | null = null) => ({
  list,
  total: null as number | null,
  page: 1 as number | null,
  pageSize: 20,
  nextCursor: hasMore ? (nextCursor ?? 'cursor-1') : null,
  hasMore,
});

describe('stores/messages', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('fetch 拉取列表并计算未读数', async () => {
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    const s = useMessagesStore();
    await s.fetch();
    expect(s.list).toHaveLength(2);
    expect(s.unreadCount).toBe(2);
  });

  it('markRead 本地立即生效并回写服务端', async () => {
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    vi.mocked(msgApi.markMessageRead).mockResolvedValue(undefined);
    const s = useMessagesStore();
    await s.fetch();

    await s.markRead('m1');
    expect(s.list.find((m) => m.id === 'm1')?.read).toBe(true);
    expect(s.unreadCount).toBe(1);
    expect(msgApi.markMessageRead).toHaveBeenCalledWith('m1');
  });

  it('重复 markRead 幂等（不重复调接口）', async () => {
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    vi.mocked(msgApi.markMessageRead).mockResolvedValue(undefined);
    const s = useMessagesStore();
    await s.fetch();
    await s.markRead('m1');
    await s.markRead('m1');
    expect(msgApi.markMessageRead).toHaveBeenCalledTimes(1);
  });

  it('markAllRead 全部置为已读', async () => {
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    vi.mocked(msgApi.markAllMessagesRead).mockResolvedValue(undefined);
    const s = useMessagesStore();
    await s.fetch();
    await s.markAllRead();
    expect(s.unreadCount).toBe(0);
    expect(msgApi.markAllMessagesRead).toHaveBeenCalled();
  });

  it('本地已读缓存优先于服务端（未登录时 read 恒 false 也不回退）', async () => {
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    const s = useMessagesStore();
    await s.markRead('m1'); // 先本地标记
    await s.fetch(); // 再拉取（服务端仍返回未读）
    expect(s.list.find((m) => m.id === 'm1')?.read).toBe(true);
  });

  it('接口失败时记录 error 且不崩溃', async () => {
    vi.mocked(msgApi.fetchMessages).mockRejectedValue(new Error('网络错误'));
    const s = useMessagesStore();
    await s.fetch();
    expect(s.error).toBe('网络错误');
    expect(s.list).toHaveLength(0);
  });
});

describe('stores/news 无限滚动（游标分页）', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('fetchNews 装满首页时 hasMore=true', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(
      pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`p0-${i}`, i))),
    );
    const s = useNewsStore();
    await s.fetchNews('recommend');
    expect(s.list).toHaveLength(20);
    expect(s.hasMore).toBe(true);
    expect(s.page).toBe(1);
    expect(s.cursor).toBe('cursor-1');
  });

  it('loadMore 用游标续接并追加下一页', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockResolvedValueOnce(
        pageOf(
          Array.from({ length: 20 }, (_, i) => newsItem(`b${i}`, i)),
          true,
          'cursor-2',
        ),
      );
    const s = useNewsStore();
    await s.fetchNews('recommend');
    const added = await s.loadMore();
    expect(added).toBe(20);
    expect(s.list).toHaveLength(40);
    expect(s.page).toBe(2);
    expect(s.cursor).toBe('cursor-2');
    // 第二次请求应带上游标
    expect(vi.mocked(newsApi.fetchNewsPage).mock.calls[1]?.[0]?.cursor).toBe('cursor-1');
  });

  it('返回不足一页 → hasMore=false（末页）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockResolvedValueOnce(pageOf([newsItem('z1', 0)], false));
    const s = useNewsStore();
    await s.fetchNews('recommend');
    await s.loadMore();
    expect(s.hasMore).toBe(false);
  });

  it('空结果 → hasMore=false', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockResolvedValueOnce(pageOf([], true));
    const s = useNewsStore();
    await s.fetchNews('recommend');
    const added = await s.loadMore();
    expect(added).toBe(0);
    expect(s.hasMore).toBe(false);
  });

  it('按 id 去重，不重复追加', async () => {
    const dup = Array.from({ length: 20 }, (_, i) => newsItem(`same${i}`, i));
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf(dup, true));
    const s = useNewsStore();
    await s.fetchNews('recommend');
    const added = await s.loadMore();
    expect(added).toBe(0);
    expect(s.list).toHaveLength(20);
  });

  it('hasMore=false 时不再请求', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf([newsItem('only', 0)], false));
    const s = useNewsStore();
    await s.fetchNews('recommend');
    expect(s.hasMore).toBe(false);
    const callsBefore = vi.mocked(newsApi.fetchNewsPage).mock.calls.length;
    await s.loadMore();
    expect(vi.mocked(newsApi.fetchNewsPage).mock.calls.length).toBe(callsBefore);
  });

  it('并发 loadMore 只生效一次（防重复请求）', async () => {
    let resolveFn: (v: ReturnType<typeof pageOf>) => void = () => {};
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockImplementationOnce(() => new Promise((r) => (resolveFn = r)));
    const s = useNewsStore();
    await s.fetchNews('recommend');

    const p1 = s.loadMore();
    const p2 = s.loadMore(); // 并发第二次应被拒
    resolveFn(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`b${i}`, i))));
    await Promise.all([p1, p2]);
    expect(s.list).toHaveLength(40);
  });

  it('loadMore 失败时记录 error 并停止翻页', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockRejectedValueOnce(new Error('加载失败'));
    const s = useNewsStore();
    await s.fetchNews('recommend');
    const added = await s.loadMore();
    expect(added).toBe(0);
    expect(s.error).toBe('加载失败');
    expect(s.hasMore).toBe(false);
  });
});

describe('stores/news 排序', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('fetchNews 记录当前排序', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf([newsItem('a', 1)], false));
    const s = useNewsStore();
    await s.fetchNews('recommend', 'hot');
    expect(s.currentSort).toBe('hot');
    expect(vi.mocked(newsApi.fetchNewsPage).mock.calls[0][0]).toMatchObject({ sort: 'hot' });
  });

  it('loadMore 沿用首请求的排序（换排序会让服务端游标失效）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`p1-${i}`, i))))
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`p2-${i}`, i))));

    const s = useNewsStore();
    await s.fetchNews('recommend', 'for-you');
    await s.loadMore();

    const second = vi.mocked(newsApi.fetchNewsPage).mock.calls[1][0];
    // 续接必须与首请求同一排序，否则 keyset 比较基准不一致 → 重复或漏项
    expect(second).toMatchObject({ sort: 'for-you' });
  });

  it('切换频道保持当前排序（不应把用户选的排序重置）', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf([newsItem('b', 1)], false));
    const s = useNewsStore();
    await s.fetchNews('recommend', 'hot');
    await s.fetchNews('tech', s.currentSort);
    expect(s.currentSort).toBe('hot');
    expect(s.currentChannel).toBe('tech');
  });
});
