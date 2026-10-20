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
// E3-a：`recordExposures` 是匿名期的本地缓冲入口（打桩 store 而非 api）
const userState = {
  isLoggedIn: false,
  recordExposures: vi.fn(),
};
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

/**
 * E3：`useExposure` 的判定规则（50% / 500ms / 攒批）由它自己的 spec 覆盖，
 * 这里只验**接线**——曝光回调用什么上报、进详情前有没有 flush。
 * 故这里把它换成捕获 options 的桩，而不是复跑一遍 IntersectionObserver。
 */
const exposureState: { report: ((ids: string[]) => unknown) | null } = { report: null };
const flushSpy = vi.fn(async () => undefined);
vi.mock('@/composables/useExposure', () => ({
  useExposure: (opts: { report: (ids: string[]) => unknown }) => {
    exposureState.report = opts.report;
    return {
      pending: { value: [] },
      observe: vi.fn(),
      unobserve: vi.fn(),
      flush: flushSpy,
      destroy: vi.fn(),
    };
  },
}));

// 参数类型写明：`vi.fn` 无参时 `mock.calls` 会被推断成空元组，
// 断言 `calls[0][0]` 报 "Source has 0 element(s)"（同 store 桩的处理）
const addDislike = vi.fn(async (_target: { newsId: string } | { sourceKey: string }) => ({
  newsIds: [],
  list: [],
  sourceKeys: [],
  sources: [],
}));
const reportExposures = vi.fn(async (_ids: string[]) => ({ accepted: 0, written: 0 }));
vi.mock('@/api/feedback', () => ({
  addDislike: (target: { newsId: string } | { sourceKey: string }) => addDislike(target),
  reportExposures: (ids: string[]) => reportExposures(ids),
}));

import HomeFeed from './HomeFeed.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';
import ReportDialog from '@/components/ReportDialog.vue';

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
  // 登录态是跨用例共享的同一个桩对象，显式复位避免用例间顺序依赖
  userState.isLoggedIn = false;
  // 已读集合落 localStorage，不清会在用例之间串味
  localStorage.clear();
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
    // NewsCard 对外是原生 click，由覆盖整卡的链接触发（E9 起 article 不再挂 @click）
    card[0].find('.mt-card__link').trigger('click');
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

/**
 * HomeFeed · 负反馈与曝光（E3）
 *
 * 钉的是"用户能感知的三件事"：看完的不再占位、踩了立刻消失、举报不混进负反馈。
 */
describe('HomeFeed 负反馈与曝光（E3）', () => {
  const twoCards = () =>
    vi.spyOn(newsState, 'bucketOf').mockReturnValue({
      list: [
        { id: 'n1', title: '第一条', source: '中新网', sourceKey: 's1' },
        { id: 'n2', title: '第二条', source: '新华社', sourceKey: 's2' },
      ],
      loading: false,
      hasMore: false,
    } as never);

  it('未登录不调服务端接口，改记本地缓冲（E3-a：登录时才被采纳）', async () => {
    userState.isLoggedIn = false;
    const w = mountPage();
    await settle(w);
    exposureState.report?.(['n1']);
    // 匿名上报只会吃 401；落本地是为了登录时能采纳（设计 §十一 理由一）
    expect(reportExposures).not.toHaveBeenCalled();
    expect(userState.recordExposures).toHaveBeenCalledWith(['n1']);
    w.unmount();
  });

  it('已登录时曝光回调走 /me/exposures', async () => {
    userState.isLoggedIn = true;
    const w = mountPage();
    await settle(w);
    exposureState.report?.(['n1', 'n2']);
    expect(reportExposures).toHaveBeenCalledWith(['n1', 'n2']);
    w.unmount();
  });

  it('进详情前先 flush 攒下的曝光（否则返回时服务端还没排除刚看过的这批）', async () => {
    userState.isLoggedIn = true;
    const spy = twoCards();
    const w = mountPage();
    await settle(w);
    w.findAllComponents(NewsCard)[0].find('.mt-card__link').trigger('click');
    await settle(w);
    expect(flushSpy).toHaveBeenCalled();
    expect(routerPush).toHaveBeenCalledWith('/news/n1');
    spy.mockRestore();
    w.unmount();
  });

  it('看过的那条原地移除，而不是留一个「已读」占位', async () => {
    userState.isLoggedIn = true;
    const spy = twoCards();
    const w = mountPage();
    await settle(w);
    expect(w.findAllComponents(NewsCard)).toHaveLength(2);
    w.findAllComponents(NewsCard)[0].find('.mt-card__link').trigger('click');
    await settle(w);
    const left = w.findAllComponents(NewsCard);
    expect(left).toHaveLength(1);
    expect(left[0].props('item').id).toBe('n2');
    spy.mockRestore();
    w.unmount();
  });

  it('「不感兴趣」立刻移除并调 addDislike（用户点了就该马上看到效果）', async () => {
    userState.isLoggedIn = true;
    const spy = twoCards();
    const w = mountPage();
    await settle(w);
    w.findAllComponents(NewsCard)[0].vm.$emit('feedback', {
      action: 'news',
      id: 'n1',
      sourceKey: 's1',
    });
    await settle(w);
    expect(addDislike).toHaveBeenCalledWith({ newsId: 'n1' });
    expect(w.findAllComponents(NewsCard)).toHaveLength(1);
    spy.mockRestore();
    w.unmount();
  });

  it('「减少该来源」按 sourceKey 提交，且不动列表（衰减不是硬过滤）', async () => {
    userState.isLoggedIn = true;
    const spy = twoCards();
    const w = mountPage();
    await settle(w);
    w.findAllComponents(NewsCard)[0].vm.$emit('feedback', {
      action: 'source',
      id: 'n1',
      sourceKey: 's1',
    });
    await settle(w);
    expect(addDislike).toHaveBeenCalledWith({ sourceKey: 's1' });
    expect(w.findAllComponents(NewsCard)).toHaveLength(2);
    spy.mockRestore();
    w.unmount();
  });

  it('举报打开 E5 弹窗，绝不走负反馈接口（两者严格分离）', async () => {
    userState.isLoggedIn = true;
    const spy = twoCards();
    const w = mountPage();
    await settle(w);
    const dialog = w.findComponent(ReportDialog);
    expect(dialog.props('open')).toBe(false);
    w.findAllComponents(NewsCard)[0].vm.$emit('feedback', {
      action: 'report',
      id: 'n1',
      sourceKey: 's1',
    });
    await settle(w);
    expect(w.findComponent(ReportDialog).props('open')).toBe(true);
    expect(w.findComponent(ReportDialog).props('targetId')).toBe('n1');
    expect(addDislike).not.toHaveBeenCalled();
    spy.mockRestore();
    w.unmount();
  });
});
