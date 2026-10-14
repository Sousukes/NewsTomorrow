import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 资讯审核没有 mock 短路：整条链路都打桩在 api 层，与 `AdminSources.spec.ts` 同思路
vi.mock('@/api/admin', () => ({
  fetchAdminNews: vi.fn(async () => ({ list: [], total: 0, hasMore: false, nextCursor: null })),
  hideNews: vi.fn(async () => ({})),
  restoreNews: vi.fn(async () => ({})),
  updateNewsTop: vi.fn(async () => ({})),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import AdminNews from './AdminNews.vue';
import * as adminApi from '@/api/admin';
import type { AdminNewsItem } from '@/types';

const news = (over: Partial<AdminNewsItem> = {}): AdminNewsItem => ({
  id: 'n_1',
  title: '某 AI 新品发布',
  summary: '一场关于端侧模型的发布会',
  source: 'AIHOT·精选',
  commentCount: 12,
  time: '2026-10-15T08:00:00',
  isTop: false,
  channel: 'tech',
  publishedAt: '2026-10-15T08:00:00',
  hotScore: 12.3456,
  // 默认"没有正在发生的下架"，需要下架态的用例自行覆盖
  hidden: false,
  hiddenAt: null,
  hiddenReason: null,
  ...over,
});

/** 列表信封：多数用例只关心列表本身，total 默认等于行数 */
const mockList = (rows: AdminNewsItem[], total = rows.length) =>
  vi.mocked(adminApi.fetchAdminNews).mockResolvedValue({
    list: rows,
    total,
    hasMore: false,
    nextCursor: null,
  });

const mountPage = () => mount(AdminNews);
/** 等 onMounted 的异步 load() 落地 + 一次渲染 tick */
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

/** 按文案找按钮（弹层里的「确认下架」不会误匹配行内的「下架」：全等匹配） */
const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

/** 打开某一行的下架确认弹层并等到渲染 */
const openHide = async (w: ReturnType<typeof mountPage>, title = '某 AI 新品发布') => {
  const row = w.findAll('.admin__item').find((i) => i.text().includes(title))!;
  await row
    .findAll('button')
    .find((b) => b.text() === '下架')!
    .trigger('click');
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockList([]);
  vi.mocked(adminApi.hideNews).mockResolvedValue(
    news({ hidden: true, hiddenAt: '2026-10-15T11:00:00', hiddenReason: '内容错误' }),
  );
  vi.mocked(adminApi.restoreNews).mockResolvedValue(news({ hidden: false }));
  vi.mocked(adminApi.updateNewsTop).mockResolvedValue(news());
});

describe('views/AdminNews', () => {
  it('非管理员不请求列表，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminNews).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限审核资讯');
  });

  it('管理员加载后渲染资讯列表、频道与评论数', async () => {
    mockList([
      news(),
      news({
        id: 'n_2',
        title: '另一条',
        channel: 'finance',
        commentCount: 3,
        publishedAt: null,
        summary: null,
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('某 AI 新品发布');
    expect(w.text()).toContain('一场关于端侧模型的发布会');
    expect(w.text()).toContain('科技'); // 频道标签
    expect(w.text()).toContain('AIHOT·精选');
    expect(w.text()).toContain('12 评论');
    expect(w.text()).toContain('2026-10-15 08:00'); // 发布时间
    expect(w.text()).toContain('共 2 条');
  });

  it('置顶行显示置顶标记', async () => {
    mockList([news({ isTop: true })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__chip--top').text()).toBe('置顶');
  });

  it('已下架行显示红色标记、理由与下架时间', async () => {
    mockList([
      news({
        id: 'n_2',
        hidden: true,
        hiddenAt: '2026-10-15T10:30:00',
        hiddenReason: '来源不可靠',
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.findAll('.admin__item')[0].classes()).toContain('is-hidden');
    expect(w.find('.admin__chip--hidden').text()).toBe('已下架');
    expect(w.text()).toContain('理由：来源不可靠');
    expect(w.text()).toContain('下架于 2026-10-15 10:30');
  });

  it('后端 403 时展示无权限态', async () => {
    vi.mocked(adminApi.fetchAdminNews).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限审核资讯');
  });

  it('加载失败（非 403）时显示错误横幅，不伪装成无权限', async () => {
    vi.mocked(adminApi.fetchAdminNews).mockRejectedValue(new Error('网络错误'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络错误');
    expect(w.text()).not.toContain('无权限审核资讯');
  });

  it('空列表时给出空态文案', async () => {
    mockList([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('还没有任何资讯');
  });
});

/* ------------------------------ 筛选与搜索 ------------------------------ */

describe('views/AdminNews 筛选与搜索', () => {
  it('状态筛选切换后带 status 重新请求，再点一次取消', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all' }),
    );

    await btn(w, '已下架').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'hidden' }),
    );
    // 已下架是处置态不是普通分类：激活态用红色
    expect(btn(w, '已下架').classes()).toContain('is-active');
    expect(btn(w, '已下架').classes()).toContain('is-danger');

    await btn(w, '已下架').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all' }),
    );
  });

  it('搜索回车与搜索按钮都带上 keyword 重新请求', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('发布会');
    await search.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '发布会' }),
    );

    await search.setValue('端侧');
    await btn(w, '搜索').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '端侧' }),
    );
  });

  it('筛选中出现清除入口，点它回到全部', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '可见').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    await btn(w, '清除筛选').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all', keyword: '' }),
    );
  });

  it('筛选无结果时提示清除筛选', async () => {
    mockList([], 0);
    const w = mountPage();
    await settle(w);
    await btn(w, '可见').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.text()).toContain('没有匹配的资讯');
  });
});

/* ------------------------------ 下架 / 恢复 ------------------------------ */

