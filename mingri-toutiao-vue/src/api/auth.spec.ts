import { beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 认证 API 层（`src/api/auth.ts`）——此前 0% 覆盖。
 *
 * 这一层是**令牌的唯一入口**，四个函数各有一条容易写错的边界：
 * - `login`：只有拿到 token 才 `setSession`，否则会写进一个空会话；
 * - `logout`：**请求失败也必须清本地**（服务端吊销失败不能留下一张可用的长令牌）；
 * - `stepUp`：没有 `stepUpToken` 要返回 false（不能假装成功放行高敏操作）；
 * - `changePassword`：成功后**必须立刻写入新的一对令牌**，否则用户改完密被自己踢下线。
 *
 * 打桩 `./http` 的四个出口，验的是「这层怎么用它们」，而不是 http 内部实现。
 * `USE_MOCK: false` 必须打桩——否则 mock 分支会短路一切（且 `vi.mock` 是文件级，
 * 不能和别的用例共用文件）。
 */

vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

const request = vi.fn();
const setSession = vi.fn();
const clearSession = vi.fn();
const setStepUpToken = vi.fn();
vi.mock('@/api/http', () => ({
  request: (...args: unknown[]) => request(...args),
  setSession: (v: unknown) => setSession(v),
  clearSession: () => clearSession(),
  setStepUpToken: (t: string, e?: number) => setStepUpToken(t, e),
  mockDelay: async () => undefined,
}));

import { changePassword, login, logout, stepUp } from '@/api/auth';

const session = {
  token: 'access-1',
  refreshToken: 'refresh-1',
  expiresIn: 1800,
  user: { id: 1, name: '甲' },
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('login', () => {
  it('拿到 token 时写入完整会话（含 refreshToken 与到期时间）', async () => {
    request.mockResolvedValueOnce(session);
    const out = await login({ phone: '13800138000', password: 'x' });
    expect(setSession).toHaveBeenCalledWith(session);
    expect(out).toBe(session);
  });

  it('响应没有 token 时不写会话（避免写入空会话把用户"登录"成空壳）', async () => {
    request.mockResolvedValueOnce({ user: { name: '甲' } });
    await login({ phone: '13800138000', password: 'x' });
    expect(setSession).not.toHaveBeenCalled();
  });

  it('把手机号与密码原样交给请求层', async () => {
    request.mockResolvedValueOnce(session);
    await login({ phone: '13800138000', password: 'p@ss' });
    const [path, opts] = request.mock.calls[0] as [string, { method: string; body: unknown }];
    expect(path).toBe('/auth/login');
    expect(opts.method).toBe('POST');
    expect(opts.body).toEqual({ phone: '13800138000', password: 'p@ss' });
  });
});

describe('logout', () => {
  it('正常退出会清本地会话', async () => {
    request.mockResolvedValueOnce(undefined);
    await logout();
    expect(clearSession).toHaveBeenCalled();
  });

  it('**服务端吊销失败也要清本地**（否则留下一张可用的长令牌）', async () => {
    request.mockRejectedValueOnce(new Error('网络断了'));
    await expect(logout()).rejects.toThrow('网络断了');
    expect(clearSession).toHaveBeenCalled();
  });
});

describe('stepUp', () => {
  it('拿到令牌：保存令牌并返回 true', async () => {
    request.mockResolvedValueOnce({ stepUpToken: 'su-1', expiresIn: 300 });
    await expect(stepUp('pwd')).resolves.toBe(true);
    expect(setStepUpToken).toHaveBeenCalledWith('su-1', 300);
  });

  it('响应里没有令牌：返回 false 且不保存（不能假装成功放行高敏操作）', async () => {
    request.mockResolvedValueOnce({ expiresIn: 300 });
    await expect(stepUp('pwd')).resolves.toBe(false);
    expect(setStepUpToken).not.toHaveBeenCalled();
  });

  it('失败向上抛（由对话框展示锁定/密码错）', async () => {
    request.mockRejectedValueOnce(new Error('账号已锁定'));
    await expect(stepUp('pwd')).rejects.toThrow('账号已锁定');
  });
});

describe('changePassword', () => {
  it('成功后立刻写入新令牌对（否则用户改完密被自己踢下线）', async () => {
    const next = { ...session, token: 'access-2', refreshToken: 'refresh-2' };
    request.mockResolvedValueOnce(next);
    const out = await changePassword('old', 'new');
    expect(setSession).toHaveBeenCalledWith(next);
    expect(out).toBe(next);
  });

  it('携带旧密码与新密码（后端两者都校验）', async () => {
    request.mockResolvedValueOnce(session);
    await changePassword('old-pass', 'new-pass');
    const [path, opts] = request.mock.calls[0] as [string, { method: string; body: unknown }];
    expect(path).toBe('/auth/password');
    expect(opts.method).toBe('PUT');
    expect(opts.body).toEqual({ oldPassword: 'old-pass', newPassword: 'new-pass' });
  });
});
