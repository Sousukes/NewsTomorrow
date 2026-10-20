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

const offlineDetail = vi.fn((_id?: string): unknown => null);
const newsState = {
  getById: vi.fn((id: string) => (id === 'gone' ? null : { id, title: `标题${id}` })),
  // E8 / P1-79：离线兜底。默认 null（在线路径不受影响），
  // 断网场景由下面专门的用例覆盖
  offlineDetail,
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
    w.findAllComponents(NewsCard)[0].find('.mt-card__link').trigger('click');
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

describe('History 离线兜底（E8 / P1-79）', () => {
  beforeEach(() => {
    userState.history = ['n1', 'gone'];
  });

  it('内存里没有的 id 回退到本地阅读副本（断网重启后历史仍可点开）', () => {
    offlineDetail.mockReturnValue({ id: 'gone', title: '离线副本标题', content: '正文' });
    const w = mountPage();
    expect(offlineDetail).toHaveBeenCalledWith('gone');
    const titles = w.findAllComponents(NewsCard).map((c) => c.props('item').title);
    expect(titles).toContain('离线副本标题');
  });

  it('副本也没有时才剔除该 id（不渲染空白卡片）', () => {
    offlineDetail.mockReturnValue(null);
    const w = mountPage();
    const items = w.findAllComponents(NewsCard).map((c) => c.props('item'));
    expect(items).toHaveLength(1); // 只剩内存命中的 n1
    expect(items[0].id).toBe('n1');
  });
});
