import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 评论治理没有 mock 短路：整条链路都打桩在 api 层，与 `AdminNews.spec.ts` 同思路
vi.mock('@/api/admin', () => ({
  fetchAdminComments: vi.fn(async () => ({ list: [], total: 0, hasMore: false, nextCursor: null })),
  hideComment: vi.fn(async () => ({})),
  restoreComment: vi.fn(async () => ({})),
  reviewComment: vi.fn(async () => ({})),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routerPush = vi.fn();
/** E7：本页支持 `?status=` 直达筛选（大盘的待办卡片跳过来时带） */
const routeState: { query: Record<string, string> } = { query: {} };
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
  useRoute: () => routeState,
}));

import AdminComments from './AdminComments.vue';
import * as adminApi from '@/api/admin';
import type { AdminCommentItem } from '@/types';

const comment = (over: Partial<AdminCommentItem> = {}): AdminCommentItem => ({
  id: 'c_1',
  newsId: 'n_1',
  newsTitle: '某 AI 新品发布',
  userId: 7,
  userName: '网友甲',
  content: '这是一条评论内容',
  createdAt: '2026-10-15T09:00:00',
  likes: 3,
  parentId: null,
  rootId: null,
  replyCount: 2,
  isDeleted: false,
  // 默认"没有正在发生的隐藏"，需要隐藏态的用例自行覆盖
  hidden: false,
  hiddenAt: null,
  hiddenReason: null,
  // P1-23：默认"没有待复核"，需要该状态的用例自行覆盖
  pendingReview: false,
  reviewedAt: null,
  ...over,
});

/** 列表信封：多数用例只关心列表本身，total 默认等于行数 */
const mockList = (rows: AdminCommentItem[], total = rows.length) =>
  vi.mocked(adminApi.fetchAdminComments).mockResolvedValue({
    list: rows,
    total,
    hasMore: false,
    nextCursor: null,
  });

const mountPage = () => mount(AdminComments);
/** 等 onMounted 的异步 load() 落地 + 一次渲染 tick */
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

/** 按文案找按钮（弹层里的「确认隐藏」不会误匹配行内的「隐藏」：全等匹配） */
const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

/** 弹层内的按钮（「取消」在资讯过滤提示条里也有一个，必须限定范围） */
const confirmBtn = (w: ReturnType<typeof mountPage>, text: string) =>
  w
    .find('.admin__confirm')
    .findAll('button')
    .find((b) => b.text() === text)!;

/** 打开某一行的隐藏确认弹层并等到渲染 */
const openHide = async (w: ReturnType<typeof mountPage>, content = '这是一条评论内容') => {
  const row = w.findAll('.admin__item').find((i) => i.text().includes(content))!;
  await row
    .findAll('button')
    .find((b) => b.text() === '隐藏')!
    .trigger('click');
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  // 默认无 query：各用例按需覆盖（不清会在用例间串味）
  routeState.query = {};
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockList([]);
  vi.mocked(adminApi.hideComment).mockResolvedValue(
    comment({ hidden: true, hiddenAt: '2026-10-15T11:00:00', hiddenReason: '含广告信息' }),
  );
  vi.mocked(adminApi.restoreComment).mockResolvedValue(comment({ hidden: false }));
  vi.mocked(adminApi.reviewComment).mockResolvedValue(comment({ pendingReview: false }));
});

