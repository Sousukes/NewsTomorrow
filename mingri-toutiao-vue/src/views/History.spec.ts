import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 浏览历史（F3'）：`user.history` → 资讯卡片的映射。
 *
 * 唯一容易错的是**过滤语义**：历史里可能残留已被下架/清理的 id，
 * `getById` 返回 null 时必须剔除——否则页面会渲染出空白卡片。
 */

const userState = {
  history: ['n1', 'n2', 'gone'] as string[],
  clearHistory: vi.fn(),
};
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const newsState = {
  getById: vi.fn((id: string) => (id === 'gone' ? null : { id, title: `标题${id}` })),
};
vi.mock('@/stores/news', () => ({ useNewsStore: () => newsState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import History from '@/views/History.vue';
import NewsCard from '@/components/NewsCard.vue';

const mountPage = () => mount(History, { global: { stubs: { AppNav: true, Button: true } } });

beforeEach(() => {
  vi.clearAllMocks();
  userState.history = ['n1', 'n2', 'gone'];
});

describe('History', () => {
  it('按历史顺序渲染，并剔除已失效的 id（否则出现空白卡片）', () => {
    const w = mountPage();
    const cards = w.findAllComponents(NewsCard);
    expect(cards.map((c) => (c.props('item') as { id: string }).id)).toEqual(['n1', 'n2']);
  });

  it('历史为空时显示空态而不是空白页', () => {
    userState.history = [];
    const w = mountPage();
    expect(w.findAllComponents(NewsCard)).toHaveLength(0);
    expect(w.text()).toContain('暂无');
  });

  it('点卡片进详情', () => {
    const w = mountPage();
    w.findAllComponents(NewsCard)[0].trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/news/n1');
  });

  it('清空要真的调 store（不是只清本地视图状态）', () => {
    const w = mountPage();
    const clearBtn = w.findAll('button').find((b) => b.text().includes('清空'));
    expect(clearBtn, '页面上应有清空入口').toBeTruthy();
    void clearBtn!.trigger('click');
    expect(userState.clearHistory).toHaveBeenCalledTimes(1);
  });
});
