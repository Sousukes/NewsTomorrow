import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * 关掉 mock 开关：测试环境没有 `VITE_API_BASE`，`USE_MOCK` 默认为 **true**，
 * 而 40304 的拦截流程只在真实请求链路上发生（同 `http.refresh.spec.ts` 的理由）。
 */
vi.mock('./config', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./config')>();
  return { ...actual, USE_MOCK: false };
});

import {
  STEP_UP_REQUIRED_CODE,
  STEP_UP_REQUIRED_EVENT,
  clearSession,
  getStepUpToken,
  request,
  resolveStepUp,
  setStepUpToken,
  setToken,
} from './http';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe('api/http 高敏操作二次校验（C10）', () => {
  beforeEach(() => {
    localStorage.clear();
    clearSession();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('40304 → 弹窗 → 验证通过后带新令牌重试', async () => {
    setToken('access');
    let first = true;
    const f = vi.fn(async () => {
      if (first) {
        first = false;
        return jsonResponse(
          { code: STEP_UP_REQUIRED_CODE, message: '此操作需要二次校验，请先验证身份' },
          403,
        );
      }
      return jsonResponse({ code: 0, data: 'ok' });
    });
    vi.stubGlobal('fetch', f);

    const events: string[] = [];
    const onRequired = () => {
      events.push('required');
      // 模拟对话框验证成功（store 内部会调 setStepUpToken）
      setStepUpToken('su-token', 300);
      resolveStepUp(true);
    };
    window.addEventListener(STEP_UP_REQUIRED_EVENT, onRequired);

    await expect(request('/t')).resolves.toBe('ok');
    expect(events).toEqual(['required']);

    // 重试那次必须带二次校验令牌，否则等于白验证
    const lastCall = f.mock.calls.at(-1) as unknown as [string, RequestInit | undefined];
    const headers = lastCall?.[1]?.headers as Record<string, string> | undefined;
    expect(headers?.['X-Step-Up-Token']).toBe('su-token');
    expect(getStepUpToken()).toBe('su-token');

    window.removeEventListener(STEP_UP_REQUIRED_EVENT, onRequired);
  });

  it('40304 且用户取消 → 原样抛出，不再重试', async () => {
    setToken('access');
    const f = vi.fn(async () =>
      jsonResponse({ code: STEP_UP_REQUIRED_CODE, message: '需要二次校验' }, 403),
    );
    vi.stubGlobal('fetch', f);

    const onRequired = () => resolveStepUp(false);
    window.addEventListener(STEP_UP_REQUIRED_EVENT, onRequired);

    await expect(request('/t')).rejects.toMatchObject({ status: 403 });
    expect(f).toHaveBeenCalledTimes(1);

    window.removeEventListener(STEP_UP_REQUIRED_EVENT, onRequired);
  });

  it('已有 step-up 令牌时直接带头，不弹窗', async () => {
    setToken('access');
    setStepUpToken('su-token', 300);
    const f = vi.fn(async () => jsonResponse({ code: 0, data: 'ok' }));
    vi.stubGlobal('fetch', f);
    const spy = vi.fn();
    window.addEventListener(STEP_UP_REQUIRED_EVENT, spy);

    await expect(request('/t')).resolves.toBe('ok');

    const headers = (f.mock.calls[0] as unknown as [string, RequestInit])[1]?.headers as Record<
      string,
      string
    >;
    expect(headers['X-Step-Up-Token']).toBe('su-token');
    expect(spy).not.toHaveBeenCalled();
    window.removeEventListener(STEP_UP_REQUIRED_EVENT, spy);
  });

  it('一次请求最多弹一次：验证后仍被拒 → 不再循环弹窗', async () => {
    setToken('access');
    const f = vi.fn(async () =>
      jsonResponse({ code: STEP_UP_REQUIRED_CODE, message: '需要二次校验' }, 403),
    );
    vi.stubGlobal('fetch', f);

    let pops = 0;
    const onRequired = () => {
      pops += 1;
      setStepUpToken('su-token', 300);
      resolveStepUp(true);
    };
    window.addEventListener(STEP_UP_REQUIRED_EVENT, onRequired);

    await expect(request('/t')).rejects.toMatchObject({ status: 403 });
    expect(pops).toBe(1);
    expect(f).toHaveBeenCalledTimes(2);

    window.removeEventListener(STEP_UP_REQUIRED_EVENT, onRequired);
  });

  it('普通 403（如 40301 权限不足）不触发二次校验', async () => {
    setToken('access');
    const f = vi.fn(async () => jsonResponse({ code: 40301, message: '无权限' }, 403));
    vi.stubGlobal('fetch', f);
    const spy = vi.fn();
    window.addEventListener(STEP_UP_REQUIRED_EVENT, spy);

    await expect(request('/t')).rejects.toMatchObject({ status: 403 });
    expect(spy).not.toHaveBeenCalled();
    window.removeEventListener(STEP_UP_REQUIRED_EVENT, spy);
  });

  it('clearSession 一并清掉 step-up 令牌（不留 5 分钟通行证）', () => {
    setToken('access');
    setStepUpToken('su-token', 300);
    expect(getStepUpToken()).toBe('su-token');
    clearSession();
    expect(getStepUpToken()).toBe('');
  });

  it('过期的 step-up 令牌自动失效', () => {
    setStepUpToken('su-token', -1);
    expect(getStepUpToken()).toBe('');
  });
});
