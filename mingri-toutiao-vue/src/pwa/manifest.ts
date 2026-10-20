/**
 * PWA manifest 的**唯一事实源**（E8 / P1-78）。
 *
 * 为什么放在 `src/` 而不是直接写 `public/manifest.webmanifest`：
 * 1. **可测**：manifest 少一个字段（如 `icons`）的表现是"安装按钮不出现"，
 *    这种缺失只在用户侧暴露。放进 src 后 vitest 能直接 import 并断言字段完整性；
 * 2. **可类型检查**：图标尺寸/用途是有限集合，写错（`purpose: "maskabble"`）
 *    编译器就能发现；
 * 3. 产物由 `vite.config.ts` 的 `pwaPlugin` 在构建期生成，public 目录不残留副本
 *    ——两份 manifest 必有漂移（这正是 D4「mock 第二实现」的同一个坑）。
 *
 * 图标文件仍是二进制、仍放在 `public/icons/`：构建时由插件校验其存在性
 * （缺文件直接让构建失败，比运行时"装不上"好）。
 */

export interface ManifestIcon {
  src: string;
  sizes: string;
  type: 'image/png';
  purpose: 'any' | 'maskable';
}

export interface WebManifest {
  id: string;
  name: string;
  short_name: string;
  description: string;
  lang: string;
  start_url: string;
  scope: string;
  display: 'standalone';
  orientation: 'portrait';
  background_color: string;
  theme_color: string;
  categories: string[];
  icons: ManifestIcon[];
  shortcuts: {
    name: string;
    short_name: string;
    url: string;
    icons: { src: string; sizes: string }[];
  }[];
}

/** 主题色与 `styles/tokens.css` 的 `--color-primary` 对齐（浅色主题值） */
export const THEME_COLOR = '#2d6cff';
/** 背景色与 `--color-page-bg` 对齐：启动屏不会闪一下深色 */
export const BACKGROUND_COLOR = '#f4f5f6';

export const manifest: WebManifest = {
  id: '/',
  name: '明日头条',
  short_name: '明日头条',
  description: 'RSS 聚合资讯信息流：关注来源、搜索联想、负反馈与离线阅读',
  lang: 'zh-CN',
  start_url: '/',
  scope: '/',
  display: 'standalone',
  orientation: 'portrait',
  background_color: BACKGROUND_COLOR,
  theme_color: THEME_COLOR,
  categories: ['news', 'magazines'],
  icons: [
    { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
    { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
    {
      src: '/icons/icon-maskable-512.png',
      sizes: '512x512',
      type: 'image/png',
      purpose: 'maskable',
    },
  ],
  shortcuts: [
    {
      name: '我的收藏',
      short_name: '收藏',
      url: '/favorites',
      icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }],
    },
    {
      name: '搜索',
      short_name: '搜索',
      url: '/search',
      icons: [{ src: '/icons/icon-192.png', sizes: '192x192' }],
    },
  ],
};

/** 序列化为 manifest JSON（构建产物用） */
export function manifestJson(): string {
  return `${JSON.stringify(manifest, null, 2)}\n`;
}
