import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 修改密码页（C4）——此前 15.6% 覆盖。
 *
 * 本页唯一的值在**校验规则**与**受限期跳转**：
 * 规则漏一条，用户可能提交出"新旧相同"或"两次不一致"的密码；
 * 受限期改完不跳 `/`，路由守卫会把用户又弹回这个页面——死循环。
 * 这里用 `vm` 直接调 `onSubmit`，只测逻辑不测 DOM。
 */

const userState = {
  mustChangePassword: false,
  changePassword: vi.fn(async (_o: string, _n: string) => undefined),
};
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import ChangePassword from '@/views/ChangePassword.vue';
import { mount } from '@vue/test-utils';

const mountPage = () => mount(ChangePassword);
const vm = (w: ReturnType<typeof mountPage>) =>
  w.vm as unknown as {
    oldPassword: string;
    newPassword: string;
    confirmPassword: string;
    error: string;
    submitting: boolean;
    onSubmit: () => Promise<void>;
  };
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  vi.clearAllMocks();
  userState.mustChangePassword = false;
});

describe('ChangePassword 校验', () => {
  it.each([
    ['', 'abc123', 'abc123', '请输入当前密码'],
    ['old', '123', '123', '新密码至少 6 位'],
    ['old', 'abc123', 'abc124', '两次输入的新密码不一致'],
    ['samepw', 'samepw', 'samepw', '新密码不能与当前密码相同'],
  ])('非法输入 %j 不提交并给出对应提示', async (old, nw, cf, msg) => {
    const w = mountPage();
    const c = vm(w);
    c.oldPassword = old;
    c.newPassword = nw;
    c.confirmPassword = cf;
    await c.onSubmit();
    expect(userState.changePassword).not.toHaveBeenCalled();
    expect(c.error).toBe(msg);
  });

  it('合法输入提交后，主动改密跳回 /profile', async () => {
    const w = mountPage();
    const c = vm(w);
    c.oldPassword = 'old-pass';
    c.newPassword = 'new-pass';
    c.confirmPassword = 'new-pass';
    await c.onSubmit();
    expect(userState.changePassword).toHaveBeenCalledWith('old-pass', 'new-pass');
    expect(routerPush).toHaveBeenCalledWith('/profile');
  });

  it('受限期（管理员代重置）改完跳 /，否则守卫会把它弹回来', async () => {
    userState.mustChangePassword = true;
    const w = mountPage();
    const c = vm(w);
    c.oldPassword = 'temp-pass';
    c.newPassword = 'new-pass';
    c.confirmPassword = 'new-pass';
    await c.onSubmit();
    expect(routerPush).toHaveBeenCalledWith('/');
  });

  it('后端拒绝时展示错误且复位 submitting', async () => {
    userState.changePassword.mockRejectedValueOnce(new Error('密码错误'));
    const w = mountPage();
    const c = vm(w);
    c.oldPassword = 'a';
    c.newPassword = 'abc123';
    c.confirmPassword = 'abc123';
    await c.onSubmit();
    await flush();
    expect(c.error).toBe('密码错误');
    expect(c.submitting).toBe(false);
  });
});
