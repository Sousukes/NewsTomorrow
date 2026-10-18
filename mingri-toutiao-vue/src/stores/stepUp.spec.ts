import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

/**
 * 二次校验 store（C10）——**安全链路，此前 0% 覆盖**。
 *
 * 它承担的是「挂起的高敏请求由谁放行」，所以每个分支都关系到安全语义：
 * - 验证成功 → `resolveStepUp(true)`：把**等待中的那个请求**放行；
 * - 取消 → `resolveStepUp(false)`：按原错误抛出，**不能**当成功；
 * - 失败 → 留在弹窗里显示原因（锁定文案要原样透传，否则用户只会反复试）。
 *
 * 打桩 `@/api/auth` 与 `@/api/http` 的 `resolveStepUp`：这里要验的是**放行了什么**，
 * 而不是 `http.ts` 内部实现（那部分由 `http` 自己的约定保证）。
 */

vi.mock('@/api/config', () => ({ USE_MOCK: false, API_BASE: '/api' }));

const resolveStepUp = vi.fn();
vi.mock('@/api/http', () => ({ resolveStepUp: (v: boolean) => resolveStepUp(v) }));

const apiStepUp = vi.fn();
vi.mock('@/api/auth', () => ({ stepUp: (p: string) => apiStepUp(p) }));

import { useStepUpStore } from '@/stores/stepUp';

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  apiStepUp.mockResolvedValue(true);
});

describe('stepUp store', () => {
  it('open 清空上次的密码与错误（否则会把上次输错的内容再送给后端）', () => {
    const s = useStepUpStore();
    s.password = '上次的密码';
    s.error = '上次的错误';
    s.open();
    expect(s.visible).toBe(true);
    expect(s.password).toBe('');
    expect(s.error).toBe('');
  });

  it('验证成功：关闭弹窗并放行挂起的请求', async () => {
    const s = useStepUpStore();
    s.open();
    s.password = 'right';
    await s.submit();
    expect(apiStepUp).toHaveBeenCalledWith('right');
    expect(s.visible).toBe(false);
    expect(resolveStepUp).toHaveBeenCalledWith(true);
  });

  it('取消：放行但标记"未取得令牌"，绝不能当成功', () => {
    const s = useStepUpStore();
    s.open();
    s.cancel();
    expect(s.visible).toBe(false);
    expect(resolveStepUp).toHaveBeenCalledWith(false);
    expect(resolveStepUp).not.toHaveBeenCalledWith(true);
  });

  it('后端说"没有令牌"（返回 false）：留在弹窗并提示重试', async () => {
    apiStepUp.mockResolvedValueOnce(false);
    const s = useStepUpStore();
    s.open();
    s.password = 'x';
    await s.submit();
    expect(s.visible).toBe(true);
    expect(s.error).toContain('重试');
    expect(resolveStepUp).not.toHaveBeenCalled();
  });

  it('锁定文案原样透传（否则用户看不出是被锁了，只会反复试）', async () => {
    apiStepUp.mockRejectedValueOnce(new Error('连续失败次数过多，请 480 秒后重试'));
    const s = useStepUpStore();
    s.open();
    s.password = 'wrong';
    await s.submit();
    expect(s.error).toBe('连续失败次数过多，请 480 秒后重试');
    expect(s.visible).toBe(true);
    expect(resolveStepUp).not.toHaveBeenCalled();
  });

  it('非 Error 异常也有可读兜底，不把 [object Object] 显示给用户', async () => {
    apiStepUp.mockRejectedValueOnce({ detail: 'x' });
    const s = useStepUpStore();
    s.open();
    s.password = 'wrong';
    await s.submit();
    expect(s.error).not.toContain('[object');
    expect(s.error.length).toBeGreaterThan(0);
  });

  it('失败后 submitting 复位（否则按钮永久禁用，用户以为卡死）', async () => {
    apiStepUp.mockRejectedValueOnce(new Error('密码错误'));
    const s = useStepUpStore();
    s.open();
    await s.submit();
    expect(s.submitting).toBe(false);
  });

  it('重复提交被挡住：只发一次校验请求（防连点刷爆锁定计数）', async () => {
    let release: (v: boolean) => void = () => {};
    apiStepUp.mockImplementationOnce(() => new Promise<boolean>((r) => (release = r)));
    const s = useStepUpStore();
    s.open();
    const first = s.submit();
    const second = s.submit();
    release(true);
    await Promise.all([first, second]);
    await flush();
    expect(apiStepUp).toHaveBeenCalledTimes(1);
    // 第二次被挡下也不能把状态弄坏
    expect(s.submitting).toBe(false);
    expect(resolveStepUp).toHaveBeenCalledTimes(1);
  });

  it('close 只关弹窗，不主动放行（请求继续挂着，语义由调用方决定）', () => {
    const s = useStepUpStore();
    s.open();
    s.close();
    expect(s.visible).toBe(false);
    expect(resolveStepUp).not.toHaveBeenCalled();
  });
});
