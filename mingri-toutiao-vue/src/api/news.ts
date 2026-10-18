import { mockNews } from '@/data/mockNews';
import { DEFAULT_SORT, toSort } from '@/constants/sort';
import type { SortMode } from '@/constants/sort';
import type { Channel, NewsItem } from '@/types';
import { mockDelay, request } from './http';
import { USE_MOCK } from './config';
import { VALIDATE_RESPONSES, validateNewsItem, warnIssues } from './validate';

/** 后端字段 → 前端 NewsItem 归一化（兼容多种常见命名） */
export function normalizeNewsItem(raw: Record<string, unknown>): NewsItem {
  const r = raw as Record<string, unknown>;
  const item: NewsItem = {
    id: String(r.id ?? r.newsId ?? ''),
    title: String(r.title ?? ''),
    summary: (r.summary ?? r.digest ?? undefined) as string | undefined,
    // E1：正文只由详情接口下发；列表响应没有该字段即 undefined
    content: (r.content ?? undefined) as string | undefined,
    source: String(r.source ?? r.mediaName ?? ''),
    commentCount: Number(r.commentCount ?? r.comment_count ?? 0),
    time: String(r.time ?? r.publishTime ?? ''),
    images: (r.images ?? r.imageList) as string[] | undefined,
    cover: (r.cover ?? r.coverUrl) as string | undefined,
    videoUrl: r.videoUrl as string | undefined,
    playCount: r.playCount as number | undefined,
    isTop: Boolean(r.isTop ?? false),
    channel: (r.channel ?? 'recommend') as Channel,
    variant: (r.variant ?? 'left-right') as NewsItem['variant'],
  };
  // 开发期运行时校验：字段漂移时给出告警而非静默吞掉（M8）
  if (VALIDATE_RESPONSES) {
    const issues = validateNewsItem(item);
    if (issues.length) warnIssues('news', issues);
  }
  return item;
}

export interface FetchNewsParams {
  channel?: Channel;
  page?: number;
  pageSize?: number;
  /** 游标分页：传上一页返回的 nextCursor（优先于 page） */
  cursor?: string;
  /** 排序：latest（时间）/ hot（热度）/ for-you（热度 + 频道偏好） */
  sort?: SortMode;
}

/** 一页资讯（游标分页契约，H5） */
export interface NewsPage {
  list: NewsItem[];
  total: number | null;
  page: number | null;
  pageSize: number;
  nextCursor: string | null;
  hasMore: boolean;
}

const emptyPage = (pageSize: number): NewsPage => ({
  list: [],
  total: 0,
  page: 1,
  pageSize,
  nextCursor: null,
  hasMore: false,
});

/** mock 模式最多翻的页数（避免无限滚动永不结束） */
const MOCK_MAX_PAGE = 5;

/** GET /news —— 拉取资讯列表（游标分页） */
export async function fetchNewsPage(params: FetchNewsParams = {}): Promise<NewsPage> {
  const { channel = 'recommend', page = 1, pageSize = 20, cursor, sort = DEFAULT_SORT } = params;

  if (USE_MOCK) {
    // mock 模式模拟服务端分页：种子数据循环铺满，使无限滚动在离线模式下可完整演示。
    let base = channel === 'recommend' ? mockNews : mockNews.filter((i) => i.channel === channel);
    // 离线演示也要体现排序差异：无服务端时评论数是最接近"热度"的可用信号
    if (sort !== 'latest') {
      base = [...base].sort((a, b) => (b.commentCount ?? 0) - (a.commentCount ?? 0));
    }
    if (!base.length) return mockDelay(emptyPage(pageSize));

    const pageNo = cursor ? Number(cursor) || 1 : page;
    const start = (pageNo - 1) * pageSize;
    const out: NewsItem[] = [];
    for (let i = 0; i < pageSize; i++) {
      const idx = (start + i) % base.length;
      const src = base[idx];
      // 跨页循环时给 id 加后缀，保证 id 唯一（否则 store 去重会吃掉后续页）
      const round = Math.floor((start + i) / base.length);
      out.push(round === 0 ? { ...src } : { ...src, id: `${src.id}_p${round}` });
    }
    const hasMore = pageNo < MOCK_MAX_PAGE;
    return mockDelay({
      list: out,
      total: base.length * MOCK_MAX_PAGE,
      page: pageNo,
      pageSize,
      nextCursor: hasMore ? String(pageNo + 1) : null,
      hasMore,
    });
  }

  const data = await request<{
    list?: unknown[];
    total?: number | null;
    page?: number | null;
    pageSize?: number;
    nextCursor?: string | null;
    hasMore?: boolean;
  }>('/news', {
    // B2：**不传 `page`**——服务端据此走 keyset 游标分页，首页不再做全表 count。
    // `page` 仍保留在参数类型里（mock 模式与调试需要），真后端调用一律不传：
    // 一旦传了，服务端会切回 offset 分支并顺手 count 一次。
    query: { channel, pageSize, cursor, sort: toSort(sort) },
  });

  const arr = Array.isArray(data) ? data : (data?.list ?? []);
  const list = (arr as Record<string, unknown>[]).map(normalizeNewsItem);
  return {
    list,
    total: data?.total ?? null,
    page: data?.page ?? null,
    pageSize: data?.pageSize ?? pageSize,
    nextCursor: data?.nextCursor ?? null,
    hasMore: Boolean(data?.hasMore ?? list.length >= pageSize),
  };
}

