import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 运营大盘（E7 / P1-77）。
 *
 * 打桩在 api 层（与其余管理端页面同思路）：本页只做"把数字摆出来 + 把待办点出去"，
 * 聚合口径由后端单测钉住。这里钉三件用户能感知的事：
 * 1. 六个板块**都在**（少一块就是页面上有个洞，而接口层看不出来）；
 * 2. 待办数字**点得动**，且跳到的是"已经筛好的"队列（只给数字不给出口等于把人挡在半路）；
 * 3. 空数据/无权限/失败三种状态下页面说人话，而不是空白或 NaN。
 */
vi.mock('@/api/admin', () => ({
  fetchDashboard: vi.fn(),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import AdminDashboard from './AdminDashboard.vue';
import * as adminApi from '@/api/admin';
import type { Dashboard } from '@/types';

const dash = (over: Partial<Dashboard> = {}): Dashboard => ({
  days: 7,
  generatedAt: '2026-10-20T06:00:00+00:00',
  content: {
    news: 491,
    newsHidden: 12,
    comments: 340,
    users: 88,
    sources: 14,
    sourcesEnabled: 12,
  },
  todo: { reports: 3, commentReviews: 2, nicknameReviews: 1 },
  engagement: {
    searches: 120,
    favorites: 8,
    history: 45,
    follows: 6,
    exposures: 900,
    dislikes: 2,
  },
  sourceHealth: {
    windowDays: 7,
    unhealthy: 2,
    worst: [
      {
        key: 'zaobao',
        name: '联合早报·中国',
        attempts: 40,
        ok: 12,
        failed: 28,
        rate: 0.3,
        avgItems: 18.5,
        lastFailAt: '2026-10-19T03:00:00+00:00',
      },
      {
        key: 'xinhua',
        name: '新华网',
        attempts: 20,
        ok: 15,
        failed: 5,
        rate: 0.75,
        avgItems: 30,
        lastFailAt: null,
      },
    ],
  },
  moderation: {
    newsHidden: 5,
    commentsHidden: 9,
    hits: { sensitive: 34, nickname: 7 },
    hitsTrend: [
      { date: '2026-10-18', sensitive: 10, nickname: 1 },
      { date: '2026-10-19', sensitive: 24, nickname: 6 },
    ],
  },
  search: {
    hot: [
      { term: '新能源', count: 12 },
      { term: '暴雨', count: 8 },
      { term: '芯片', count: 5 },
    ],
  },
  ...over,
});

const mockDash = (d: Dashboard) => vi.mocked(adminApi.fetchDashboard).mockResolvedValue(d);

const mountPage = () => mount(AdminDashboard);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};
const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text().includes(text))!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockDash(dash());
});

describe('AdminDashboard · 板块齐全', () => {
  it('一次请求拿全部数据，六个板块都渲染出来', async () => {
    const w = mountPage();
    await settle(w);

    // 单请求：拆成六个就会有六个 loading 态，也无法保证同一时间口径
    expect(adminApi.fetchDashboard).toHaveBeenCalledTimes(1);
    const text = w.text();
    for (const title of [
      '待处理',
      '内容与账号',
      '近 7 天互动',
      '源健康',
      '审核与命中',
      '热门搜索',
    ]) {
      expect(text).toContain(title);
    }
    // 关键数字抽查（可见/已下架必须分开，否则"下架了多少"看不出来）
    expect(text).toContain('491');
    expect(text).toContain('12');
    expect(text).toContain('联合早报·中国');
    expect(text).toContain('新能源');
  });

  it('成功率按百分比显示，null（无尝试）显示 — 而不是伪造成 0%', async () => {
    mockDash(
      dash({
        sourceHealth: {
          windowDays: 7,
          unhealthy: 1,
          worst: [
            {
              key: 'k',
              name: '某源',
              attempts: 0,
              ok: 0,
              failed: 0,
              rate: null,
              avgItems: null,
              lastFailAt: null,
            },
          ],
        },
      }),
    );
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('—');
    expect(w.text()).not.toContain('0%');
  });

  it('命中趋势按天一行，条形宽度按窗口峰值归一', async () => {
    const w = mountPage();
    await settle(w);
    const rows = w.findAll('.dash__trendrow');
    expect(rows).toHaveLength(2);
    // 峰值那天（24）应为满宽 100%，前一天（10）约 42%
    const bars = rows[1].findAll('.dash__bar');
    expect(bars[0].attributes('style')).toContain('width: 100%');
    expect(rows[0].findAll('.dash__bar')[0].attributes('style')).toContain('width: 42%');
  });
});

describe('AdminDashboard · 待办可点（发现→处理的链路）', () => {
  it('三条待办各自跳到"已经筛好的"队列', async () => {
    const w = mountPage();
    await settle(w);

    await btn(w, '待复核举报').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/reports');
    await btn(w, '待复核评论').trigger('click');
    // 带 query：否则运营落地在「全部」还得自己再找一遍筛选器
    expect(routerPush).toHaveBeenCalledWith('/admin/comments?status=pending');
    await btn(w, '待复核昵称').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/nickname-reviews');
  });

  it('全部为 0 时明确说"没有待处理"，而不是留三个 0 让人猜', async () => {
    mockDash(dash({ todo: { reports: 0, commentReviews: 0, nicknameReviews: 0 } }));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('当前没有需要人工处理的事项');
    expect(w.findAll('.dash__todocard')).toHaveLength(0);
  });
});

describe('AdminDashboard · 空数据与失败', () => {
  it('窗口内无失败源时说得出来（不是一片空白）', async () => {
    mockDash(dash({ sourceHealth: { windowDays: 7, unhealthy: 0, worst: [] } }));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('没有失败的源');
  });

  it('无命中 / 无热词各自给空态文案', async () => {
    mockDash(
      dash({
        moderation: {
          newsHidden: 0,
          commentsHidden: 0,
          hits: { sensitive: 0, nickname: 0 },
          hitsTrend: [],
        },
        search: { hot: [] },
      }),
    );
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('窗口内没有命中记录');
    expect(w.text()).toContain('窗口内还没有搜索记录');
  });

  it('加载失败显示提示，不把失败伪装成"没有数据"', async () => {
    vi.mocked(adminApi.fetchDashboard).mockRejectedValue(new Error('网络异常'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络异常');
  });

  it('非管理员看到无权限，且不请求接口', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限查看运营大盘');
    expect(adminApi.fetchDashboard).not.toHaveBeenCalled();
  });
});

describe('AdminDashboard · 时间窗切换', () => {
  it('切到近 30 天会重新拉一次（带上 days）', async () => {
    const w = mountPage();
    await settle(w);
    expect(vi.mocked(adminApi.fetchDashboard).mock.calls[0][0]).toEqual({ days: 7 });

    await btn(w, '近 30 天').trigger('click');
    await settle(w);
    expect(vi.mocked(adminApi.fetchDashboard).mock.calls[1][0]).toEqual({ days: 30 });
  });

  it('点当前档位不重复请求（幂等，避免白跑一次）', async () => {
    const w = mountPage();
    await settle(w);
    await btn(w, '近 7 天').trigger('click');
    await settle(w);
    expect(adminApi.fetchDashboard).toHaveBeenCalledTimes(1);
  });
});
