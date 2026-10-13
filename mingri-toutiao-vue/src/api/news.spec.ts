import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 关闭 mock，走真实请求分支（测试环境默认 USE_MOCK=true）
vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

import { fetchHotTerms, fetchNewsPage, fetchRelated } from '@/api/news';

/** 构造统一契约响应 { code, message, data } */
function respond(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({ code: 0, message: 'ok', data }),
  } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      respond({
        list: [
          {
            id: 'r1',
            title: '相关一',
            summary: '摘要',
            source: '源',
            commentCount: 3,
            time: '1小时前',
            channel: 'tech',
            variant: 'text',
            isTop: false,
          },
        ],
        total: 1,
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('api/news.fetchRelated', () => {
  it('请求 /news/:id/related 并带上 limit', async () => {
    const list = await fetchRelated('n1', 3);
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('/news/n1/related');
    expect(url).toContain('limit=3');
    expect(list).toHaveLength(1);
  });

  it('归一化后端字段为 NewsItem（camelCase）', async () => {
    const [item] = await fetchRelated('n1');
    expect(item).toMatchObject({
      id: 'r1',
      title: '相关一',
      commentCount: 3,
      channel: 'tech',
      variant: 'text',
      isTop: false,
    });
  });

  it('limit 缺省为 6', async () => {
    await fetchRelated('n1');
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain('limit=6');
  });

  it('接口失败时静默降级为空数组（不影响正文阅读）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('network down');
      }),
    );
    await expect(fetchRelated('n1')).resolves.toEqual([]);
  });

  it('空列表也正常返回空数组', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => respond({ list: [], total: 0 })),
    );
    await expect(fetchRelated('n1')).resolves.toEqual([]);
  });
});

describe('fetchNewsPage 排序参数', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        respond({
          list: [],
          total: 0,
          page: 1,
          pageSize: 20,
          nextCursor: null,
          hasMore: false,
          sort: 'hot',
        }),
      ),
    );
  });

  it('把 sort 透传到查询串', async () => {
    await fetchNewsPage({ sort: 'hot' });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain('sort=hot');
  });

  it('缺省使用 latest（不给后端传非法值）', async () => {
    await fetchNewsPage({});
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain('sort=latest');
  });

  it('非法 sort 被归一化为 latest', async () => {
    await fetchNewsPage({ sort: 'bogus' as never });
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain('sort=latest');
    expect(url).not.toContain('bogus');
  });
});

describe('fetchHotTerms', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        respond({
          list: [
            { term: '新能源', count: 12 },
            { term: '楼市', count: 5 },
          ],
          days: 7,
        }),
      ),
    );
  });

  it('请求路径与字段归一化', async () => {
    const terms = await fetchHotTerms(10);
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain('/news/search/hot');
    expect(terms).toEqual([
      { term: '新能源', count: 12 },
      { term: '楼市', count: 5 },
    ]);
  });

  it('接口失败静默降级为空数组（热搜是引导，不该让搜索页报错）', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('500');
      }),
    );
    await expect(fetchHotTerms()).resolves.toEqual([]);
  });
});
