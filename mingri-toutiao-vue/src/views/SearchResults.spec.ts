import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';
import { mount } from '@vue/test-utils';

// 「加载更多」是纯前端分页语义，只依赖 news store 的 search 与 user store 的
// 搜索历史记录；两者都打桩，store 自身行为由 stores.spec.ts 覆盖
const search = vi.fn();
const searchError = { value: '' };
vi.mock('@/stores/news', () => ({
  useNewsStore: () => ({ search, searchError: searchError.value }),
}));

const addSearchTerm = vi.fn();
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ addSearchTerm }),
}));

// 真实 vue-router 的 route 是响应式的，关键词变化要能触发 watcher
const routeQuery = reactive<Record<string, string>>({ q: '暴雨' });
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery }),
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}));

import SearchResults from './SearchResults.vue';
import type { NewsItem } from '@/types';

const item = (id: string): NewsItem => ({
  id,
  title: `标题${id}`,
  source: '源',
  time: '1小时前',
  channel: 'recommend',
  variant: 'text',
  commentCount: 0,
});

/** 一页结果：hasMore 由服务端给出，是「还有没有下一页」的唯一判据 */
const page = (ids: string[], hasMore: boolean, total = ids.length) => ({
  list: ids.map(item),
  total,
  page: 1,
  keyword: '暴雨',
  hasMore,
});

const mountPage = () => mount(SearchResults);
type Wrapper = ReturnType<typeof mountPage>;

const settle = async (w: Wrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const cardTitles = (w: Wrapper) =>
  w.findAllComponents({ name: 'NewsCard' }).map((c) => String(c.props('item').id));

const moreBtn = (w: Wrapper) => w.find('.results__more-btn');

beforeEach(() => {
  vi.clearAllMocks();
  searchError.value = '';
  routeQuery.q = '暴雨';
});

describe('views/SearchResults 分页语义（A5）', () => {
  it('首屏渲染第一页，按钮文案带进度', async () => {
    search.mockResolvedValue(page(['a1', 'a2'], true, 5));
    const w = mountPage();
    await settle(w);

    expect(search).toHaveBeenCalledWith('暴雨', 1, 20);
    expect(cardTitles(w)).toEqual(['a1', 'a2']);
    expect(moreBtn(w).text()).toContain('加载更多（2/5）');
    expect(addSearchTerm).toHaveBeenCalledWith('暴雨');
  });

  it('加载更多是追加而非整段替换', async () => {
    // 修复前：runSearch(2) 之后 `results.value = res.list`，用户永远只看到最后一页
    search.mockResolvedValueOnce(page(['a1', 'a2'], true, 4));
    search.mockResolvedValueOnce(page(['a3', 'a4'], false, 4));
    const w = mountPage();
    await settle(w);

    await moreBtn(w).trigger('click');
    await settle(w);

    expect(search).toHaveBeenLastCalledWith('暴雨', 2, 20);
    expect(cardTitles(w)).toEqual(['a1', 'a2', 'a3', 'a4']);
    expect(w.text()).toContain('没有更多了');
  });

  it('追加时按 id 去重（翻页间的重复项不重复渲染）', async () => {
    search.mockResolvedValueOnce(page(['a1', 'a2'], true, 4));
    search.mockResolvedValueOnce(page(['a2', 'a3'], false, 4));
    const w = mountPage();
    await settle(w);

    await moreBtn(w).trigger('click');
    await settle(w);

    expect(cardTitles(w)).toEqual(['a1', 'a2', 'a3']);
  });

  it('hasMore=false 时不展示「加载更多」按钮', async () => {
    search.mockResolvedValue(page(['a1'], false, 1));
    const w = mountPage();
    await settle(w);

    expect(moreBtn(w).exists()).toBe(false);
    expect(w.text()).toContain('没有更多了');
  });

  it('关键词变化回到第一页并重新计数（不叠加旧结果）', async () => {
    search.mockResolvedValueOnce(page(['a1', 'a2'], true, 4));
    search.mockResolvedValueOnce(page(['a3'], false, 4));
    const w = mountPage();
    await settle(w);
    await moreBtn(w).trigger('click');
    await settle(w);
    expect(cardTitles(w)).toEqual(['a1', 'a2', 'a3']);

    search.mockResolvedValue(page(['b1'], false, 1));
    routeQuery.q = '楼市';
    await w.vm.$nextTick();
    await settle(w);

    expect(search).toHaveBeenLastCalledWith('楼市', 1, 20);
    expect(cardTitles(w)).toEqual(['b1']);
  });
});
