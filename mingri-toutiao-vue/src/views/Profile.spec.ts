import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';

// 昵称编辑只依赖 api/me 的 updateNickname 与 user store；两者都打桩，
// store 自身的持久化行为由 stores/user.spec.ts 覆盖
vi.mock('@/api/me', () => ({
  updateNickname: vi.fn(async () => ({
    name: '',
    result: 'pass',
    reason: '',
    pendingReview: false,
  })),
}));

const userState = {
  isLoggedIn: true,
  isAdmin: false,
  name: '旧名字',
  favorites: [] as string[],
  history: [] as string[],
  theme: 'system',
  setTheme: vi.fn(),
  setName: vi.fn(),
  logout: vi.fn(async () => undefined),
};
vi.mock('@/stores/user', () => ({
  useUserStore: () => userState,
}));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
}));

import Profile from './Profile.vue';
import * as meApi from '@/api/me';

const mountPage = () => mount(Profile);
const settle = async (w: ReturnType<typeof mountPage>) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const btn = (w: ReturnType<typeof mountPage>, text: string) =>
  w.findAll('button').find((b) => b.text() === text)!;

beforeEach(() => {
  vi.clearAllMocks();
  routerPush.mockClear();
  userState.isLoggedIn = true;
  userState.isAdmin = false;
  userState.name = '旧名字';
  vi.mocked(meApi.updateNickname).mockResolvedValue({
    name: '新名字',
    result: 'pass',
    reason: '',
    pendingReview: false,
  });
});

describe('views/Profile 昵称展示', () => {
  it('未登录显示「未登录」，不提供改名入口', async () => {
    userState.isLoggedIn = false;
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('未登录');
    expect(w.find('button[aria-label="修改昵称"]').exists()).toBe(false);
  });

  it('已登录显示服务端下发的昵称', async () => {
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('旧名字');
    expect(w.find('button[aria-label="修改昵称"]').exists()).toBe(true);
  });

  it('昵称为空时回落默认名', async () => {
    userState.name = '';
    const w = mountPage();
    await settle(w);
    expect(w.text()).toContain('明日头条用户');
  });
});

describe('views/Profile 修改昵称', () => {
  it('点「修改」打开编辑器并预填当前昵称', async () => {
    const w = mountPage();
    await settle(w);

    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    const input = w.find('input[aria-label="新昵称"]');
    expect(input.exists()).toBe(true);
    expect((input.element as HTMLInputElement).value).toBe('旧名字');
    expect(w.text()).toContain('3/24');
  });

  it('保存成功（pass）：trim 后提交、更新 store、提示并收起编辑器', async () => {
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    await w.find('input[aria-label="新昵称"]').setValue('  新名字  ');
    await btn(w, '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(meApi.updateNickname).toHaveBeenCalledWith('新名字');
    expect(userState.setName).toHaveBeenCalledWith('新名字');
    expect(w.text()).toContain('昵称已保存');
    expect(w.find('input[aria-label="新昵称"]').exists()).toBe(false);
  });

  it('保存成功（review）：明确告知用户「将通过人工复核后展示」', async () => {
    vi.mocked(meApi.updateNickname).mockResolvedValue({
      name: '广告哥',
      result: 'review',
      reason: '包含广告内容，需人工复核',
      pendingReview: true,
    });
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    await w.find('input[aria-label="新昵称"]').setValue('广告哥');
    await btn(w, '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(userState.setName).toHaveBeenCalledWith('广告哥');
    expect(w.text()).toContain('昵称已保存，将通过人工复核后展示');
  });

  it('被拦截（40006）：展示后端文案（不回显命中词），不更新 store，编辑器保持打开', async () => {
    vi.mocked(meApi.updateNickname).mockRejectedValue(new Error('昵称包含违规内容，请更换后重试'));
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    await w.find('input[aria-label="新昵称"]').setValue('坏蛋');
    await btn(w, '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('昵称包含违规内容，请更换后重试');
    expect(userState.setName).not.toHaveBeenCalled();
    expect(w.find('input[aria-label="新昵称"]').exists()).toBe(true);
  });

  it('形状问题（42203）：后端文案原样展示', async () => {
    vi.mocked(meApi.updateNickname).mockRejectedValue(new Error('昵称至少需要 2 个字符'));
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    await w.find('input[aria-label="新昵称"]').setValue('a');
    await btn(w, '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(w.text()).toContain('昵称至少需要 2 个字符');
  });

  it('空白输入被前端拦下，不发请求', async () => {
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();

    await w.find('input[aria-label="新昵称"]').setValue('   ');
    await btn(w, '保存').trigger('click');
    await new Promise((r) => setTimeout(r, 0));
    await w.vm.$nextTick();

    expect(meApi.updateNickname).not.toHaveBeenCalled();
    expect(w.text()).toContain('昵称不能为空');
  });

  it('取消只收起编辑器，不发请求', async () => {
    const w = mountPage();
    await settle(w);
    await w.find('button[aria-label="修改昵称"]').trigger('click');
    await w.vm.$nextTick();
    await btn(w, '取消').trigger('click');
    await w.vm.$nextTick();

    expect(w.find('input[aria-label="新昵称"]').exists()).toBe(false);
    expect(meApi.updateNickname).not.toHaveBeenCalled();
  });
});

describe('views/Profile 管理入口', () => {
  it('管理员可见昵称敏感词与昵称复核两个入口并可跳转', async () => {
    userState.isAdmin = true;
    const w = mountPage();
    await settle(w);

    const wordsBtn = w.findAll('button').find((b) => b.text().includes('昵称敏感词'))!;
    const reviewsBtn = w.findAll('button').find((b) => b.text().includes('昵称复核'))!;
    expect(wordsBtn.exists()).toBe(true);
    expect(reviewsBtn.exists()).toBe(true);

    await wordsBtn.trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/nickname-words');
    await reviewsBtn.trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/nickname-reviews');
  });

  it('非管理员看不到昵称管理入口', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await settle(w);
    expect(w.text()).not.toContain('昵称敏感词');
    expect(w.text()).not.toContain('昵称复核');
  });

  it('E7：管理员可见「运营大盘」与「举报复核」并跳转（举报不再是黑洞）', async () => {
    userState.isAdmin = true;
    const w = mountPage();
    await settle(w);

    const dashBtn = w.findAll('button').find((b) => b.text().includes('运营大盘'))!;
    const reportsBtn = w.findAll('button').find((b) => b.text().includes('举报复核'))!;
    expect(dashBtn.exists()).toBe(true);
    expect(reportsBtn.exists()).toBe(true);

    await dashBtn.trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin');
    await reportsBtn.trigger('click');
    expect(routerPush).toHaveBeenCalledWith('/admin/reports');
  });

  it('E7：非管理员看不到大盘与举报入口', async () => {
    userState.isAdmin = false;
    const w = mountPage();
    await settle(w);
    expect(w.text()).not.toContain('运营大盘');
    expect(w.text()).not.toContain('举报复核');
  });
});
