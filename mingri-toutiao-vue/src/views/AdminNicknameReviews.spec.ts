import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 复核队列没有 mock 短路：整条链路打桩在 api 层（与 AdminNicknameWords.spec.ts 同思路）
vi.mock('@/api/admin', () => ({
  fetchNicknameReviews: vi.fn(async () => ({ list: [], total: 0 })),
  reviewNickname: vi.fn(async () => ({
    userId: 0,
    name: '',
    phone: '',
    pendingReview: false,
    reviewedAt: null,
  })),
}));

const userState = { isLoggedIn: true, isAdmin: true };
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import AdminNicknameReviews from './AdminNicknameReviews.vue';
import * as adminApi from '@/api/admin';
import type { NicknameReviewItem } from '@/types';

const review = (over: Partial<NicknameReviewItem> = {}): NicknameReviewItem => ({
  userId: 7,
  name: '坏蛋哥',
  phone: '13800138000',
  flaggedAt: '2026-10-15T11:20:00',
  flaggedName: '坏蛋哥',
  flaggedMatched: '坏蛋',
  ...over,
});

const mockReviews = (rows: NicknameReviewItem[], total = rows.length) =>
  vi.mocked(adminApi.fetchNicknameReviews).mockResolvedValue({ list: rows, total });

const mountPage = () => mount(AdminNicknameReviews);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

/** 队列行（li）——按 data-user 限定 */
const reviewRows = (w: ReturnType<typeof mountPage>) => w.findAll('li[data-user]');

const rowBtn = (row: ReturnType<typeof reviewRows>[number], text: string) =>
  row.findAll('button').find((b) => b.text() === text)!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = true;
  mockReviews([]);
  vi.mocked(adminApi.reviewNickname).mockResolvedValue({
    userId: 7,
    name: '坏蛋哥',
    phone: '13800138000',
    pendingReview: false,
    reviewedAt: '2026-10-15T12:00:00',
  });
});

describe('views/AdminNicknameReviews', () => {
  it('非管理员不请求队列，直接展示无权限态', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameReviews).not.toHaveBeenCalled();
    expect(w.text()).toContain('无权限管理昵称复核');
  });

  it('管理员加载后渲染队列：当前昵称、待复核、手机号、当初提交与命中', async () => {
    mockReviews([review()], 1);
    const w = mountPage();
    await settle(w);

    const row = reviewRows(w)[0];
    expect(row.text()).toContain('坏蛋哥');
    expect(row.text()).toContain('待复核');
    expect(row.text()).toContain('13800138000');
    expect(row.text()).toContain('当初提交：坏蛋哥');
    expect(row.text()).toContain('命中「坏蛋」');
    expect(row.text()).toContain('2026-10-15 11:20');
    expect(w.text()).toContain('共 1 条');
  });

  it('当前昵称与当初提交不同时（用户又改过名）两者都展示', async () => {
    mockReviews([review({ name: '新名字', flaggedName: '坏蛋哥', flaggedMatched: null })], 1);
    const w = mountPage();
    await settle(w);

    const row = reviewRows(w)[0];
    expect(row.text()).toContain('新名字');
    expect(row.text()).toContain('当初提交：坏蛋哥');
    // 没有命中模式记录时不渲染命中行，而不是留个空「命中「」」
    expect(row.text()).not.toContain('命中「');
  });

  it('后端 403 时展示无权限态', async () => {
    vi.mocked(adminApi.fetchNicknameReviews).mockRejectedValue(new Error('无权限执行该操作'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('无权限管理昵称复核');
  });

  it('加载失败（非 403）时显示错误横幅，不伪装成无权限', async () => {
    vi.mocked(adminApi.fetchNicknameReviews).mockRejectedValue(new Error('网络错误'));
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('网络错误');
    expect(w.text()).not.toContain('无权限管理昵称复核');
  });
});

/* ------------------------------ 筛选 ------------------------------ */

