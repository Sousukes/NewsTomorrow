import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 关掉 mock 开关：测试环境没有 `VITE_API_BASE`，`USE_MOCK` 默认为 **true**，
 * 而 `ensureFreshToken` 在 mock 模式下直接短路（mock 没有真实续期接口可打）。
 * 不关掉它，本文件所有续期断言都会静默退化成"什么都没发生"。
 */
vi.mock('./config', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./config')>();
  return { ...actual, USE_MOCK: false };
});

import {
  UNAUTHORIZED_EVENT,
  clearSession,
  getRefreshToken,
  getToken,
  request,
  setSession,
  setToken,
} from './http';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('api/http 令牌双轨与自动续期（C1）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** 按 URL 路由的 fetch 替身：`/auth/refresh` 走续期，其余走 `main` */
  function routedFetch(
    main: () => Response,
    refresh: () => Response = () =>
      jsonResponse({ code: 0, data: { token: 'fresh', refreshToken: 'r2', expiresIn: 1800 } }),
  ) {
    return vi.fn(async (url: string) =>
      String(url).includes('/auth/refresh') ? refresh() : main(),
    );
  }

  const refreshCalls = (f: ReturnType<typeof vi.fn>) =>
    f.mock.calls.filter(([u]) => String(u).includes('/auth/refresh')).length;

  it('setSession 写入令牌对与到期时刻；clearSession 一并清空', () => {
    setSession({ token: 'a', refreshToken: 'b', expiresIn: 1800 });
    expect(getToken()).toBe('a');
    expect(getRefreshToken()).toBe('b');
    expect(localStorage.getItem('mingri-toutiao:token-exp')).toBeTruthy();

    clearSession();
    expect(getToken()).toBe('');
    expect(getRefreshToken()).toBe('');
  });

  it('没有 expiresIn 时不记到期时刻（不主动续期，交给 401 兜底）', () => {
    setSession({ token: 'a', refreshToken: 'b' });
    expect(localStorage.getItem('mingri-toutiao:token-exp')).toBeNull();
  });

  it('access 将到期时先续期再发请求，并发只打一次续期接口', async () => {
    setSession({ token: 'old', refreshToken: 'r', expiresIn: 10 });
    const f = routedFetch(() => jsonResponse({ code: 0, data: 'ok' }));
    vi.stubGlobal('fetch', f);

    await Promise.all([request('/t'), request('/t')]);

    // 单飞：两个请求同时发现将过期，也只换一次
    expect(refreshCalls(f)).toBe(1);
    expect(getToken()).toBe('fresh');
  });

  it('401 先用 refresh 令牌续期，再用新令牌重试原请求', async () => {
    setSession({ token: 'old', refreshToken: 'r', expiresIn: 1800 });
    let first = true;
    const f = routedFetch(() => {
      if (first) {
        first = false;
        return jsonResponse({ message: '未登录或登录已过期' }, 401);
      }
      return jsonResponse({ code: 0, data: 'ok' });
    });
    vi.stubGlobal('fetch', f);

    await expect(request('/t')).resolves.toBe('ok');

    // 重试那次必须带**新**令牌，否则等于白续
    const lastCall = f.mock.calls.at(-1) as unknown as
      [string, RequestInit | undefined] | undefined;
    const headers = lastCall?.[1]?.headers as Record<string, string> | undefined;
    expect(headers?.Authorization).toBe('Bearer fresh');
  });

  it('续期后仍 401 → 不再重试，清理令牌对并广播事件', async () => {
    setSession({ token: 'old', refreshToken: 'r', expiresIn: 1800 });
    const spy = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, spy);

    const f = routedFetch(() => jsonResponse({ message: '未登录' }, 401));
    vi.stubGlobal('fetch', f);

    await expect(request('/t')).rejects.toMatchObject({ status: 401 });

    // 关键：只续一次。少了这个守卫，「续期成功但服务端仍 401」会变成无限循环
    expect(refreshCalls(f)).toBe(1);
    expect(getToken()).toBe('');
    expect(getRefreshToken()).toBe('');
    expect(spy).toHaveBeenCalledTimes(1);
    window.removeEventListener(UNAUTHORIZED_EVENT, spy);
  });

  it('refresh 令牌失效 → 清理登录态并广播（不留半登录态）', async () => {
    setSession({ token: 'old', refreshToken: 'r', expiresIn: 1800 });
    const spy = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, spy);

    const f = routedFetch(
      () => jsonResponse({ message: '未登录或登录已过期' }, 401),
      () => jsonResponse({ message: '刷新令牌失效' }, 401),
    );
    vi.stubGlobal('fetch', f);

    await expect(request('/t')).rejects.toMatchObject({ status: 401 });
    expect(getToken()).toBe('');
    expect(getRefreshToken()).toBe('');
    expect(spy).toHaveBeenCalledTimes(1);
    window.removeEventListener(UNAUTHORIZED_EVENT, spy);
  });

  it('没有 refresh 令牌时遇到 401 直接兜底，不打续期接口', async () => {
    // 老客户端 / 游客：只有 access，没有长令牌可换
    setToken('only-access');
    const f = routedFetch(() => jsonResponse({ message: '未登录' }, 401));
    vi.stubGlobal('fetch', f);

    await expect(request('/t')).rejects.toMatchObject({ status: 401 });
    expect(refreshCalls(f)).toBe(0);
    expect(getToken()).toBe('');
  });
});
