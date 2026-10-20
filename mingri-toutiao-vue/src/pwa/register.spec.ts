import { describe, expect, it, vi } from 'vitest';

/**
 * PWA 注册器（E8 / P1-78）。
 *
 * 三条守卫是本文件的全部重点——它们各自对应一个真实的崩溃场景：
 * - **dev 不注册**：dev 的模块按需加载，precache 会缓存半批模块，
 *   症状是"改了代码却不生效"（这种锅极难归因到 SW 上）；
 * - **无 `navigator.serviceWorker` 不注册**：jsdom / 旧浏览器会 ReferenceError；
 * - **非安全上下文不注册**：浏览器本身禁止，register() 会 reject，
 *   不 catch 就是一条未处理的 Promise 拒绝。
 */

/** 带参数的 register mock：否则 vi.fn 的实现函数是 0 参，`mock.calls[0][0]` 取不到值 */
const regMock = (waiting: unknown = null) =>
  vi.fn(async (_url: unknown, _opts?: unknown) => ({
    scope: '/',
    addEventListener: vi.fn(),
    waiting,
  }));

function installNavigator(sw: unknown) {
  Object.defineProperty(navigator, 'serviceWorker', { value: sw, configurable: true });
}

const NO_SW = undefined;

describe('pwa/register', () => {
  it('jsdom（无 serviceWorker）返回空实现且不抛', async () => {
    vi.resetModules();
    installNavigator(NO_SW);
    vi.stubEnv('PROD', true);
    const { registerServiceWorker } = await import('./register');
    const h = registerServiceWorker();
    expect(h.available()).toBe(false);
    expect(() => h.refresh()).not.toThrow();
  });

  it('dev 下不注册（否则 dev 的半批模块会被缓存，改代码不生效）', async () => {
    vi.resetModules();
    const register = regMock();
    installNavigator({ register, addEventListener: vi.fn(), controller: null });
    vi.stubEnv('PROD', false);
    const { registerServiceWorker } = await import('./register');
    registerServiceWorker();
    expect(register).not.toHaveBeenCalled();
  });

  it('prod + 有 SW 时注册 /sw.js，作用域为整站', async () => {
    vi.resetModules();
    const register = regMock();
    installNavigator({ register, addEventListener: vi.fn(), controller: null });
    vi.stubEnv('PROD', true);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    const { registerServiceWorker } = await import('./register');
    registerServiceWorker();
    await vi.waitFor(() => expect(register).toHaveBeenCalledTimes(1));
    expect(register.mock.calls[0][0]).toBe('/sw.js');
    expect(register.mock.calls[0][1]).toEqual({ scope: '/' });
  });

  it('非安全上下文（http）不注册、不抛', async () => {
    vi.resetModules();
    const register = regMock();
    installNavigator({ register, addEventListener: vi.fn(), controller: null });
    vi.stubEnv('PROD', true);
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    const { registerServiceWorker } = await import('./register');
    const h = registerServiceWorker();
    expect(register).not.toHaveBeenCalled();
    expect(h.available()).toBe(false);
  });

  it('注册失败被捕获，且应用不受影响（离线缓存是增强不是前提）', async () => {
    vi.resetModules();
    const register = vi.fn(async (_url: unknown, _opts?: unknown) => {
      throw new Error('nope');
    });
    installNavigator({ register, addEventListener: vi.fn(), controller: null });
    vi.stubEnv('PROD', true);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { registerServiceWorker } = await import('./register');
    expect(() => registerServiceWorker()).not.toThrow();
    await vi.waitFor(() => expect(warn).toHaveBeenCalled());
    warn.mockRestore();
  });

  it('已有 waiting 且页面已被 SW 控制 → 提示有新版本', async () => {
    vi.resetModules();
    const waiting = { postMessage: vi.fn() };
    const register = vi.fn(async () => ({
      scope: '/',
      addEventListener: vi.fn(),
      waiting,
    }));
    installNavigator({ register, addEventListener: vi.fn(), controller: { scriptURL: '/sw.js' } });
    vi.stubEnv('PROD', true);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    const { registerServiceWorker } = await import('./register');
    const h = registerServiceWorker();
    await vi.waitFor(() => expect(h.available()).toBe(true));
  });

  it('点刷新 → 通知 SW 换版本并重载（只重载一次）', async () => {
    vi.resetModules();
    const waiting = { postMessage: vi.fn() };
    const register = regMock(waiting);
    installNavigator({ register, addEventListener: vi.fn(), controller: { scriptURL: '/sw.js' } });
    vi.stubEnv('PROD', true);
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    const { registerServiceWorker } = await import('./register');
    const h = registerServiceWorker();
    await vi.waitFor(() => expect(h.available()).toBe(true));
    h.refresh();
    expect(waiting.postMessage).toHaveBeenCalledWith('SKIP_WAITING');
  });
});