describe('views/AdminComments', () => {
  it('非管理员不请求列表，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminComments).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理评论');
  });

  it('管理员加载后渲染评论列表、归属资讯与主评论标记', async () => {
    mockList([
      comment(),
      comment({
        id: 'c_2',
        userName: '网友乙',
        content: '第二条回复',
        rootId: 'c_1',
        parentId: 'c_1',
        replyCount: 0,
        likes: 0,
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('网友甲');
    expect(w.text()).toContain('这是一条评论内容');
    expect(w.text()).toContain('《某 AI 新品发布》'); // 行内资讯标签
    expect(w.text()).toContain('主评论');
    expect(w.text()).toContain('回复');
    expect(w.text()).toContain('2026-10-15 09:00'); // 发表时间
    expect(w.text()).toContain('3 赞');
    expect(w.text()).toContain('2 条回复');
    expect(w.text()).toContain('共 2 条');
  });

  it('已隐藏行显示红色标记、理由与隐藏时间，操作换成恢复', async () => {
    mockList([
      comment({
        id: 'c_2',
        hidden: true,
        hiddenAt: '2026-10-15T10:30:00',
        hiddenReason: '含广告信息',
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.findAll('.admin__item')[0].classes()).toContain('is-hidden');
    expect(w.find('.admin__chip--hidden').text()).toBe('已隐藏');
    expect(w.text()).toContain('理由：含广告信息');
    expect(w.text()).toContain('隐藏于 2026-10-15 10:30');
    expect(btn(w, '恢复').exists()).toBe(true);
    // 已隐藏行不再给「隐藏」入口（幂等由后端兜底，前端不重复暴露）
    expect(w.findAll('button').some((b) => b.text() === '隐藏')).toBe(false);
  });

  it('后端 403 时展示无权限态', async () => {
    vi.mocked(adminApi.fetchAdminComments).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理评论');
  });

  it('加载失败（非 403）时显示错误横幅，不伪装成无权限', async () => {
    vi.mocked(adminApi.fetchAdminComments).mockRejectedValue(new Error('网络错误'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络错误');
    expect(w.text()).not.toContain('无权限管理评论');
  });

  it('空列表时给出空态文案', async () => {
    mockList([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('还没有任何评论');
  });
});

/* ------------------------------ 筛选与搜索 ------------------------------ */

describe('views/AdminComments 筛选与搜索', () => {
  it('状态筛选切换后带 status 重新请求，再点一次取消', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all' }),
    );

    await btn(w, '已隐藏').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'hidden' }),
    );
    // 已隐藏是处置态不是普通分类：激活态用红色
    expect(btn(w, '已隐藏').classes()).toContain('is-active');
    expect(btn(w, '已隐藏').classes()).toContain('is-danger');

    await btn(w, '已隐藏').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all' }),
    );
  });

  it('搜索回车与搜索按钮都带上 keyword 重新请求', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('广告');
    await search.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '广告' }),
    );

    await search.setValue('发布会');
    await btn(w, '搜索').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '发布会' }),
    );
  });

  it('筛选中出现清除入口，点它回到全部', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await btn(w, '可见').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    await btn(w, '清除筛选').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
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
    expect(w.text()).toContain('没有匹配的评论');
  });

  it('点行内资讯标签按文章过滤：带 newsId 重新请求并出现提示条', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await w.find('.admin__newschip').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ newsId: 'n_1' }),
    );
    expect(w.text()).toContain('仅看《某 AI 新品发布》下的评论');

    // 再点一次同一标签 = 取消（与源管理页汇总数字同款交互）
    await w.find('.admin__newschip').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ newsId: undefined }),
    );
    expect(w.text()).not.toContain('仅看《');
  });

  it('提示条上的取消按钮回到全部', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await w.find('.admin__newschip').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    await w.find('.admin__newsfilter').find('button').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ newsId: undefined }),
    );
    expect(w.find('.admin__newsfilter').exists()).toBe(false);
  });
});

/* ------------------------------ 隐藏 / 恢复 ------------------------------ */

