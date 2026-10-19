import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 搜索入口页（F3' + E2 联想）。
 *
 * 值得钉的两条：
 * 1. **热搜失败不能退回写死假热词**——那是误导（用户以为是真实趋势）；
 * 2. **空关键词不跳转**，且合法关键词要**先记历史再跳**（顺序反了历史会丢）。
 *
 * E2 追加的重点是联想链路的**两个容易悄悄坏掉**的性质：
 * - **debounce**：逐键请求等于把"输入过程"打成服务端请求，联想本是为省事而存在；
 * - **代际守卫**：慢请求先回来会覆盖快请求，表现为"下拉的词落后于输入"
 *   ——这种错位在真机上极难复现，只能靠测试钉住。
 */

const fetchHotTerms = vi.fn(async () => [{ term: '真实热搜', count: 3 }]);
const fetchSuggestions = vi.fn(async (_q: string, _limit?: number) => [
  { term: '国产大飞机', kind: 'hot', hitCount: 5 },
]);
vi.mock('@/api/news', () => ({
  fetchHotTerms: () => fetchHotTerms(),
  fetchSuggestions: (q: string, limit?: number) => fetchSuggestions(q, limit),
}));

const addSearchTerm = vi.fn();
const searchTerms: string[] = [];
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    addSearchTerm,
    searchTerms,
    clearSearchHistory: vi.fn(),
    removeSearchTerm: vi.fn(),
  }),
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import Search from '@/views/Search.vue';
import type { Suggestion } from '@/api/news';

const mountPage = () => mount(Search);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await vi.advanceTimersByTimeAsync(400);
  await w.vm.$nextTick();
};
const vm = (w: ReturnType<typeof mountPage>) =>
  w.vm as unknown as { keyword: string; go: (q: string) => void; submit: () => void };

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  fetchHotTerms.mockResolvedValue([{ term: '真实热搜', count: 3 }]);
  fetchSuggestions.mockResolvedValue([{ term: '国产大飞机', kind: 'hot', hitCount: 5 }]);
});

afterEach(() => {
  vi.useRealTimers();
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

describe('Search 输入联想（E2）', () => {
  it('输入后按 debounce 请求一次，并带上条数上限', async () => {
    const w = mountPage();
    await settle(w);
    vi.clearAllMocks();
    fetchSuggestions.mockResolvedValue([{ term: '暴雨预警', kind: 'title', hitCount: 0 }]);

    await w.find('input').setValue('暴雨');
    // 还没到 debounce 间隔：不能有请求（逐键请求会把输入过程打成服务端压力）
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchSuggestions).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(400);
    expect(fetchSuggestions).toHaveBeenCalledTimes(1);
    expect(fetchSuggestions).toHaveBeenCalledWith('暴雨', 8);
  });

  it('连续输入只发一次请求（debounce 合并，不是每键一次）', async () => {
    const w = mountPage();
    await settle(w);

    await w.find('input').setValue('暴');
    await vi.advanceTimersByTimeAsync(100);
    await w.find('input').setValue('暴雨');
    await vi.advanceTimersByTimeAsync(100);
    await w.find('input').setValue('暴雨蓝');
    await vi.advanceTimersByTimeAsync(400);

    expect(fetchSuggestions).toHaveBeenCalledTimes(1);
    expect(fetchSuggestions).toHaveBeenCalledWith('暴雨蓝', 8);
  });

  it('**慢请求不覆盖新输入的结果**（代际守卫，否则下拉落后于输入）', async () => {
    const w = mountPage();
    await settle(w);

    // 第一次请求挂起不放行，第二次立刻返回：若没守卫，第一次回来时会覆盖第二次的结果
    let release: (v: Suggestion[]) => void = () => {};
    fetchSuggestions.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    await w.find('input').setValue('暴');
    await vi.advanceTimersByTimeAsync(400);

    fetchSuggestions.mockResolvedValueOnce([{ term: '暴雨预警', kind: 'title', hitCount: 0 }]);
    await w.find('input').setValue('暴雨');
    await vi.advanceTimersByTimeAsync(400);
    await w.vm.$nextTick();
    expect(w.text()).toContain('暴雨预警');

    // 迟到的旧响应回来了：必须被丢弃，不能把已显示的下拉换回旧词
    release([{ term: '暴风雪', kind: 'title', hitCount: 0 }]);
    await vi.advanceTimersByTimeAsync(0);
    await w.vm.$nextTick();
    expect(w.text()).toContain('暴雨预警');
    expect(w.text()).not.toContain('暴风雪');
  });

  it('点建议直接跳转 + 记历史，不二次确认', async () => {
    fetchSuggestions.mockResolvedValue([{ term: '国产大飞机', kind: 'correct', hitCount: 4 }]);
    const w = mountPage();
    await settle(w);

    await w.find('input').setValue('国产大飞');
    await vi.advanceTimersByTimeAsync(400);
    await w.find('.search__suggest-btn').trigger('click');

    expect(routerPush).toHaveBeenCalledWith({
      path: '/search/results',
      query: { q: '国产大飞机' },
    });
    expect(addSearchTerm).toHaveBeenCalledWith('国产大飞机');
    // 跳转后下拉收起（留着会挡住返回后的界面）
    expect(w.find('.search__suggest').exists()).toBe(false);
  });

  it('纠错项标注"您是不是想搜"（让用户知道这是纠错，不是更多候选）', async () => {
    fetchSuggestions.mockResolvedValue([{ term: '新能源汽车', kind: 'correct', hitCount: 3 }]);
    const w = mountPage();
    await settle(w);

    await w.find('input').setValue('新能源汽吃');
    await vi.advanceTimersByTimeAsync(400);
    await w.vm.$nextTick();

    expect(w.text()).toContain('您是不是想搜');
  });

  it('空输入收起联想且不发请求（清空就是清空）', async () => {
    const w = mountPage();
    await settle(w);
    await w.find('input').setValue('暴');
    await vi.advanceTimersByTimeAsync(400);
    vi.clearAllMocks();

    await w.find('input').setValue('');
    await vi.advanceTimersByTimeAsync(400);

    expect(fetchSuggestions).not.toHaveBeenCalled();
    expect(w.find('.search__suggest').exists()).toBe(false);
  });

  it('联想接口失败静默降级：不弹错误、不挡住搜索', async () => {
    fetchSuggestions.mockRejectedValueOnce(new Error('挂了'));
    const w = mountPage();
    await settle(w);

    await w.find('input').setValue('暴');
    await vi.advanceTimersByTimeAsync(400);
    await w.vm.$nextTick();

    expect(w.find('.search__suggest').exists()).toBe(false);
    // 提交路径仍然可用
    await w.find('.search__go').trigger('click');
    expect(routerPush).toHaveBeenCalled();
  });

  it('联想结果为空时不渲染空面板', async () => {
    fetchSuggestions.mockResolvedValue([]);
    const w = mountPage();
    await settle(w);

    await w.find('input').setValue('暴');
    await vi.advanceTimersByTimeAsync(400);
    await w.vm.$nextTick();

    expect(w.find('.search__suggest').exists()).toBe(false);
  });
});
