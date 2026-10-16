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
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ addHistory, isFavorite, toggleFavorite }),
}));

const countOf = vi.fn(() => 0);
vi.mock('@/stores/comments', () => ({
  useCommentsStore: () => ({ countOf }),
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