describe('views/AdminComments 隐藏与恢复', () => {
  it('空理由被前端拦截，不发请求', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    expect(w.text()).toContain('隐藏理由（必填，≤300 字）');

    await w.find('.admin__confirm textarea').setValue('   ');
    await confirmBtn(w, '确认隐藏').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.hideComment).not.toHaveBeenCalled();
    expect(w.text()).toContain('请填写隐藏理由（留痕是准入条件）');
  });

  it('理由超长被前端拦截（后端 422 之外的双保险）', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('x'.repeat(301));
    await confirmBtn(w, '确认隐藏').trigger('click');
    await w.vm.$nextTick();
    expect(adminApi.hideComment).not.toHaveBeenCalled();
    expect(w.text()).toContain('理由不能超过 300 字');
  });

  it('填写理由后隐藏成功：行就地更新为已隐藏态并 toast', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('含广告信息');
    await confirmBtn(w, '确认隐藏').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.hideComment).toHaveBeenCalledWith('c_1', { reason: '含广告信息' });
    expect(w.text()).toContain('已隐藏该评论');
    expect(w.text()).toContain('理由：含广告信息');
    expect(w.find('.admin__confirm').exists()).toBe(false); // 弹层关闭
    // 全部筛选下隐藏行留在列表里（状态一眼可辨），按钮换成「恢复」
    expect(btn(w, '恢复').exists()).toBe(true);
  });

  it('隐藏失败时错误留在弹层内，行不变', async () => {
    mockList([comment()]);
    vi.mocked(adminApi.hideComment).mockRejectedValue(new Error('隐藏理由不能为空'));
    const w = mountPage();
    await settle(w);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('含广告信息');
    await confirmBtn(w, '确认隐藏').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('隐藏理由不能为空');
    expect(w.find('.admin__confirm').exists()).toBe(true); // 不关层，便于改完重试
    expect(btn(w, '隐藏').exists()).toBe(true); // 行仍未隐藏
  });

  it('在「可见」筛选中隐藏成功后行从列表移除并递减总数', async () => {
    mockList([comment(), comment({ id: 'c_2', userName: '网友乙', content: '第二条评论' })], 2);
    const w = mountPage();
    await settle(w);

    await btn(w, '可见').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(2);

    await openHide(w);
    await w.find('.admin__confirm textarea').setValue('含广告信息');
    await confirmBtn(w, '确认隐藏').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    // 留在可见列表里会让人误判"隐藏没生效"——不匹配当前筛选即移除
    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.findAll('.admin__item .admin__name').map((n) => n.text())).toEqual(['网友乙']);
    expect(w.text()).toContain('共 1 条');
  });

  it('恢复已隐藏评论：行内更新回可见态', async () => {
    mockList([
      comment({ id: 'c_2', hidden: true, hiddenAt: '2026-10-15T10:00:00', hiddenReason: '旧理由' }),
    ]);
    // 返回值必须带上同一行的 id，否则 applyUpdate 找不到目标行（按 id 定位）
    vi.mocked(adminApi.restoreComment).mockResolvedValue(comment({ id: 'c_2', hidden: false }));
    const w = mountPage();
    await settle(w);

    await btn(w, '恢复').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.restoreComment).toHaveBeenCalledWith('c_2');
    expect(w.text()).toContain('已恢复该评论');
    expect(w.find('.admin__chip--hidden').exists()).toBe(false);
    expect(w.text()).not.toContain('理由：旧理由');
    expect(w.findAll('.admin__item')[0].classes()).not.toContain('is-hidden');
  });

  it('恢复失败时错误进横幅，行保持已隐藏', async () => {
    mockList([comment({ id: 'c_2', hidden: true, hiddenReason: '旧理由' })]);
    vi.mocked(adminApi.restoreComment).mockRejectedValue(new Error('恢复失败'));
    const w = mountPage();
    await settle(w);

    await btn(w, '恢复').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('恢复失败');
    expect(w.find('.admin__chip--hidden').exists()).toBe(true);
  });
});

/* ------------------------------ 待复核（P1-23） ------------------------------ */

