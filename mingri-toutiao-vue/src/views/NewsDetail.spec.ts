import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive, ref } from 'vue';
import { mount } from '@vue/test-utils';
import type { NewsItem } from '@/types';

// 详情渲染只关心 useNewsDetail 给出的 item（ref）；store / 网络层全部打桩
const item = ref<NewsItem | null>(null);
const resolving = ref(false);
// 工厂内必须惰性引用（vi.mock 会被提升，eager 解构会踩 TDZ）
vi.mock('@/composables/useNewsDetail', () => ({
  useNewsDetail: () => ({ item, resolving }),
}));

const addHistory = vi.fn();
const isFavorite = vi.fn(() => false);
const toggleFavorite = vi.fn();
// 登录态做成可变对象：E5（举报）与 E6b（关注）的未登录分支都要在这里切换
const userState = { isLoggedIn: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    addHistory,
    isFavorite,
    toggleFavorite,
    isLoggedIn: userState.isLoggedIn,
  }),
}));

const countOf = vi.fn(() => 0);
vi.mock('@/stores/comments', () => ({
  useCommentsStore: () => ({ countOf }),
}));

// E6b：关注来源。目录默认按「已加载」打桩，避免 onMounted 的 loadDirectory 干扰用例
const follow = vi.fn(async (_key?: string) => true);
const unfollow = vi.fn(async (_key?: string) => true);
const followsState = {
  keys: [] as string[],
  loaded: true,
  loading: false,
  error: '',
  isFollowing: vi.fn((key?: string) => followsState.keys.includes(key ?? '')),
  loadDirectory: vi.fn(async () => undefined),
  follow: (key: string) => follow(key),
  unfollow: (key: string) => unfollow(key),
};
vi.mock('@/stores/follows', () => ({
  useFollowsStore: () => followsState,
}));

const fetchRelated = vi.fn(async (_id: string, _limit?: number) => []);
// 工厂内必须惰性引用（vi.mock 会被提升，eager 解构会踩 TDZ）
vi.mock('@/api/news', () => ({
  fetchRelated: (id: string, limit?: number) => fetchRelated(id, limit),
}));

const routeParams = reactive<{ id: string }>({ id: 't_body' });
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}));

import NewsDetail from './NewsDetail.vue';

const detailItem = (over: Partial<NewsItem> = {}): NewsItem => ({
  id: 't_body',
  title: '有正文的资讯',
  source: '测试源',
  time: '1小时前',
  channel: 'recommend',
  variant: 'text',
  commentCount: 3,
  ...over,
});

const mountPage = () => mount(NewsDetail);
type Wrapper = ReturnType<typeof mountPage>;

const settle = async (w: Wrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const paras = (w: Wrapper) => w.findAll('.detail__para').map((p) => p.text());

beforeEach(() => {
  vi.clearAllMocks();
  item.value = null;
  resolving.value = false;
  userState.isLoggedIn = true;
  followsState.keys = [];
});

describe('views/NewsDetail 正文渲染（E1）', () => {
  it('按段落渲染真实正文（后端以 \\n 分隔下发）', async () => {
    item.value = detailItem({ content: '第一段内容。\n第二段内容。\n第三段内容。' });
    const w = mountPage();
    await settle(w);

    expect(paras(w)).toEqual(['第一段内容。', '第二段内容。', '第三段内容。']);
  });

  it('无正文时显示「暂无正文」，且不渲染任何占位文案', async () => {
    // 修复前：这里是写死的三段「据相关方面介绍……」——界面假装有内容
    item.value = detailItem();
    const w = mountPage();
    await settle(w);

    expect(paras(w)).toEqual(['暂无正文']);
    expect(w.text()).not.toContain('据相关方面介绍');
  });

  it('空串正文同样走「暂无正文」（trim 后无段落）', async () => {
    item.value = detailItem({ content: '  \n  ' });
    const w = mountPage();
    await settle(w);

    expect(paras(w)).toEqual(['暂无正文']);
  });

  it('标题/来源/评论数等既有信息不受正文改动影响', async () => {
    item.value = detailItem({ content: '正文一段。' });
    const w = mountPage();
    await settle(w);

    expect(w.find('.detail__title').text()).toBe('有正文的资讯');
    expect(w.text()).toContain('测试源');
    expect(w.text()).toContain('3评论');
    expect(addHistory).toHaveBeenCalledWith('t_body');
  });
});

describe('views/NewsDetail 举报入口（E5）', () => {
  it('默认不显示举报弹窗', async () => {
    item.value = detailItem();
    const w = mountPage();
    await settle(w);

    expect(w.find('.mask').exists()).toBe(false);
  });

  it('点击导航「更多」打开举报弹窗，目标为当前资讯', async () => {
    // 修复前：「更多」是个只有图标、没有处理函数的死按钮
    item.value = detailItem({ id: 'n_report' });
    const w = mountPage();
    await settle(w);

    await w.find('button[aria-label="更多"]').trigger('click');
    await settle(w);

    expect(w.find('.mask').exists()).toBe(true);
    expect(w.text()).toContain('举报该资讯');
    // 弹窗内理由列表就绪 → 目标 id 已透传（未传会被后端 40409）
    expect(w.findAll('.reason').length).toBeGreaterThan(0);
  });
});

describe('views/NewsDetail 关注来源（E6b）', () => {
  it('有 sourceKey 时才渲染关注按钮（缺 key 不能放一个点不了的死按钮）', async () => {
    item.value = detailItem({ sourceKey: 'src_1' });
    const w = mountPage();
    await settle(w);
    expect(w.find('.mt-nav__follow').exists()).toBe(true);

    item.value = detailItem();
    const w2 = mountPage();
    await settle(w2);
    expect(w2.find('.mt-nav__follow').exists()).toBe(false);
  });

  it('点击关注：已关注态取关，未关注态关注', async () => {
    item.value = detailItem({ sourceKey: 'src_1' });
    const w = mountPage();
    await settle(w);

    await w.find('.mt-nav__follow').trigger('click');
    await settle(w);
    expect(follow).toHaveBeenCalledWith('src_1');

    followsState.keys = ['src_1'];
    const w2 = mountPage();
    await settle(w2);
    await w2.find('.mt-nav__follow').trigger('click');
    await settle(w2);
    expect(unfollow).toHaveBeenCalledWith('src_1');
  });

  it('未登录时不调接口，先弹登录门禁（别把失败留到最后一步）', async () => {
    item.value = detailItem({ sourceKey: 'src_1' });
    userState.isLoggedIn = false;
    const w = mountPage();
    await settle(w);

    await w.find('.mt-nav__follow').trigger('click');
    await settle(w);

    expect(follow).not.toHaveBeenCalled();
    expect(unfollow).not.toHaveBeenCalled();
    expect(w.text()).toContain('关注来源');
    expect(w.text()).toContain('登录后即可关注该来源');
  });

  it('关注失败时把原因显示出来（不默默吞掉，用户会以为点了没反应）', async () => {
    follow.mockResolvedValueOnce(false);
    item.value = detailItem({ sourceKey: 'src_1' });
    userState.isLoggedIn = true;
    const w = mountPage();
    await settle(w);

    await w.find('.mt-nav__follow').trigger('click');
    await settle(w);

    expect(w.find('.detail__hint').exists()).toBe(true);
    expect(w.find('.detail__hint').text()).toContain('操作失败');
  });
});
