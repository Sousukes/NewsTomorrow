import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { applyTheme, useTheme, THEME_ATTR } from '@/composables/useTheme';
import { useUserStore } from '@/stores/user';

/** 模拟系统偏好（jsdom 的 matchMedia 默认恒为 false） */
function mockPrefersDark(value: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('dark') ? value : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    })),
  );
}

let stop: (() => void) | undefined;

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
  document.documentElement.removeAttribute(THEME_ATTR);
});

afterEach(() => {
  stop?.();
  stop = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useTheme', () => {
  it('applyTheme 把解析结果写到 <html data-theme>', () => {
    expect(applyTheme('dark')).toBe('dark');
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('dark');
    // 同步 color-scheme，让原生控件也跟随
    expect(document.documentElement.style.colorScheme).toBe('dark');

    expect(applyTheme('light')).toBe('light');
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('light');
  });

  it('system 模式按系统偏好解析', () => {
    mockPrefersDark(true);
    expect(applyTheme('system')).toBe('dark');
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('dark');
  });

  it('切换 store 主题即生效', async () => {
    stop = useTheme();
    const user = useUserStore();
    expect(user.theme).toBe('system');

    user.setTheme('dark');
    // watch 是异步 flush
    await Promise.resolve();
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('dark');
  });

  it('主题选择持久化到 localStorage', async () => {
    stop = useTheme();
    const user = useUserStore();
    user.setTheme('dark');
    await Promise.resolve();

    const raw = localStorage.getItem('mingri-toutiao:user');
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw!).theme).toBe('dark');
  });

  it('非法值经 setTheme 归一化，不会写脏数据', () => {
    const user = useUserStore();
    user.setTheme('blue' as never);
    expect(user.theme).toBe('system');
  });

  it('stop() 后不再跟随 store 变化', async () => {
    stop = useTheme();
    const user = useUserStore();
    user.setTheme('dark');
    await Promise.resolve();
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('dark');

    stop();
    stop = undefined;
    user.setTheme('light');
    await Promise.resolve();
    // 已停止监听，属性保持原值
    expect(document.documentElement.getAttribute(THEME_ATTR)).toBe('dark');
  });
});