describe('views/AdminComments 待复核', () => {
  it('待复核行显示黄色标记与「通过复核」按钮，且不打断可见性', async () => {
    mockList([comment({ pendingReview: true })]);
    const w = mountPage();
    await settle(w);

    expect(w.find('.admin__chip--pending').text()).toBe('待复核');
    expect(btn(w, '通过复核').exists()).toBe(true);
    // 脱敏评论仍是可见评论：不能套用"已隐藏"的降透明度样式
    expect(w.findAll('.admin__item')[0].classes()).not.toContain('is-hidden');
    // 仍可隐藏：认定不合适要走隐藏，不是把待复核标记留着不管
    expect(btn(w, '隐藏').exists()).toBe(true);
  });

  it('非待复核行没有复核入口（标记是状态，不是常驻操作）', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);
    // btn() 用全等查找且带非空断言，断言"不存在"必须走 some 而非 !.exists()
    expect(w.findAll('button').some((b) => b.text() === '通过复核')).toBe(false);
    expect(w.find('.admin__chip--pending').exists()).toBe(false);
  });

  it('「待复核」筛选带 status=pending 重新请求，再点一次取消', async () => {
    mockList([comment({ pendingReview: true })]);
    const w = mountPage();
    await settle(w);

    await btn(w, '待复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'pending' }),
    );

    await btn(w, '待复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'all' }),
    );
  });

  it('复核成功后标记消失并 toast', async () => {
    mockList([comment({ pendingReview: true })]);
    vi.mocked(adminApi.reviewComment).mockResolvedValue(
      comment({ pendingReview: false, reviewedAt: '2026-10-15T12:00:00' }),
    );
    const w = mountPage();
    await settle(w);

    await btn(w, '通过复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.reviewComment).toHaveBeenCalledWith('c_1');
    expect(w.text()).toContain('已通过复核');
    expect(w.find('.admin__chip--pending').exists()).toBe(false);
  });

  it('在「待复核」筛选中复核成功后行从列表移除（不清数据会停在旧结果）', async () => {
    mockList(
      [
        comment({ pendingReview: true }),
        comment({ id: 'c_2', userName: '网友乙', content: '第二条评论', pendingReview: true }),
      ],
      2,
    );
    vi.mocked(adminApi.reviewComment).mockResolvedValue(
      comment({ pendingReview: false, reviewedAt: '2026-10-15T12:00:00' }),
    );
    const w = mountPage();
    await settle(w);

    await btn(w, '待复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.findAll('.admin__item')).toHaveLength(2);

    const row = w.findAll('.admin__item').find((i) => i.text().includes('这是一条评论内容'))!;
    await row
      .findAll('button')
      .find((b) => b.text() === '通过复核')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.findAll('.admin__item')).toHaveLength(1);
    expect(w.findAll('.admin__item .admin__name').map((n) => n.text())).toEqual(['网友乙']);
    expect(w.text()).toContain('共 1 条');
  });

  it('复核失败时错误进横幅，行保持待复核', async () => {
    mockList([comment({ pendingReview: true })]);
    vi.mocked(adminApi.reviewComment).mockRejectedValue(new Error('评论不存在或已删除'));
    const w = mountPage();
    await settle(w);

    await btn(w, '通过复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('评论不存在或已删除');
    expect(w.find('.admin__chip--pending').exists()).toBe(true);
  });
});

/* ------------------------------ 加载更多与历史 ------------------------------ */

describe('views/AdminComments 加载更多与历史', () => {
  it('加载更多追加第二批并带上 cursor', async () => {
    vi.mocked(adminApi.fetchAdminComments)
      .mockResolvedValueOnce({ list: [comment()], total: 3, hasMore: true, nextCursor: 'c1' })
      .mockResolvedValueOnce({
        list: [comment({ id: 'c_2', userName: '网友乙', content: '第二条评论' })],
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

    expect(adminApi.fetchAdminComments).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: 'c1' }),
    );
    expect(w.findAll('.admin__item')).toHaveLength(2);
    expect(w.text()).toContain('第二条评论');
  });

  it('没有更多时不出加载更多按钮', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);
    expect(w.findAll('button').some((b) => b.text() === '加载更多')).toBe(false);
  });

  it('历史按钮带 target 跳审计页', async () => {
    mockList([comment()]);
    const w = mountPage();
    await settle(w);
    await btn(w, '历史').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/audit?target=c_1');
  });
});

/**
 * E7（大盘待办卡片的落点）：`?status=` 直达筛选。
 *
 * 大盘只显示"待复核评论 N"却点不到对应队列，等于把运营挡在半路。
 * 两个方向都要钉：认得的档位生效、不认得的静默回落 `all`（不能白屏或 422）。
 */
describe('AdminComments · ?status= 直达筛选（E7）', () => {
  it('?status=pending 进页即按待复核筛选', async () => {
    routeState.query = { status: 'pending' };
    const w = mountPage();
    await settle(w);

    const [opts] = vi.mocked(adminApi.fetchAdminComments).mock.calls[0];
    expect(opts?.status).toBe('pending');
  });

  it('未知档位静默回落 all（不白屏、不发非法参数）', async () => {
    routeState.query = { status: 'bogus' };
    const w = mountPage();
    await settle(w);

    const [opts] = vi.mocked(adminApi.fetchAdminComments).mock.calls[0];
    expect(opts?.status).toBe('all');
  });
});
