import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

/**
 * 举报复核页（E5 的宿主页，P1-77 补齐）。
 *
 * 这一页要证明的是"举报不再是黑洞"：用户能提交（P1-66），运营必须看得到、处理得了。
 * 钉四件事：
 * 1. **看得见被举报的是什么**（后端 P1-77 带 targetTitle / targetSnippet / targetNewsId）——
 *    只给 id 的话评论根本没有落脚点，这页就只剩一个装饰性数字；
 * 2. **确认隐藏要二次确认，驳回不用**：隐藏让内容不可见（虽可恢复），
 *    驳回是恢复可见（安全方向）。破坏性操作才需要确认；
 * 3. **处理完就地移出待处理队列**（否则下一个人重复看一遍）；
 * 4. 目标已被删除时说的是"对象已不存在"，不是留一片空白。
 */
vi.mock('@/api/admin', () => ({
  fetchReports: vi.fn(),
  resolveReport: vi.fn(),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import AdminReports from './AdminReports.vue';
import * as adminApi from '@/api/admin';
import type { AdminReportItem } from '@/types';

const report = (over: Partial<AdminReportItem> = {}): AdminReportItem => ({
  id: 1,
  targetType: 'comment',
  targetId: 'c_1',
  reason: '广告营销',
  status: 'pending',
  resolution: null,
  createdAt: '2026-10-19T09:30:00+00:00',
  targetTitle: null,
  targetSnippet: '这评论区怎么全是博彩广告，没人管吗？',
  targetNewsId: 'n_1',
  targetMissing: false,
  ...over,
});

const mockList = (rows: AdminReportItem[], nextCursor: string | null = null) =>
  vi.mocked(adminApi.fetchReports).mockResolvedValue({ list: rows, nextCursor });

const mountPage = () => mount(AdminReports);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};
const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text().includes(text))!;

/**
 * 行内动作按钮必须**限定在 `.rp__actions` 里**找：全页找「驳回」会先命中
 * 筛选器的「已驳回」chip——测试于是静默地什么都没测到（点了筛选器，自然没调接口）。
 */
const actionBtn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('.rp__actions button').find((b) => b.text().includes(text))!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockList([report()]);
  vi.mocked(adminApi.resolveReport).mockImplementation(async (id, input) =>
    report({
      id,
      status: input.resolution === 'hidden' ? 'resolved' : 'dismissed',
      resolution: input.resolution,
    }),
  );
});

