import { defineStore } from 'pinia';
import { ref } from 'vue';
import {
  fetchNewsById as apiFetchNewsById,
  fetchNewsPage as apiFetchNewsPage,
  searchNews as apiSearchNews,
  type SearchResult,
} from '@/api/news';
import { mockNews } from '@/data/mockNews';
import { CHANNELS, DEFAULT_CHANNEL, FOLLOWING_FEED, type ListKey } from '@/constants/channels';
import { DEFAULT_SORT, SORT_MODES } from '@/constants/sort';
import type { SortMode } from '@/constants/sort';
import { listReaderIds, readReader, readerKey, writeReader } from '@/utils/reader';
import { USE_MOCK } from '@/api/config';

/**
 * 当前账号 id（阅读副本的作用域依据）。
 *
 * 从 prefs 读而不是引入 user store：news store 被 user store 依赖
 * （浏览历史要查资讯），反向 import 会成环。prefs 里已经有权威的 userId。
 */
const userId = ref<number | null>(readUserId());
function readUserId(): number | null {
  try {
    const prefs = JSON.parse(localStorage.getItem('mingri-toutiao:user') ?? '{}');
    const v = prefs?.userId;
    return typeof v === 'number' ? v : null;
  } catch {
    return null;
  }
}
import type { NewsItem } from '@/types';

/**
 * 阅读副本（E8 / P1-79）：详情读成功后写一份到 localStorage，**读失败时回退到它**。
 *
 * 写穿放在 store 而不是路由：每条被打开的详情都该留下副本，而"打开详情"这条路
 * 只经过 `loadDetail`（列表预取走 `cacheMany`，不含正文，不落）。
 */

/**
 * 单个「列表桶」：一个 (频道 × 排序) 组合的完整列表状态。
 *
 * 为什么要分桶（A4）：此前 store 只有一个 `list` + `currentChannel`，首页与视频页
 * 共用同一份状态——视频页 `fetchNews('video')` 会把首页列表整体覆盖（KeepAlive 下
 * 切回首页看到的是视频内容），且首页触底 `loadMore()` 会按 `video` 频道往首页追加。
 * 分桶后每个视图各读各的桶，互不干扰。
 */
export interface NewsBucket {
  channel: ListKey;
  sort: SortMode;
  list: NewsItem[];
  loading: boolean;
  error: string;
  page: number;
  total: number | null;
  hasMore: boolean;
  loadingMore: boolean;
  /** 游标：服务端返回的续接位置（null 表示改用 offset 分页） */
  cursor: string | null;
  /**
   * 代次（A4 竞态守卫）：每次 `fetchNews` / `resetPaging` 递增。
   * 异步响应回来时若代次已变，说明该请求已被更新的一次请求取代，**必须丢弃**，
   * 否则旧频道/旧排序的数据会被追加进新列表并覆盖游标。
   */
  generation: number;
}

function bucketKey(channel: ListKey, sort: SortMode): string {
  return `${channel}:${sort}`;
}

/** 预建全部 (频道 × 排序) 桶：避免在渲染期按需创建（渲染期写入会触发额外更新） */
function buildBuckets(): Record<string, NewsBucket> {
  const out: Record<string, NewsBucket> = {};
  // E6b：关注流与频道共用同一张桶表——它是"另一种列表键"，不是频道。
  // 放进来后 store 的调用方无需为它单开一套 API。
  const keys: ListKey[] = [...CHANNELS.map((c) => c.key), FOLLOWING_FEED];
  for (const c of keys) {
    for (const s of SORT_MODES) {
      out[bucketKey(c, s.key)] = {
        channel: c,
        sort: s.key,
        // 仅默认桶用 mock 数据占位（保持首屏行为不变）；其余频道首次进入为空
        list: c === DEFAULT_CHANNEL && s.key === DEFAULT_SORT ? [...mockNews] : [],
        loading: false,
        error: '',
        page: 0,
        total: 0,
        hasMore: true,
        loadingMore: false,
        cursor: null,
        generation: 0,
      };
    }
  }
  return out;
}

