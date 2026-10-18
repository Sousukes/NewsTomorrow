import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, type VueWrapper } from '@vue/test-utils';

/**
 * 登录视图：本地校验顺序、提交失败的可读文案、以及"登录成功后跳转"。
 *
 * 关注点是**失败可读性**：锁定策略（C2）上线后，错误信息多半是后端给的
 * "账号已锁定，请 N 秒后重试"。如果视图把它吞掉换成"登录失败"，
 * 用户就只能反复试——而那正是锁定策略最不希望发生的行为。
 * 所以这里断言**后端文案原样透传**，而不是断言某个固定的兜底文案。
 */

const login = vi.fn(async () => undefined);
const userState = { login };
vi.mock('@/stores/user', () => ({ useUserStore: () => userState }));

const routerPush = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }));

import Login from './Login.vue';

const mountPage = () => mount(Login);
const settle = async (w: VueWrapper) => {
  await new Promise((r) => setTimeout(r, 0));
  await w.vm.$nextTick();
};

const vm = (w: VueWrapper) =>
  w.vm as unknown as {
    phone: string;
    password: string;
    error: string;
    failField: '' | 'phone' | 'password';
    submitting: boolean;
    submit: () => Promise<void>;
  };

async function submitWith(w: VueWrapper, phone: string, password: string) {
  const c = vm(w);
  c.phone = phone;
  c.password = password;
  await c.submit();
  await settle(w);
}

beforeEach(() => {
  vi.clearAllMocks();
  login.mockResolvedValue(undefined);
});

describe('Login', () => {
  it('手机号不合法时本地拦住，不发请求', async () => {
    const w = mountPage();
    await submitWith(w, '138', '123456');
    expect(login).not.toHaveBeenCalled();
    expect(vm(w).error).toContain('手机号');
    expect(vm(w).failField).toBe('phone');
  });

  it('密码短于 6 位时本地拦住，错误指向密码框', async () => {
    const w = mountPage();
    await submitWith(w, '13800138000', '123');
    expect(login).not.toHaveBeenCalled();
    expect(vm(w).error).toContain('6');
    expect(vm(w).failField).toBe('password');
  });

  it('手机号与密码都合法时调用 store 并跳首页', async () => {
    const w = mountPage();
    await submitWith(w, '13800138000', '123456');
    expect(login).toHaveBeenCalledWith('13800138000', '123456');
    expect(routerPush).toHaveBeenCalledWith('/');
    expect(vm(w).error).toBe('');
  });

  it('后端错误文案原样透传（锁定提示不能被兜底文案吞掉）', async () => {
    login.mockRejectedValueOnce(new Error('账号已锁定，请 480 秒后重试'));
    const w = mountPage();
    await submitWith(w, '13800138000', '123456');
    expect(vm(w).error).toBe('账号已锁定，请 480 秒后重试');
    expect(vm(w).failField).toBe('password');
    expect(routerPush).not.toHaveBeenCalled();
  });

  it('非 Error 异常也给出可读兜底，不把 [object Object] 显示给用户', async () => {
    login.mockRejectedValueOnce('字符串异常');
    const w = mountPage();
    await submitWith(w, '13800138000', '123456');
    expect(vm(w).error).not.toContain('[object');
    expect(vm(w).error.length).toBeGreaterThan(0);
  });

  it('提交失败后 submitting 复位（否则按钮永久禁用）', async () => {
    login.mockRejectedValueOnce(new Error('登录失败'));
    const w = mountPage();
    await submitWith(w, '13800138000', '123456');
    expect(vm(w).submitting).toBe(false);
  });

  it('二次提交时按正确顺序校验：先手机号再密码', async () => {
    const w = mountPage();
    await submitWith(w, 'abc', '1');
    // 两个都不合法时应先报手机号
    expect(vm(w).failField).toBe('phone');
  });
});
