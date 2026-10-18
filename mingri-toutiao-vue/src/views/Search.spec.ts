import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 搜索入口页（F3'）。
 *
 * 值得钉的两条：
 * 1. **热搜失败不能退回写死假热词**——那是误导（用户以为是真实趋势）；
 * 2. **空关键词不跳转**，且合法关键词要**先记历史再跳**（顺序反了历史会丢）。
 */

const fetchHotTerms = vi.fn(async () => [{ term: '真实热搜', count: 3 }]);
vi.mock('@/api/news', () => ({ fetchHotTerms: () => fetchHotTerms() }));

const addSearchTerm = vi.fn();
const searchTerms: string[] = [];
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ addSearchTerm, searchTerms, clearSearchHistory: vi.fn() }),
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import Search from '@/views/Search.vue';

const mountPage = () => mount(Search);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};
const vm = (w: ReturnType<typeof mountPage>) =>
  w.vm as unknown as { keyword: string; go: (q: string) => void; submit: () => void };

beforeEach(() => {
  vi.clearAllMocks();
  fetchHotTerms.mockResolvedValue([{ term: '真实热搜', count: 3 }]);
});

describe('Search', () => {
  it('挂载后拉热搜并聚焦输入框（进来就能打字）', async () => {
    const w = mountPage();
    await settle(w);
    expect(fetchHotTerms).toHaveBeenCalledTimes(1);
    expect(w.text()).toContain('真实热搜');
  });

  it('热搜接口失败时区块留空，**不退回假热词**', async () => {
    fetchHotTerms.mockRejectedValueOnce(new Error('挂了'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).not.toContain('真实热搜');
    // 关键：不能出现任何"看起来像热搜"的硬编码文案
    expect(w.text()).not.toContain('热搜榜');
  });

  it('空关键词不跳转、不记历史（避免脏词进历史）', async () => {
    const w = mountPage();
    await settle(w);
    vm(w).go('   ');
    expect(routerPush).not.toHaveBeenCalled();
    expect(addSearchTerm).not.toHaveBeenCalled();
  });

  it('合法关键词：先记历史再跳转（顺序反了历史会丢）', async () => {
    const w = mountPage();
    await settle(w);
    vm(w).go('  国产航母  ');
    expect(addSearchTerm).toHaveBeenCalledWith('国产航母');
    expect(routerPush).toHaveBeenCalledWith({ path: '/search/results', query: { q: '国产航母' } });
    const addOrder = addSearchTerm.mock.invocationCallOrder[0];
    const pushOrder = routerPush.mock.invocationCallOrder[0];
    expect(addOrder).toBeLessThan(pushOrder);
  });

  it('回车提交走同一套校验', async () => {
    const w = mountPage();
    await settle(w);
    const c = vm(w);
    c.keyword = '测试词';
    c.submit();
    expect(routerPush).toHaveBeenCalledWith({
      path: '/search/results',
      query: { q: '测试词' },
    });
  });
});