describe('views/AdminNicknameReviews 筛选', () => {
  it('搜索回车带上 keyword 重新请求', async () => {
    mockReviews([review()]);
    const w = mountPage();
    await settle(w);

    const search = w.find('input[type="search"]');
    await search.setValue('138');
    await search.trigger('keyup.enter');
    await new Promise((r) => setTimeout(r, 0));
    expect(adminApi.fetchNicknameReviews).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '138' }),
    );
  });

  it('清除筛选复位状态**并重新拉取**（状态复位 ≠ 数据复位）', async () => {
    mockReviews([review()]);
    const w = mountPage();
    await settle(w);

    await w.find('input[type="search"]').setValue('坏蛋');
    await btn(w, '搜索').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    await btn(w, '清除筛选').trigger('click');
    await new Promise((r) => setTimeout(r, 0));

    expect(adminApi.fetchNicknameReviews).toHaveBeenLastCalledWith(
      expect.objectContaining({ keyword: '' }),
    );
    // 服务端筛选：清除后必须真的发过一次请求，不能只改本地状态
    expect(adminApi.fetchNicknameReviews).toHaveBeenCalledTimes(3);
  });

  it('空队列与筛选无结果是两种文案', async () => {
    mockReviews([]);
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('还没有待复核的昵称');

    await w.find('input[type="search"]').setValue('不存在');
    await btn(w, '搜索').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(w.text()).toContain('没有匹配的昵称');
    expect(w.text()).not.toContain('还没有待复核的昵称');
  });
});

/* ------------------------------ 复核 ------------------------------ */

describe('views/AdminNicknameReviews 通过复核', () => {
  it('复核成功：就地移出队列、递减计数并提示', async () => {
    mockReviews([review(), review({ userId: 8, name: '广告哥', flaggedName: '广告哥' })], 2);
    const w = mountPage();
    await settle(w);
    expect(reviewRows(w)).toHaveLength(2);

    await rowBtn(reviewRows(w)[0], '通过复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.reviewNickname).toHaveBeenCalledWith(7);
    expect(reviewRows(w)).toHaveLength(1);
    expect(w.text()).toContain('已通过复核：坏蛋哥');
    expect(w.text()).toContain('共 1 条');
  });

  it('复核中按钮置忙，连点不会发第二次请求', async () => {
    mockReviews([review()], 1);
    let resolve: ((v: unknown) => void) | undefined;
    vi.mocked(adminApi.reviewNickname).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r as (v: unknown) => void;
        }),
    );
    const w = mountPage();
    await settle(w);

    const button = rowBtn(reviewRows(w)[0], '通过复核');
    await button.trigger('click');
    await w.vm.$nextTick();
    // 请求在途：按钮变忙位，此时再点也只会命中守卫
    expect((rowBtn(reviewRows(w)[0], '复核中…').element as HTMLButtonElement).disabled).toBe(true);
    await rowBtn(reviewRows(w)[0], '复核中…').trigger('click');
    expect(adminApi.reviewNickname).toHaveBeenCalledTimes(1);

    resolve?.({
      userId: 7,
      name: '坏蛋哥',
      phone: '13800138000',
      pendingReview: false,
      reviewedAt: '2026-10-15T12:00:00',
    });
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();
    expect(reviewRows(w)).toHaveLength(0);
  });

  it('复核失败（40408 已复核过 / 用户不存在）时错误横幅，行不动', async () => {
    mockReviews([review()], 1);
    vi.mocked(adminApi.reviewNickname).mockRejectedValue(new Error('用户不存在或无需复核'));
    const w = mountPage();
    await settle(w);

    await rowBtn(reviewRows(w)[0], '通过复核').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('用户不存在或无需复核');
    expect(reviewRows(w)).toHaveLength(1);
    expect(w.text()).toContain('共 1 条');
  });
});

/* ------------------------------ 分页与入口 ------------------------------ */

describe('views/AdminNicknameReviews 分页与入口', () => {
  it('加载更多用 offset=已加载条数追加', async () => {
    vi.mocked(adminApi.fetchNicknameReviews)
      .mockResolvedValueOnce({ list: [review()], total: 3 })
      .mockResolvedValueOnce({
        list: [review({ userId: 8, name: '广告哥', flaggedMatched: null })],
        total: 3,
      });
    const w = mountPage();
    await settle(w);
    expect(reviewRows(w)).toHaveLength(1);

    await btn(w, '加载更多').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(adminApi.fetchNicknameReviews).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 1 }),
    );
    expect(reviewRows(w)).toHaveLength(2);
  });

  it('没有更多时不出加载更多按钮', async () => {
    mockReviews([review()], 1);
    const w = mountPage();
    await settle(w);
    expect(w.findAll('button').some((b) => b.text() === '加载更多')).toBe(false);
  });

  it('「历史」带 userId 进审计页', async () => {
    mockReviews([review()], 1);
    const w = mountPage();
    await settle(w);

    await rowBtn(reviewRows(w)[0], '历史').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/audit?target=7');
  });

  it('提供跳转词库的入口', async () => {
    mockReviews([review()], 1);
    const w = mountPage();
    await settle(w);

    await btn(w, '昵称敏感词').trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/nickname-words');
  });
});
