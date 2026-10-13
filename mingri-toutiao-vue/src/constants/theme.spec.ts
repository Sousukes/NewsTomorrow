import { describe, expect, it } from 'vitest';
import {
  DEFAULT_THEME,
  THEME_MODES,
  THEME_OPTIONS,
  isThemeMode,
  prefersDark,
  resolveTheme,
  toThemeMode,
} from '@/constants/theme';

describe('constants/theme', () => {
  it('三种模式且选项一一对应', () => {
    expect(THEME_MODES).toEqual(['system', 'light', 'dark']);
    expect(THEME_OPTIONS.map((o) => o.value)).toEqual([...THEME_MODES]);
    for (const o of THEME_OPTIONS) expect(o.label).toBeTruthy();
  });

  it('isThemeMode 识别合法值、拒绝非法值', () => {
    expect(isThemeMode('system')).toBe(true);
    expect(isThemeMode('dark')).toBe(true);
    for (const v of ['', 'DARK', 'blue', null, undefined, 1, {}]) {
      expect(isThemeMode(v)).toBe(false);
    }
  });

  it('toThemeMode 非法值回退默认', () => {
    expect(toThemeMode('light')).toBe('light');
    expect(toThemeMode('nope')).toBe(DEFAULT_THEME);
    expect(toThemeMode(undefined)).toBe(DEFAULT_THEME);
    expect(toThemeMode(null)).toBe(DEFAULT_THEME);
  });

  it('resolveTheme：显式值直接用', () => {
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
  });

  it('resolveTheme：system 按系统偏好解析', () => {
    // jsdom 下 prefersDark() 恒为 false，故 system 解析为 light
    expect(prefersDark()).toBe(false);
    expect(resolveTheme('system')).toBe('light');
  });
});
