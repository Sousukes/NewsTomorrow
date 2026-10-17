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

import { setToken } from '@/api/http';
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
    setToken('test-token'); // 已读要记在账号维度，游客不回写（见下一条用例）
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    vi.mocked(msgApi.markMessageRead).mockResolvedValue(undefined);
    const s = useMessagesStore();
    await s.fetch();

    await s.markRead('m1');
    expect(s.list.find((m) => m.id === 'm1')?.read).toBe(true);
    expect(s.unreadCount).toBe(1);
    expect(msgApi.markMessageRead).toHaveBeenCalledWith('m1');
  });

  it('游客 markRead 只落本地，不发起请求', async () => {
    // 后端标记已读要求登录；游客发起只会拿到 401，进而触发全局跳登录页
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    const s = useMessagesStore();
    await s.fetch();

    await s.markRead('m1');
    expect(s.list.find((m) => m.id === 'm1')?.read).toBe(true);
    expect(msgApi.markMessageRead).not.toHaveBeenCalled();
  });

  it('重复 markRead 幂等（不重复调接口）', async () => {
    setToken('test-token');
    vi.mocked(msgApi.fetchMessages).mockResolvedValue(mockMessages());
    vi.mocked(msgApi.markMessageRead).mockResolvedValue(undefined);
    const s = useMessagesStore();
    await s.fetch();
    await s.markRead('m1');
    await s.markRead('m1');
    expect(msgApi.markMessageRead).toHaveBeenCalledTimes(1);
  });

  it('markAllRead 全部置为已读', async () => {
    setToken('test-token');
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
  const R = 'recommend' as const;
  const L = 'latest' as const;

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
    await s.fetchNews(R, L);
    const b = s.bucketOf(R, L);
    expect(b.list).toHaveLength(20);
    expect(b.hasMore).toBe(true);
    expect(b.page).toBe(1);
    expect(b.cursor).toBe('cursor-1');
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
    await s.fetchNews(R, L);
    const added = await s.loadMore(R, L);
    const b = s.bucketOf(R, L);
    expect(added).toBe(20);
    expect(b.list).toHaveLength(40);
    expect(b.page).toBe(2);
    expect(b.cursor).toBe('cursor-2');
    // 第二次请求应带上游标
    expect(vi.mocked(newsApi.fetchNewsPage).mock.calls[1]?.[0]?.cursor).toBe('cursor-1');
  });

  it('返回不足一页 → hasMore=false（末页）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockResolvedValueOnce(pageOf([newsItem('z1', 0)], false));
    const s = useNewsStore();
    await s.fetchNews(R, L);
    await s.loadMore(R, L);
    expect(s.bucketOf(R, L).hasMore).toBe(false);
  });

  it('空结果 → hasMore=false', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockResolvedValueOnce(pageOf([], true));
    const s = useNewsStore();
    await s.fetchNews(R, L);
    const added = await s.loadMore(R, L);
    expect(added).toBe(0);
    expect(s.bucketOf(R, L).hasMore).toBe(false);
  });

  it('按 id 去重，不重复追加', async () => {
    const dup = Array.from({ length: 20 }, (_, i) => newsItem(`same${i}`, i));
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf(dup, true));
    const s = useNewsStore();
    await s.fetchNews(R, L);
    const added = await s.loadMore(R, L);
    expect(added).toBe(0);
    expect(s.bucketOf(R, L).list).toHaveLength(20);
  });

  it('hasMore=false 时不再请求', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValue(pageOf([newsItem('only', 0)], false));
    const s = useNewsStore();
    await s.fetchNews(R, L);
    expect(s.bucketOf(R, L).hasMore).toBe(false);
    const callsBefore = vi.mocked(newsApi.fetchNewsPage).mock.calls.length;
    await s.loadMore(R, L);
    expect(vi.mocked(newsApi.fetchNewsPage).mock.calls.length).toBe(callsBefore);
  });

  it('并发 loadMore 只生效一次（防重复请求）', async () => {
    let resolveFn: (v: ReturnType<typeof pageOf>) => void = () => {};
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockImplementationOnce(() => new Promise((r) => (resolveFn = r)));
    const s = useNewsStore();
    await s.fetchNews(R, L);

    const p1 = s.loadMore(R, L);
    const p2 = s.loadMore(R, L); // 并发第二次应被拒
    resolveFn(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`b${i}`, i))));
    await Promise.all([p1, p2]);
    expect(s.bucketOf(R, L).list).toHaveLength(40);
  });

  it('loadMore 失败时记录 error 并停止翻页', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i))))
      .mockRejectedValueOnce(new Error('加载失败'));
    const s = useNewsStore();
    await s.fetchNews(R, L);
    const added = await s.loadMore(R, L);
    const b = s.bucketOf(R, L);
    expect(added).toBe(0);
    expect(b.error).toBe('加载失败');
    expect(b.hasMore).toBe(false);
  });
});

