import { describe, expect, it } from 'vitest';
import { manifest, manifestJson } from './manifest';

/**
 * manifest 契约（E8 / P1-78）。
 *
 * 为什么值得单测：manifest 缺一个字段的**表现**是"安装按钮不出现"——
 * 没有报错、没有日志，只有用户装不上。这种缺失必须在 CI 里拦住。
 * `purpose: 'maskable'` 手滑写成 `maskabble` 同样只表现为 Android 上缘露白边。
 */
describe('pwa/manifest', () => {
  it('可安装所必需的字段齐备', () => {
    expect(manifest.name).toBe('明日头条');
    expect(manifest.short_name.length).toBeLessThanOrEqual(12);
    expect(manifest.start_url).toBe('/');
    expect(manifest.scope).toBe('/');
    expect(manifest.display).toBe('standalone');
    expect(manifest.id).toBe('/');
    expect(manifest.description.length).toBeGreaterThan(10);
  });

  it('主题色与背景色都是 6 位 hex（多了 #ggg 这类无效值浏览器会整份忽略）', () => {
    expect(manifest.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
    expect(manifest.background_color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('主题色与设计令牌一致（改 tokens.css 时这里会提醒同步）', () => {
    expect(manifest.theme_color).toBe('#2d6cff');
    expect(manifest.background_color).toBe('#f4f5f6');
  });

  it('图标同时覆盖 any 与 maskable（少 maskable，Android 裁切后边缘出白边）', () => {
    const purposes = manifest.icons.map((i) => i.purpose);
    expect(purposes).toContain('any');
    expect(purposes).toContain('maskable');
    // 每个图标都必须声明尺寸（缺失时部分浏览器直接跳过该图标）
    for (const icon of manifest.icons) {
      expect(icon.sizes).toMatch(/^\d+x\d+$/);
      expect(icon.type).toBe('image/png');
      expect(icon.src.startsWith('/icons/')).toBe(true);
    }
    // 至少一个 192（Chrome 安装门槛）与一个 512（maskable 要求）
    const sizes = manifest.icons.map((i) => i.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
  });

  it('快捷方式指向已存在的路由', () => {
    expect(manifest.shortcuts.map((s) => s.url)).toEqual(['/favorites', '/search']);
  });

  it('序列化结果是合法 JSON（构建产物直接用它，语法错会在部署后才炸）', () => {
    const parsed = JSON.parse(manifestJson());
    expect(parsed.name).toBe(manifest.name);
    expect(parsed.icons).toHaveLength(manifest.icons.length);
  });
});
