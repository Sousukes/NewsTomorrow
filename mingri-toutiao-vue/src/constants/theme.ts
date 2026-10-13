/**
 * 主题模式单一来源。
 *
 * - `system`：跟随系统偏好（`prefers-color-scheme`），并实时响应其变化
 * - `light` / `dark`：显式覆盖系统偏好
 *
 * 生效方式：由 JS 把**解析后的实际主题**写成 `<html data-theme="light|dark">`，
 * CSS 只认 `[data-theme='dark']`。这样"跟随系统"与"手动指定"共用同一套深色令牌，
 * 不需要在 CSS 里把媒体查询与属性选择器各写一遍（避免重复与不一致）。
 */

export const THEME_MODES = ['system', 'light', 'dark'] as const;

/** 用户可选的三种模式 */
export type ThemeMode = (typeof THEME_MODES)[number];

/** 实际生效的主题（已把 system 解析掉） */
export type ResolvedTheme = 'light' | 'dark';

export const DEFAULT_THEME: ThemeMode = 'system';

/** 供 UI 渲染的选项（含中文标签） */
export const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
];

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && (THEME_MODES as readonly string[]).includes(value);
}

/** 归一化：非法值（含 null / 数字）回退默认，避免脏数据写入 store */
export function toThemeMode(value: unknown): ThemeMode {
  return isThemeMode(value) ? value : DEFAULT_THEME;
}

/** 系统是否偏好深色；不支持 matchMedia 时按浅色处理 */
export function prefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** 把用户选择解析为实际主题 */
export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === 'light' || mode === 'dark') return mode;
  return prefersDark() ? 'dark' : 'light';
}