/** 便捷封装：只取列表（详情页 / 视频页等只关心数据本身） */
export async function fetchNews(params: FetchNewsParams = {}): Promise<NewsItem[]> {
  return (await fetchNewsPage(params)).list;
}

/** GET /news/:id/related —— 相关推荐（服务端按标题相似度/同频道召回） */
/** 热搜词（后端真实统计） */
export interface HotTerm {
  term: string;
  count: number;
}

/**
 * GET /news/search/hot —— 热搜词。
 *
 * **失败静默降级为空数组**：热搜是引导性内容，没有它用户仍可搜索，
 * 不应因此让整个搜索页报错（更不应退回写死的假热词）。
 */
export async function fetchHotTerms(limit = 10): Promise<HotTerm[]> {
  if (USE_MOCK) {
    // 离线演示：用种子资讯的高频词近似，保持界面可演示
    return mockDelay(
      ['国产大飞机', '新能源汽车', '楼市新政', '暴雨预警', '航母训练', '教育课后服务']
        .slice(0, limit)
        .map((term, i) => ({ term, count: 100 - i * 10 })),
    );
  }
  try {
    const data = await request<{ list?: unknown[] }>('/news/search/hot', { query: { limit } });
    const arr = Array.isArray(data) ? data : (data?.list ?? []);
    return (arr as Record<string, unknown>[]).map((r) => ({
      term: String(r.term ?? ''),
      count: Number(r.count ?? 0),
    }));
  } catch {
    return [];
  }
}

export async function fetchRelated(id: string, limit = 6): Promise<NewsItem[]> {
  if (USE_MOCK) {
    // 离线兜底：同频道优先，其次全量
    const base = mockNews.find((i) => i.id === id);
    const pool = mockNews.filter((i) => i.id !== id);
    const sameChannel = base ? pool.filter((i) => i.channel === base.channel) : [];
    const rest = pool.filter((i) => !sameChannel.includes(i));
    return mockDelay([...sameChannel, ...rest].slice(0, limit));
  }
  try {
    const data = await request<{ list?: unknown[] }>(`/news/${id}/related`, {
      query: { limit },
    });
    return ((data?.list ?? []) as Record<string, unknown>[]).map(normalizeNewsItem);
  } catch {
    // 相关推荐是增强信息，失败时静默降级，不影响正文阅读
    return [];
  }
}

/** GET /news/:id —— 拉取单条详情（可选，供深链兜底） */
export async function fetchNewsById(id: string): Promise<NewsItem | null> {
  if (USE_MOCK) {
    const found = mockNews.find((i) => i.id === id) ?? null;
    return mockDelay(found);
  }
  try {
    const data = await request<unknown>(`/news/${id}`);
    return data ? normalizeNewsItem(data as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export interface SearchParams {
  q: string;
  page?: number;
  pageSize?: number;
}

export interface SearchResult {
  list: NewsItem[];
  total: number;
  page: number;
  keyword: string;
  hasMore: boolean;
}

/** GET /news/search?q= —— 服务端搜索（标题/摘要模糊匹配） */
export async function searchNews(params: SearchParams): Promise<SearchResult> {
  const { q, page = 1, pageSize = 20 } = params;
  const keyword = q.trim();

  if (USE_MOCK) {
    // 离线兜底：本地过滤
    const list = keyword
      ? mockNews.filter((i) => i.title.includes(keyword) || (i.summary ?? '').includes(keyword))
      : [];
    return mockDelay({ list, total: list.length, page, keyword, hasMore: false });
  }

  const data = await request<{
    list?: unknown[];
    total?: number;
    page?: number;
    keyword?: string;
    hasMore?: boolean;
  }>('/news/search', { query: { q: keyword, page, pageSize } });
  const arr = Array.isArray(data?.list) ? data.list : [];
  const list = (arr as Record<string, unknown>[]).map(normalizeNewsItem);
  const total = Number(data?.total ?? arr.length);
  return {
    list,
    total,
    page: Number(data?.page ?? page),
    keyword: data?.keyword ?? keyword,
    hasMore: Boolean(data?.hasMore ?? (page - 1) * pageSize + list.length < total),
  };
}