describe('AdminReports · 看得见被举报的是什么', () => {
  it('评论举报带内容片段与类型标签', async () => {
    const w = mountPage();
    await settle(w);
    const text = w.text();
    expect(text).toContain('评论');
    expect(text).toContain('广告营销');
    expect(text).toContain('这评论区怎么全是博彩广告');
  });

  it('资讯举报带标题', async () => {
    mockList([
      report({
        id: 2,
        targetType: 'news',
        targetId: 'n_9',
        targetTitle: '某 AI 新品发布',
        targetSnippet: null,
        targetNewsId: 'n_9',
      }),
    ]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('某 AI 新品发布');
    expect(w.text()).toContain('资讯');
  });

  it('评论的「查看原文」跳回**所属资讯**的评论区（评论没有独立页面）', async () => {
    const w = mountPage();
    await settle(w);
    await btn(w, '查看原文').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/news/n_1/comments');
  });

  it('资讯的「查看原文」直接进详情页', async () => {
    mockList([
      report({ targetType: 'news', targetId: 'n_9', targetNewsId: 'n_9', targetTitle: '标题' }),
    ]);
    const w = mountPage();
    await settle(w);
    await btn(w, '查看原文').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/news/n_9');
  });

  it('目标已被删除时说清楚（举报不建外键，这是正常状态）', async () => {
    mockList([report({ targetMissing: true, targetSnippet: null, targetNewsId: null })]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('被举报对象已不存在');
    // 没有跳转目标时不给「查看原文」，避免点出一个 404
    expect(btn(w, '查看原文')).toBeUndefined();
  });
});

describe('AdminReports · 处置', () => {
  it('「确认隐藏」要二次确认：第一次点击不发请求', async () => {
    const w = mountPage();
    await settle(w);

    await actionBtn(w, '确认隐藏').trigger('click');
    expect(adminApi.resolveReport).not.toHaveBeenCalled();
    // 进入确认态：多出「取消」
    expect(actionBtn(w, '取消')).toBeDefined();

    await actionBtn(w, '确认隐藏').trigger('click');
    await settle(w);
    expect(adminApi.resolveReport).toHaveBeenCalledWith(1, { resolution: 'hidden' });
  });

  it('「驳回」是恢复可见的安全方向，直接生效不需确认', async () => {
    const w = mountPage();
    await settle(w);
    await actionBtn(w, '驳回').trigger('click');
    await settle(w);
    expect(adminApi.resolveReport).toHaveBeenCalledWith(1, { resolution: 'kept' });
  });

  it('确认态可以取消，且取消后不发请求', async () => {
    const w = mountPage();
    await settle(w);
    await actionBtn(w, '确认隐藏').trigger('click');
    await actionBtn(w, '取消').trigger('click');
    await settle(w);
    expect(adminApi.resolveReport).not.toHaveBeenCalled();
    expect(actionBtn(w, '取消')).toBeUndefined();
  });

  it('待处理队列里处理完就移出（下一个人不该重复看）', async () => {
    // 两条行的预览必须**互不相同**：共用同一段文案时，"移出"与"没移出"看起来一样
    mockList([
      report({ id: 1, targetId: 'c_1' }),
      report({ id: 2, targetId: 'c_2', targetSnippet: '另一条被举报的评论' }),
    ]);
    const w = mountPage();
    await settle(w);
    expect(w.findAll('.rp__item')).toHaveLength(2);

    await actionBtn(w, '驳回').trigger('click');
    await settle(w);
    expect(w.findAll('.rp__item')).toHaveLength(1);
    expect(w.text()).not.toContain('这评论区怎么全是博彩广告');
    expect(w.text()).toContain('另一条被举报的评论');
  });

  it('非待处理筛选下处置后就地更新状态，而不是移出列表', async () => {
    mockList([report({ status: 'resolved', resolution: 'hidden' })]);
    vi.mocked(adminApi.resolveReport).mockResolvedValue(
      report({ status: 'dismissed', resolution: 'kept' }),
    );
    const w = mountPage();
    // 切到「已处置」再操作
    await btn(w, '已处置').trigger('click');
    await settle(w);
    mockList([report({ status: 'resolved', resolution: 'hidden' })]);
    await btn(w, '已处置').trigger('click');
    await settle(w);
    // 该档下没有处置按钮（非 pending 不显示），列表保留 —— 这是"就地更新"的前提
    expect(w.findAll('.rp__actions')).toHaveLength(0);
  });

  it('处置失败给出提示，不静默吞掉', async () => {
    vi.mocked(adminApi.resolveReport).mockRejectedValue(new Error('举报不存在'));
    const w = mountPage();
    await settle(w);
    await actionBtn(w, '驳回').trigger('click');
    await settle(w);
    expect(w.text()).toContain('举报不存在');
  });
});

describe('AdminReports · 筛选与翻页', () => {
  it('默认只拉待处理', async () => {
    const w = mountPage();
    await settle(w);
    expect(vi.mocked(adminApi.fetchReports).mock.calls[0][0]).toMatchObject({ status: 'pending' });
  });

  it('切到「全部」重新拉（服务端筛选：状态复位 ≠ 数据复位）', async () => {
    const w = mountPage();
    await settle(w);
    await btn(w, '全部').trigger('click');
    await settle(w);
    expect(vi.mocked(adminApi.fetchReports).mock.calls[1][0]).toMatchObject({ status: 'all' });
  });

  it('有 nextCursor 才显示「加载更多」，并按游标续页', async () => {
    mockList([report()], '1');
    const w = mountPage();
    await settle(w);
    await btn(w, '加载更多').trigger('click');
    await settle(w);
    expect(vi.mocked(adminApi.fetchReports).mock.calls[1][0]).toMatchObject({ cursor: 1 });
  });

  it('没有下一页时不显示「加载更多」', async () => {
    mockList([report()], null);
    const w = mountPage();
    await settle(w);
    expect(btn(w, '加载更多')).toBeUndefined();
  });
});

describe('AdminReports · 权限与空态', () => {
  it('非管理员看到无权限且不请求接口', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限查看举报');
    expect(adminApi.fetchReports).not.toHaveBeenCalled();
  });

  it('待处理为空时说"没有待复核的举报"（是好消息，不是错误）', async () => {
    mockList([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('没有待复核的举报');
  });
});
