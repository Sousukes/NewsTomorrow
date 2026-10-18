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

// E6b：关注流需要 user / follows 两个 store。目录是公开数据，这里按「已加载」打桩，
// 避免每个用例都被 onMounted 的 loadDirectory 干扰。
const userState = { isLoggedIn: false };
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));
const followsState = {
  keys: [] as string[],
  loaded: true,
  loading: false,
  error: '',
  loadDirectory: vi.fn(async () => undefined),
  isFollowing: vi.fn((_key?: string) => false),
};
vi.mock('@/stores/follows', () => ({ useFollowsStore: () => followsState }));

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
    (w.vm as unknown as { onTab: (k: string) => void }).onTab('tech');

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
    const vm = w.vm as unknown as { onTab: (k: string) => void };
    vm.onTab('tech');
    await settle(w);
    vm.onTab('不存在的频道');
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

describe('HomeFeed 关注流（E6b）', () => {
  it('切到「关注」时按关注流取数，并沿用当前排序', async () => {
    const w = mountPage();
    await settle(w);
    const tabs = w.findAll('.mt-channels__item');
    expect(tabs[0].text()).toBe('关注');

    await tabs[0].trigger('click');
    await settle(w);

    expect(newsState.resetPaging).toHaveBeenCalledWith('following', expect.any(String));
    expect(newsState.fetchNews).toHaveBeenCalledWith('following', expect.anything());
  });

  it('关注流的三种空态分开：未登录 → 引导登录', async () => {
    userState.isLoggedIn = false;
    followsState.keys = [];
    const w = mountPage();
    await settle(w);
    await w.findAll('.mt-channels__item')[0].trigger('click');
    await settle(w);
    expect(w.text()).toContain('登录后查看关注的内容');
  });

  it('已登录未关注 → 引导去发现来源', async () => {
    userState.isLoggedIn = true;
    followsState.keys = [];
    const w = mountPage();
    await settle(w);
    await w.findAll('.mt-channels__item')[0].trigger('click');
    await settle(w);
    expect(w.text()).toContain('还没有关注任何来源');
    expect(w.find('.mt-empty button.mt-btn').exists()).toBe(true);
  });

  it('已关注但无内容 → 不误导用户「功能坏了」', async () => {
    userState.isLoggedIn = true;
    followsState.keys = ['src_1'];
    const w = mountPage();
    await settle(w);
    await w.findAll('.mt-channels__item')[0].trigger('click');
    await settle(w);
    expect(w.text()).toContain('关注的来源暂时没有新内容');
    expect(w.text()).not.toContain('还没有关注任何来源');
  });

  it('有内容时不显示空态', async () => {
    userState.isLoggedIn = true;
    followsState.keys = ['src_1'];
    const spy = vi.spyOn(newsState, 'bucketOf').mockReturnValue({
      list: [{ id: 'n1' }],
      loading: false,
      hasMore: false,
    } as never);
    const w = mountPage();
    await settle(w);
    await w.findAll('.mt-channels__item')[0].trigger('click');
    await settle(w);
    expect(w.text()).not.toContain('登录后查看关注的内容');
    expect(w.text()).not.toContain('还没有关注任何来源');
    spy.mockRestore();
  });
});
