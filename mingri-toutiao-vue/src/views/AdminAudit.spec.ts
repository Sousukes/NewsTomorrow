import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 审计没有 mock 短路：整条链路打桩在 api 层，与 AdminSources.spec.ts 同思路
vi.mock('@/api/admin', () => ({
  fetchAuditPage: vi.fn(async () => ({ list: [], hasMore: false, nextCursor: null })),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routeQuery = { target: '' as string | undefined };
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
  useRoute: () => ({ query: routeQuery }),
}));

import AdminAudit from './AdminAudit.vue';
import * as adminApi from '@/api/admin';
import type { AdminAuditItem } from '@/types';

const item = (over: Partial<AdminAuditItem> = {}): AdminAuditItem => ({
  id: 1,
  actorPhone: '13800138000',
  action: 'source.create',
  target: 'aihot_picks',
  detail: {
    snapshot: { name: 'AIHOT·精选', url: 'https://aihot.news/feed.xml', weight: 9, enabled: true },
  },
  createdAt: '2026-10-15T08:30:00',
  ...over,
});

const mountPage = () => mount(AdminAudit);
/** 等 onMounted 的异步 load() 落地 + 一次渲染 tick */
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

beforeEach(() => {
  vi.clearAllMocks();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  routeQuery.target = undefined;
  vi.mocked(adminApi.fetchAuditPage).mockResolvedValue({
    list: [],
    hasMore: false,
    nextCursor: null,
  });
});

describe('views/AdminAudit', () => {
  it('非管理员不请求列表，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchAuditPage).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限查看审计记录');
  });

  it('渲染时间线：中文动作 / 对象 / 操作者 / 时间', async () => {
    vi.mocked(adminApi.fetchAuditPage).mockResolvedValue({
      list: [item()],
      hasMore: false,
      nextCursor: null,
    });
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('新增源');
    expect(w.text()).toContain('aihot_picks');
    expect(w.text()).toContain('由 13800138000 操作');
    expect(w.text()).toContain('2026-10-15 08:30');
  });

  it('更新类记录渲染字段级变更，且 enabled 翻成启用/停用', async () => {
    vi.mocked(adminApi.fetchAuditPage).mockResolvedValue({
      list: [
        item({
          action: 'source.update',
          detail: { changes: { weight: [9, 3], enabled: [true, false] } },
        }),
      ],
      hasMore: false,
      nextCursor: null,
    });
    const w = mountPage();
    await settle(w);

    expect(w.text()).toContain('修改源');
    expect(w.text()).toContain('权重 9→3');
    // 不出现后端视角的 true→false
    expect(w.text()).toContain('启用 启用→停用');
    expect(w.text()).not.toContain('true');
  });

  it('加载更多按游标追加，且不重复已有条目', async () => {
    vi.mocked(adminApi.fetchAuditPage)
      .mockResolvedValueOnce({
        list: [item({ id: 2, target: 's2' })],
        hasMore: true,
        nextCursor: '2',
      })
      .mockResolvedValueOnce({
        list: [item({ id: 1, target: 's1' })],
        hasMore: false,
        nextCursor: null,
      });
    const w = mountPage();
    await settle(w);

    await w
      .findAll('button')
      .find((b) => b.text() === '加载更多')!
      .trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    // 第二次请求带上首次下发的游标
    expect(adminApi.fetchAuditPage).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: '2' }),
    );
    expect(w.findAll('.admin__item')).toHaveLength(2);
    expect(w.text()).toContain('s2');
    expect(w.text()).toContain('s1');
    // 末页后不再显示"加载更多"
    expect(w.findAll('button').some((b) => b.text() === '加载更多')).toBe(false);
  });

  it('带 target 进入时只查该对象，并提供查看全部入口', async () => {
    routeQuery.target = 'aihot_picks';
    const w = mountPage();
    await settle(w);

    expect(adminApi.fetchAuditPage).toHaveBeenCalledWith(
      expect.objectContaining({ target: 'aihot_picks' }),
    );
    expect(w.text()).toContain('只看「aihot_picks」');
    expect(w.findAll('button').some((b) => b.text() === '查看全部')).toBe(true);
  });

  it('加载失败时展示错误横幅', async () => {
    vi.mocked(adminApi.fetchAuditPage).mockRejectedValue(new Error('网络异常'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络异常');
  });

  it('加载返回 403 时转入无权限态', async () => {
    vi.mocked(adminApi.fetchAuditPage).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限查看审计记录');
  });
});

// P1-18：自动禁用（默认关闭，运维可开启）。这类条目没有人类操作者，
// 留痕的意义全在"能不能看出为什么被关"——所以重点测操作者与原因两栏。
describe('views/AdminAudit（自动禁用，P1-18）', () => {
  const withItems = (items: AdminAuditItem[]) => {
    vi.mocked(adminApi.fetchAuditPage).mockResolvedValue({
      list: items,
      hasMore: false,
      nextCursor: null,
    });
  };

  it('系统自动执行的动作不显示 system 这个假账号', async () => {
    withItems([
      item({
        action: 'source.auto_disable',
        actorPhone: 'system',
        detail: { snapshot: { name: '旧源A', consecutive_failures: 7, last_error: 'HTTP 404' } },
      }),
    ]);
    const w = mountPage();
    await settle(w);

    expect(w.find('.admin__actor').text()).toBe('系统自动执行');
    expect(w.text()).not.toContain('由 system 操作');
    expect(w.text()).toContain('自动禁用源');
  });

  it('自动禁用自证原因：连续失败次数与最后错误都渲染出来', async () => {
    withItems([
      item({
        action: 'source.auto_disable',
        actorPhone: 'system',
        target: 'dead_src',
        detail: { snapshot: { name: '旧源A', consecutive_failures: 7, last_error: 'HTTP 404' } },
      }),
    ]);
    const w = mountPage();
    await settle(w);

    const changes = w.findAll('.admin__changes li').map((li) => li.text());
    expect(changes).toContain('旧源A');
    expect(changes).toContain('连续失败 7 次');
    expect(changes).toContain('HTTP 404');
  });

  it('人工操作仍显示手机号，两种来源不会混淆', async () => {
    withItems([item({ action: 'source.delete', actorPhone: '13800138000', detail: {} })]);
    const w = mountPage();
    await settle(w);
    expect(w.find('.admin__actor').text()).toBe('由 13800138000 操作');
    expect(w.text()).toContain('删除源');
  });

  it('未配置映射的动作退化成原样显示，而不是渲染成空白', async () => {
    withItems([item({ action: 'source.pinning', actorPhone: '13800138000', detail: {} })]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('source.pinning');
  });
});
