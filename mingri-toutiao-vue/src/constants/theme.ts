/**
 * 主题模式单一来源。JS 把解析后的主题写到 `<html data-theme>`，
 * CSS 只认该属性，避免媒体查询与属性选择器各写一套。
 */

export const THEME_MODES = ['system', 'light', 'dark'] as const;

/** 用户可选 */
export type ThemeMode = (typeof THEME_MODES)[number];

/** 解析后的实际主题 */
export type ResolvedTheme = 'light' | 'dark';

export const DEFAULT_THEME: ThemeMode = 'system';

/** UI 选项 */
export const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
];

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (THEME_MODES as readonly string[]).includes(value);
}

/** 非法值回退默认 */
export function toThemeMode(value: unknown): ThemeMode {
  return isThemeMode(value) ? value : DEFAULT_THEME;
}

/** 无 matchMedia 时按浅色 */
export function prefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** 用户选择 → 实际主题 */
export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'light' || mode === 'dark') return mode;
  return prefersDark() ? 'dark' : 'light';
}
