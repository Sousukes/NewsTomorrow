/**
 * 语义色板的**唯一事实源**（E9 / P1-81）。
 *
 * 为什么不直接从 `tokens.css` 读：vitest 会把 `.css` 模块置空，
 * `?raw` 与 `import.meta.glob` 拿到的都是空串（实测）。于是：
 * - 色值定义在这里，可被测试直接断言（对比度门槛靠它守）；
 * - `tokens.css` 仍写具体值（CSS 必须如此），
 *   **一致性由构建期校验**（`vite.config.ts::tokensPlugin`）——不一致直接让构建失败。
 */

export const palette = {
  light: {
    primary: '#2a68f7',
    pageBg: '#f4f5f6',
    cardBg: '#ffffff',
    textPrimary: '#222222',
    textSecondary: '#666c76',
    textTertiary: '#9a9fa8',
    divider: '#f0f1f2',
    border: '#e5e6e8',
    borderStrong: '#8e939d',
    error: '#e5484d',
  },
  dark: {
    primary: '#4d86ff',
    pageBg: '#121316',
    cardBg: '#1c1e22',
    textPrimary: '#e8eaed',
    textSecondary: '#9aa0a6',
    textTertiary: '#7d838c',
    divider: '#2a2d33',
    border: '#33373d',
    borderStrong: '#6b7280',
    error: '#ff6b6f',
  },
} as const;

/** CSS 变量名 → palette 字段（构建期校验与文档共用这一张表） */
export const CSS_VAR_MAP: Record<string, keyof typeof palette.light> = {
  'color-primary': 'primary',
  'color-page-bg': 'pageBg',
  'color-card-bg': 'cardBg',
  'color-text-primary': 'textPrimary',
  'color-text-secondary': 'textSecondary',
  'color-text-tertiary': 'textTertiary',
  'color-divider': 'divider',
  'color-border': 'border',
  'color-border-strong': 'borderStrong',
  'color-error': 'error',
};