/**
 * 资讯 store：数据来自 api/news（未配置后端时自动走 mock）。
 *
 * 列表状态按 **(频道 × 排序)** 分桶（A4）；分页采用游标（H5）：首页请求拿
 * nextCursor，后续 loadMore 用游标续接，服务端未返回游标时回退 offset 分页。
 *
 * 调用约定：所有列表方法都必须显式传频道与排序——不再有「隐式当前频道」，
 * 否则视图之间会再次互相污染。
 */
export const useNewsStore = defineStore('news', () => {
  const buckets = ref<Record<string, NewsBucket>>(buildBuckets());

  // 详情缓存：深链直达时避免重复请求
  const detailCache = ref<Record<string, NewsItem>>({});

  // 搜索状态
  const searching = ref(false);
  const searchError = ref('');

  const PAGE_SIZE = 20;

  /** 取桶（全部合法组合已预建；非法输入才按需补建） */
  function bucketOf(channel: ListKey, sort: SortMode = DEFAULT_SORT): NewsBucket {
    const key = bucketKey(channel, sort);
    let bucket = buckets.value[key];
    if (!bucket) {
      bucket = {
        channel,
        sort,
        list: [],
        loading: false,
        error: '',
        page: 0,
        total: 0,
        hasMore: true,
        loadingMore: false,
        cursor: null,
        generation: 0,
      };
      buckets.value[key] = bucket;
    }
    return bucket;
  }

  /** 便捷读取某桶的列表（模板用） */
  function listOf(channel: ListKey, sort: SortMode = DEFAULT_SORT): NewsItem[] {
    return bucketOf(channel, sort).list;
  }

  /** 拉取第一页（只影响该 (频道 × 排序) 桶） */
  async function fetchNews(
    channel: ListKey = DEFAULT_CHANNEL,
    sort: SortMode = DEFAULT_SORT,
  ): Promise<NewsItem[]> {
    const bucket = bucketOf(channel, sort);
    const gen = ++bucket.generation; // 新代次：使在途的旧 fetch / loadMore 全部失效
    bucket.loading = true;
    bucket.loadingMore = false; // 旧的 loadMore 已被本请求取代
    bucket.error = '';
    bucket.channel = channel;
    bucket.sort = sort;
    try {
      const res = await apiFetchNewsPage({
        // 关注流是独立参数而非 channel 取值（见 constants/channels.ts）
        channel: channel === FOLLOWING_FEED ? undefined : channel,
        feed: channel === FOLLOWING_FEED ? FOLLOWING_FEED : undefined,
        pageSize: PAGE_SIZE,
        sort,
      });
      if (gen !== bucket.generation) return bucket.list; // 已被更新的一次请求取代
      bucket.list = res.list;
      bucket.page = 1;
      bucket.cursor = res.nextCursor;
      bucket.total = res.total ?? res.list.length;
      bucket.hasMore = res.hasMore;
    } catch (e) {
      if (gen !== bucket.generation) return bucket.list;
      bucket.error = e instanceof Error ? e.message : '加载失败';
      bucket.hasMore = false;
    } finally {
      if (gen === bucket.generation) bucket.loading = false;
    }
    return bucket.list;
  }

  /**
   * 加载下一页并追加到该桶的 list（无限滚动）。
   *
   * 幂等：并发调用时直接返回；按 id 去重避免重复插入。
   * 竞态：捕获调用时的代次，响应回来若代次已变（用户切了频道/排序）则整体丢弃。
   * @returns 本次追加的条数
   */
  async function loadMore(
    channel: ListKey = DEFAULT_CHANNEL,
    sort: SortMode = DEFAULT_SORT,
  ): Promise<number> {
    const bucket = bucketOf(channel, sort);
    if (bucket.loading || bucket.loadingMore || !bucket.hasMore) return 0;

    const gen = bucket.generation;
    bucket.loadingMore = true;
    bucket.error = '';
    try {
      const nextPage = bucket.page + 1;
      const res = await apiFetchNewsPage({
        channel: bucket.channel === FOLLOWING_FEED ? undefined : bucket.channel,
        feed: bucket.channel === FOLLOWING_FEED ? FOLLOWING_FEED : undefined,
        pageSize: PAGE_SIZE,
        // 关键：排序必须与首请求一致。服务端游标自带 sort 并按该排序键做 keyset 比较，
        // 若续接时换了排序，比较基准就失效了（表现为重复或漏项）。
        sort,
        cursor: bucket.cursor ?? undefined,
      });

      if (gen !== bucket.generation) return 0; // 过期响应：绝不写入新列表/覆盖游标

      if (!res.list.length) {
        bucket.hasMore = false;
        return 0;
      }

      const exist = new Set(bucket.list.map((i) => i.id));
      const fresh = res.list.filter((i) => !exist.has(i.id));
      if (fresh.length) bucket.list = [...bucket.list, ...fresh];

      bucket.page = nextPage;
      bucket.cursor = res.nextCursor;
      // 服务端说没更多，或整页都是重复（无法推进）→ 停止翻页
      bucket.hasMore = res.hasMore && fresh.length > 0;
      return fresh.length;
    } catch (e) {
      if (gen !== bucket.generation) return 0;
      bucket.error = e instanceof Error ? e.message : '加载失败';
      bucket.hasMore = false;
      return 0;
    } finally {
      if (gen === bucket.generation) bucket.loadingMore = false;
    }
  }

  /** 重新从第一页开始（切换频道/排序/下拉刷新时调用）；同时使在途请求失效 */
  function resetPaging(channel: ListKey = DEFAULT_CHANNEL, sort: SortMode = DEFAULT_SORT) {
    const bucket = bucketOf(channel, sort);
    bucket.generation += 1;
    bucket.page = 0;
    bucket.hasMore = true;
    bucket.loadingMore = false;
    bucket.cursor = null;
  }

  function getById(id: string): NewsItem | null {
    const cached = detailCache.value[id];
    if (cached) return cached;
    for (const bucket of Object.values(buckets.value)) {
      const hit = bucket.list.find((i) => i.id === id);
      if (hit) return hit;
    }
    return mockNews.find((i) => i.id === id) ?? null;
  }

  /**
   * 批量写入详情缓存（供收藏 / 浏览历史云端同步复用）。
   *
   * 云端返回的资讯可能不在任何列表中（如新设备登录后拉取收藏），
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

    try {
      const item = await apiFetchNewsById(id);
      if (item) detailCache.value = { ...detailCache.value, [id]: item };
      // 读成功即写穿（不挑内容源：在线读到的才值得留，mock 数据不留本地档案）
      if (item && !USE_MOCK) {
        const scope = readerKey(userId.value);
        writeReader(scope, id, item as unknown as Record<string, unknown>);
      }
      return item;
    } catch {
      // 详情加载失败由调用方（useNewsDetail 的 failed）呈现，不污染列表的 error
      return offlineDetail(id);
    }
  }

  /**
   * 断网兜底：从阅读副本里取一条详情（E8 / P1-79）。
   *
   * 与 `getById` 的区别是它**不看内存桶**而是看本地存储——重启页面后内存已空，
   * 而副本还在。返回的条目带 `fromCache: true`，供 UI 标注"离线内容"。
   */
  function offlineDetail(id: string): NewsItem | null {
    const raw = readReader(readerKey(userId.value), id);
    if (!raw) return null;
    return { ...(raw as unknown as NewsItem), fromCache: true };
  }

  /** 本地过滤（离线兜底 / 搜索接口不可用时降级）：取默认桶作语料 */
  function searchLocal(keyword: string): NewsItem[] {
    const pool = listOf(DEFAULT_CHANNEL, DEFAULT_SORT);
    const corpus = pool.length ? pool : mockNews;
    const k = keyword.trim();
    if (!k) return [];
    return corpus.filter((i) => i.title.includes(k) || (i.summary ?? '').includes(k));
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
    offlineDetail,
    /** 让收藏/历史页能拿到当前作用域的副本 id（离线排序用） */
    readerIds: () => listReaderIds(readerKey(userId.value)),
    buckets,
    detailCache,
    searching,
    searchError,
    PAGE_SIZE,
    bucketOf,
    listOf,
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
