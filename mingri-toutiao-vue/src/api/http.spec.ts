import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UNAUTHORIZED_EVENT, getToken, request, setToken } from './http';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('api/http 加固（H6）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('解包 {code,data} 约定', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ code: 0, data: { x: 1 } })));
    await expect(request('/t')).resolves.toEqual({ x: 1 });
  });

  it('401 → 清除 token 并广播未授权事件', async () => {
    setToken('abc');
    const spy = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, spy);

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ message: '未登录' }, 401)));
    await expect(request('/t')).rejects.toMatchObject({ status: 401 });

    expect(getToken()).toBe('');
    expect(spy).toHaveBeenCalledTimes(1);

    window.removeEventListener(UNAUTHORIZED_EVENT, spy);
  });

  it('GET 网络错误按指数退避重试，最终成功', async () => {
    const f = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('network'))
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(jsonResponse({ code: 0, data: 'ok' }));
    vi.stubGlobal('fetch', f);

    await expect(request('/t', { retries: 2 })).resolves.toBe('ok');
    expect(f).toHaveBeenCalledTimes(3);
  });

  it('POST 不重试（非幂等）', async () => {
    const f = vi.fn().mockRejectedValue(new TypeError('network'));
    vi.stubGlobal('fetch', f);

    await expect(request('/t', { method: 'POST' })).rejects.toBeInstanceOf(TypeError);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('非 2xx 透传后端 message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ message: '手机号或密码错误' }, 401)),
    );
    await expect(request('/t')).rejects.toThrow('手机号或密码错误');
  });

  it('业务 code 非 0 时抛 ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ code: 40901, message: '已注册' })),
    );
    await expect(request('/t')).rejects.toMatchObject({ name: 'ApiError', message: '已注册' });
  });
});
