import { defineStore } from 'pinia';
import { ref } from 'vue';
import {
  fetchNewsById as apiFetchNewsById,
  fetchNewsPage as apiFetchNewsPage,
  searchNews as apiSearchNews,
  type SearchResult,
} from '@/api/news';
import { mockNews } from '@/data/mockNews';
import { DEFAULT_SORT } from '@/constants/sort';
import type { SortMode } from '@/constants/sort';
import type { Channel, NewsItem } from '@/types';

/**
 * 资讯 store：数据来自 api/news（未配置后端时自动走 mock）。
 * 分页采用游标（H5）：首页请求拿 nextCursor，后续 loadMore 用游标续接，
 * 服务端未返回游标时回退 offset 分页。
 */
export const useNewsStore = defineStore('news', () => {
  const list = ref<NewsItem[]>(mockNews);
  const loading = ref(false);
  const error = ref('');

  // 详情缓存：深链直达时避免重复请求
  const detailCache = ref<Record<string, NewsItem>>({});

  // 搜索状态
  const searching = ref(false);
  const searchError = ref('');

  // 分页 / 无限滚动状态
  const PAGE_SIZE = 20;
  const page = ref(0);
  const total = ref<number | null>(0);
  const hasMore = ref(true);
  const loadingMore = ref(false);
  /** 游标：服务端返回的续接位置（null 表示改用 offset 分页） */
  const cursor = ref<string | null>(null);
  /** 当前列表所属频道，供 loadMore 续接 */
  const currentChannel = ref<Channel>('recommend');
  /** 当前排序方式：loadMore 必须沿用，否则续接的游标与排序键不匹配 */
  const currentSort = ref<SortMode>(DEFAULT_SORT);

  async function fetchNews(
    channel: Channel = 'recommend',
    sort: SortMode = DEFAULT_SORT,
  ): Promise<NewsItem[]> {
    loading.value = true;
    error.value = '';
    currentChannel.value = channel;
    currentSort.value = sort;
    try {
      const res = await apiFetchNewsPage({ channel, page: 1, pageSize: PAGE_SIZE, sort });
      list.value = res.list;
      page.value = 1;
      cursor.value = res.nextCursor;
      total.value = res.total ?? res.list.length;
      hasMore.value = res.hasMore;
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败';
      hasMore.value = false;
    } finally {
      loading.value = false;
    }
    return list.value;
  }

  /**
   * 加载下一页并追加到 list（无限滚动）。
   * 幂等：并发调用时直接返回；按 id 去重避免重复插入。
   * @returns 本次追加的条数
   */
  async function loadMore(): Promise<number> {
    if (loading.value || loadingMore.value || !hasMore.value) return 0;

    loadingMore.value = true;
    error.value = '';
    try {
      const nextPage = page.value + 1;
      const res = await apiFetchNewsPage({
        channel: currentChannel.value,
        pageSize: PAGE_SIZE,
        // 关键：排序必须与首请求一致。服务端游标自带 sort 并按该排序键做 keyset 比较，
        // 若续接时换了排序，比较基准就失效了（表现为重复或漏项）。
        sort: currentSort.value,
        // 有游标优先走 keyset；否则回退 offset
        cursor: cursor.value ?? undefined,
        page: cursor.value ? undefined : nextPage,
      });

      if (!res.list.length) {
        hasMore.value = false;
        return 0;
      }

      const exist = new Set(list.value.map((i) => i.id));
      const fresh = res.list.filter((i) => !exist.has(i.id));
      if (fresh.length) list.value = [...list.value, ...fresh];

      page.value = nextPage;
      cursor.value = res.nextCursor;
      // 服务端说没更多，或整页都是重复（无法推进）→ 停止翻页
      hasMore.value = res.hasMore && fresh.length > 0;
      return fresh.length;
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败';
      hasMore.value = false;
      return 0;
    } finally {
      loadingMore.value = false;
    }
  }

  /** 重新从第一页开始（切换频道/下拉刷新时调用） */
  function resetPaging() {
    page.value = 0;
    hasMore.value = true;
    loadingMore.value = false;
    cursor.value = null;
  }

  function getById(id: string): NewsItem | null {
    return (
      detailCache.value[id] ??
      list.value.find((i) => i.id === id) ??
      mockNews.find((i) => i.id === id) ??
      null
    );
  }

  /**
   * 批量写入详情缓存（供收藏 / 浏览历史云端同步复用）。
   *
   * 云端返回的资讯可能不在当前列表中（如新设备登录后拉取收藏），
   * 缓存后 `getById()` 即可直接命中，无需逐条再请求详情。
   */
  function cacheMany(items: NewsItem[]): void {
    if (!items.length) return;
    const next = { ...detailCache.value };
    for (const item of items) {
      if (item.id) next[item.id] = item;
    }
    detailCache.value = next;
  }

  /**
   * 深链兜底：内存中查不到时按 id 拉详情。
   * @returns 命中的资讯；确实不存在返回 null
   */
  async function loadDetail(id: string): Promise<NewsItem | null> {
    const cached = getById(id);
    if (cached) return cached;

    loading.value = true;
    error.value = '';
    try {
      const item = await apiFetchNewsById(id);
      if (item) detailCache.value = { ...detailCache.value, [id]: item };
      return item;
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载失败';
      return null;
    } finally {
      loading.value = false;
    }
  }

  /** 本地过滤（离线兜底 / 搜索接口不可用时降级） */
  function searchLocal(keyword: string): NewsItem[] {
    const pool = list.value.length ? list.value : mockNews;
    const k = keyword.trim();
    if (!k) return [];
    return pool.filter((i) => i.title.includes(k) || (i.summary ?? '').includes(k));
  }

  /** 服务端搜索；失败时降级为本地过滤，保证界面不空白 */
  async function search(keyword: string, page = 1, pageSize = 20): Promise<SearchResult> {
    const k = keyword.trim();
    searching.value = true;
    searchError.value = '';
    try {
      return await apiSearchNews({ q: k, page, pageSize });
    } catch (e) {
      searchError.value = e instanceof Error ? e.message : '搜索失败';
      const list = searchLocal(k);
      return { list, total: list.length, page: 1, keyword: k, hasMore: false };
    } finally {
      searching.value = false;
    }
  }

  return {
    list,
    loading,
    error,
    detailCache,
    searching,
    searchError,
    page,
    total,
    hasMore,
    loadingMore,
    cursor,
    currentChannel,
    currentSort,
    PAGE_SIZE,
    fetchNews,
    loadMore,
    resetPaging,
    getById,
    cacheMany,
    loadDetail,
    search,
    searchLocal,
  };
});
