/**
 * manifest 唯一事实源（放 src 才能被 vitest 断言、被 tsc 校验）。
 * 产物由 vite.config.ts::pwaPlugin 构建期生成，public 下不留副本。
 */

// 相对路径：本文件被 vite.config 直接 import，@/ 别名在配置文件里不可用
import { palette } from '../styles/palette';

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

export const THEME_COLOR = palette.light.primary;
export const BACKGROUND_COLOR = palette.light.pageBg;

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