describe('stores/news 分桶隔离（A4）', () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('不同频道的列表互不覆盖（首页与视频页不再串页）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf([newsItem('home-1', 1)], false))
      .mockResolvedValueOnce(pageOf([newsItem('video-1', 1)], false));

    const s = useNewsStore();
    await s.fetchNews('recommend', 'latest');
    await s.fetchNews('video', 'latest');

    // 视频页的请求不应影响首页的列表
    expect(s.listOf('recommend', 'latest').map((i) => i.id)).toEqual(['home-1']);
    expect(s.listOf('video', 'latest').map((i) => i.id)).toEqual(['video-1']);
  });

  it('同一频道不同排序也各自成桶（排序键变了游标就失效）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf([newsItem('hot-1', 1)], false))
      .mockResolvedValueOnce(pageOf([newsItem('latest-1', 1)], false));

    const s = useNewsStore();
    await s.fetchNews('recommend', 'hot');
    await s.fetchNews('recommend', 'latest');

    expect(s.listOf('recommend', 'hot').map((i) => i.id)).toEqual(['hot-1']);
    expect(s.listOf('recommend', 'latest').map((i) => i.id)).toEqual(['latest-1']);
  });

  it('getById 可命中任意桶（收藏/历史渲染依赖）', async () => {
    vi.mocked(newsApi.fetchNewsPage).mockResolvedValueOnce(pageOf([newsItem('v-9', 9)], false));
    const s = useNewsStore();
    await s.fetchNews('video', 'latest');
    expect(s.getById('v-9')?.id).toBe('v-9');
  });
});

describe('stores/news 竞态守卫（A4）', () => {
  const R = 'recommend' as const;
  const L = 'latest' as const;

  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it('loadMore 在途时重新 fetch，旧响应被丢弃（不追加、不覆盖游标）', async () => {
    let resolveOld: (v: ReturnType<typeof pageOf>) => void = () => {};
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(
        pageOf(
          Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i)),
          true,
          'c1',
        ),
      )
      .mockImplementationOnce(() => new Promise((r) => (resolveOld = r)))
      .mockResolvedValueOnce(pageOf([newsItem('fresh-1', 1)], false, null));

    const s = useNewsStore();
    await s.fetchNews(R, L);
    const pending = s.loadMore(R, L); // 在途
    await s.fetchNews(R, L); // 同一桶重新拉第一页 → 代次 +1

    resolveOld(
      pageOf(
        Array.from({ length: 20 }, (_, i) => newsItem(`stale${i}`, i)),
        true,
        'c-old',
      ),
    );
    await pending;

    const b = s.bucketOf(R, L);
    expect(b.list.map((i) => i.id)).toEqual(['fresh-1']); // 未被旧页追加
    expect(b.cursor).toBeNull(); // 未被旧游标覆盖
    expect(b.hasMore).toBe(false);
    expect(b.loadingMore).toBe(false); // 未被旧请求卡住
  });

  it('先发的 fetch 后返回时不覆盖后发的结果', async () => {
    let resolveFirst: (v: ReturnType<typeof pageOf>) => void = () => {};
    vi.mocked(newsApi.fetchNewsPage)
      .mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)))
      .mockResolvedValueOnce(pageOf([newsItem('second', 1)], false));

    const s = useNewsStore();
    const p1 = s.fetchNews(R, L);
    await s.fetchNews(R, L); // 后发先至
    resolveFirst(pageOf([newsItem('first', 1)], false));
    await p1;

    expect(s.listOf(R, L).map((i) => i.id)).toEqual(['second']);
  });

  it('resetPaging 使在途 loadMore 失效', async () => {
    let resolveOld: (v: ReturnType<typeof pageOf>) => void = () => {};
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(
        pageOf(
          Array.from({ length: 20 }, (_, i) => newsItem(`a${i}`, i)),
          true,
          'c1',
        ),
      )
      .mockImplementationOnce(() => new Promise((r) => (resolveOld = r)));

    const s = useNewsStore();
    await s.fetchNews(R, L);
    const pending = s.loadMore(R, L);
    s.resetPaging(R, L);
    resolveOld(
      pageOf(
        Array.from({ length: 20 }, (_, i) => newsItem(`stale${i}`, i)),
        true,
        'c-old',
      ),
    );
    await pending;

    const b = s.bucketOf(R, L);
    expect(b.list).toHaveLength(20); // 未被追加
    expect(b.cursor).toBeNull(); // reset 后的状态保持
    expect(b.page).toBe(0);
  });

  it('loadMore 续接沿用桶的排序（换排序会让服务端游标失效）', async () => {
    vi.mocked(newsApi.fetchNewsPage)
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`p1-${i}`, i))))
      .mockResolvedValueOnce(pageOf(Array.from({ length: 20 }, (_, i) => newsItem(`p2-${i}`, i))));

    const s = useNewsStore();
    await s.fetchNews(R, 'for-you');
    await s.loadMore(R, 'for-you');

    const second = vi.mocked(newsApi.fetchNewsPage).mock.calls[1][0];
    expect(second).toMatchObject({ sort: 'for-you' });
  });
});