describe('views/AdminNews 下架与恢复', () => {
  it('空理由被前端拦截，不发请求', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    expect(w.text()).toContain('下架《某 AI 新品发布》');
    expect(w.text()).toContain('下架理由（必填，≤300 字）');

    await w.find('.admin__confirm textarea').setValue('   ');
    await btn(w, '确认下架').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.hideNews).not.toHaveBeenCalled();
    expect(w.text()).toContain('请填写下架理由（留痕是准入条件）');
  });

  it('理由超长被前端拦截（后端 422 之外的双保险）', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('x'.repeat(301));
    await btn(w, '确认下架').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.hideNews).not.toHaveBeenCalled();
    expect(w.text()).toContain('理由不能超过 300 字');
  });

  it('填写理由后下架成功：行就地更新为已下架态并 toast', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('内容错误');
    await btn(w, '确认下架').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.hideNews).toHaveBeenCalledWith('n_1', { reason: '内容错误' });
    expect(w.text()).toContain('已下架《某 AI 新品发布》');
    expect(w.text()).toContain('理由：内容错误');
    expect(w.find('.admin__confirm').exists()).toBe(false); // 弹层关闭
    // 全部筛选下下架行留在列表里（状态一眼可辨），按钮换成「恢复」
    expect(btn(w, '恢复').exists()).toBe(true);
  });

  it('下架失败时错误留在弹层内，行不变', async () => {
    mockList([news()]);
    vi.mocked(adminApi.hideNews).mockRejectedValue(new Error('下架理由不能为空'));
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('内容错误');
    await btn(w, '确认下架').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('下架理由不能为空');
    expect(w.find('.admin__confirm').exists()).toBe(true); // 不关层，便于改完重试
    expect(btn(w, '下架').exists()).toBe(true); // 行仍未下架
  });

  it('在「可见」筛选中下架成功后行从列表移除并递减总数', async () => {
    mockList([news(), news({ id: 'n_2', title: '第二条' })], 2);
    const w = mountPage();
    await settle(w);

    await btn(w, '可见').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(2);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('内容错误');
    await btn(w, '确认下架').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    // 留在可见列表里会让人误判"下架没生效"——不匹配当前筛选即移除
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.findAll('.admin__item .admin__name').map((n) => n.text())).toEqual(['第二条']);
    expect(w.text()).toContain('共 1 条');
  });

  it('恢复已下架资讯：行内更新回可见态', async () => {
    mockList([
      news({ id: 'n_2', hidden: true, hiddenAt: '2026-10-15T10:00:00', hiddenReason: '旧理由' }),
    ]);
    // 返回值必须带上同一行的 id，否则 applyUpdate 找不到目标行（按 id 定位）
    vi.mocked(adminApi.restoreNews).mockResolvedValue(news({ id: 'n_2', hidden: false }));
    const w = mountPage();
    await settle(w);

    await btn(w, '恢复').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.restoreNews).toHaveBeenCalledWith('n_2');
    expect(w.text()).toContain('已恢复《某 AI 新品发布》');
    expect(w.find('.admin__chip--hidden').exists()).toBe(false);
    expect(w.text()).not.toContain('理由：旧理由');
    expect(w.findAll('.admin__item')[0].classes()).not.toContain('is-hidden');
  });

  it('恢复失败时错误进横幅，行保持已下架', async () => {
    mockList([news({ id: 'n_2', hidden: true, hiddenReason: '旧理由' })]);
    vi.mocked(adminApi.restoreNews).mockRejectedValue(new Error('恢复失败'));
    const w = mountPage();
    await settle(w);

    await btn(w, '恢复').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('恢复失败');
    expect(w.find('.admin__chip--hidden').exists()).toBe(true);
  });
});

/* ------------------------------ 置顶与更多 ------------------------------ */

describe('views/AdminNews 置顶与加载更多', () => {
  it('置顶走乐观更新：点击即换文案，PATCH 带驼峰 isTop', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '置顶').trigger('click');
    // 乐观更新立即生效（不等网络往返）
    expect(btn(w, '取消置顶').exists()).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.updateNewsTop).toHaveBeenCalledWith('n_1', { isTop: true });
  });

  it('置顶失败时回滚，避免界面与服务端不一致', async () => {
    mockList([news()]);
    vi.mocked(adminApi.updateNewsTop).mockRejectedValue(new Error('置顶操作失败'));
    const w = mountPage();
    await settle(w);

    await btn(w, '置顶').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    const labels = w.findAll('.admin__item-actions button').map((b) => b.text());
    expect(labels).toContain('置顶'); // 已回滚
    expect(labels).not.toContain('取消置顶');
    expect(w.text()).toContain('置顶操作失败');
  });

  it('加载更多追加第二批并带上 cursor', async () => {
    vi.mocked(adminApi.fetchAdminNews)
      .mockResolvedValueOnce({ list: [news()], total: 3, hasMore: true, nextCursor: 'c1' })
      .mockResolvedValueOnce({
        list: [news({ id: 'n_2', title: '第二条' })],
        total: 3,
        hasMore: false,
        nextCursor: null,
      });
    const w = mountPage();
    await settle(w);
    expect(w.findAll('.admin__item')).toHaveLength(1);

    await btn(w, '加载更多').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.fetchAdminNews).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'c1' }),
    );
    expect(w.findAll('.admin__item')).toHaveLength(2);
    expect(w.text()).toContain('第二条');
  });

  it('没有更多时不出加载更多按钮', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);
    expect(w.findAll('button').some((b) => b.text() === '加载更多')).toBe(false);
  });

  it('历史按钮带 target 跳审计页', async () => {
    mockList([news()]);
    const w = mountPage();
    await settle(w);
    await btn(w, '历史').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/audit?target=n_1');
  });
});
