import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, type VueWrapper } from '@vue/test-utils';

/**
 * HomeFeed 视图层：频道/排序切换与桶隔离的**组合行为**。
 *
 * 桶与竞态的底层逻辑由 `stores/news.spec.ts` 覆盖（那里断言的是 fetch 调用与
 * 代次），本文件补的是**视图接线**：切换频道是否带上了当前排序、排序切换是否
 * 先 resetPaging、打开详情是否带上 id。
 *
 * 打桩 store 而不是 api：视图只碰 store，api 链路在 store 层已被覆盖，
 * 这里再打一层只会让"到底哪层坏了"变难判断。
 */

const newsState = {
  resetPaging: vi.fn(),
  // 参数类型写明：否则 vi.fn 的 `mock.calls` 被推断成空元组，
  // 断言 `calls[0][0]` 时报 "Source has 0 element(s)"
  fetchNews: vi.fn(async (_channel: string, _sort: string) => undefined),
  loadMore: vi.fn(async (_channel: string, _sort: string) => 0),
  bucketOf: vi.fn((_channel: string, _sort: string) => ({
    list: [] as { id: string }[],
    loading: false,
    hasMore: false,
  })),
  fetchChannelPrefs: vi.fn(async () => undefined),
};

vi.mock('@/stores/news', () => ({ useNewsStore: () => newsState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import HomeFeed from './HomeFeed.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';

const mountPage = () =>
  mount(HomeFeed, {
    global: {
      stubs: { AppNav: true, BannerCarousel: true },
    },
  });

const settle = async (w: VueWrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
});

describe('HomeFeed', () => {
  it('挂载时按默认频道与排序初始化并拉取', async () => {
    const w = mountPage();
    await settle(w);
    expect(newsState.resetPaging).toHaveBeenCalled();
    const [channel, sort] = newsState.fetchNews.mock.calls[0];
    expect(channel).toBeTruthy();
    expect(sort).toBeTruthy();
  });

  it('切换频道时保留当前排序（不把用户选的排序重置掉）', async () => {
    const w = mountPage();
    await settle(w);
    const tabs = w.findComponent(ChannelTabs);
    expect(tabs.exists()).toBe(true);

    // 先选一个排序，再切频道
    (w.vm as unknown as { onSort: (k: string) => void }).onSort('hot');
    (w.vm as unknown as { onChannel: (k: string) => void }).onChannel('tech');

    const last = newsState.fetchNews.mock.calls.at(-1)!;
    expect(last[0]).toBe('tech');
    expect(last[1]).toBe('hot');
  });

  it('切换排序前先 resetPaging：排序键变了旧游标失效', async () => {
    const w = mountPage();
    await settle(w);
    vi.clearAllMocks();
    (w.vm as unknown as { onSort: (k: string) => void }).onSort('hot');
    const order = newsState.resetPaging.mock.invocationCallOrder[0];
    const fetchOrder = newsState.fetchNews.mock.invocationCallOrder[0];
    expect(order).toBeLessThan(fetchOrder);
  });

  it('未知频道 key 回落到默认频道（不可信输入兜底）', async () => {
    const w = mountPage();
    await settle(w);
    vi.clearAllMocks();
    (w.vm as unknown as { onChannel: (k: string) => void }).onChannel('不存在的频道');
    const last = newsState.fetchNews.mock.calls.at(-1) as [string, string];
    expect(last[0]).toBeTruthy(); // 归一化后的合法值，而不是原样透传
    expect(last[0]).not.toBe('不存在的频道');
  });

  it('重复点击同一排序不做任何请求', async () => {
    const w = mountPage();
    await settle(w);
    // 先取当前排序，再清桩——顺序反了就拿不到初始值了
    const cur = newsState.fetchNews.mock.calls[0] as [string, string];
    vi.clearAllMocks();
    (w.vm as unknown as { onSort: (k: string) => void }).onSort(cur[1]);
    expect(newsState.fetchNews).not.toHaveBeenCalled();
  });

  it('打开资讯时带 id 跳详情', async () => {
    newsState.bucketOf.mockReturnValue({
      list: [{ id: 'n1', title: '标题' }],
      loading: false,
      hasMore: false,
    } as never);
    const w = mountPage();
    await settle(w);
    const card = w.findAllComponents(NewsCard);
    expect(card.length).toBeGreaterThan(0);
    // NewsCard 对外是原生 click（`@click="open"`），不是自定义 emit
    card[0].trigger('click');
    await settle(w);
    expect(routerPush).toHaveBeenCalledWith('/news/n1');
    newsState.bucketOf.mockReturnValue({
      list: [],
      loading: false,
      hasMore: false,
    } as never);
  });
});
