import { describe, expect, it, vi } from 'vitest';

/**
 * 图片代理 URL 构造（B4），**真实后端模式**下才生效。
 *
 * 单独一个文件的原因：`vi.mock` 是**文件级**的，而默认 spec 里
 * `USE_MOCK` 为 true（未配 VITE_API_BASE）——那时 `proxiedImageUrl` 按设计
 * 原样返回外链，混在同一文件里测不到代理分支，还会污染其他用例。
 */
vi.mock('@/api/config', () => ({
  API_BASE: 'http://127.0.0.1:8801',
  USE_MOCK: false,
}));

import { bgFor, gradientFor, proxiedImageUrl } from '@/utils/image';

describe('proxiedImageUrl（真实后端模式）', () => {
  it('外站图片换成图片代理地址', () => {
    const out = proxiedImageUrl('https://cdn.example.com/a.jpg');
    expect(out).toBe(
      `http://127.0.0.1:8801/api/img?url=${encodeURIComponent('https://cdn.example.com/a.jpg')}&w=640`,
    );
  });

  it('宽度按白名单值透传（与后端 ALLOWED_WIDTHS 一致）', () => {
    expect(proxiedImageUrl('https://cdn.example.com/a.jpg', 960)).toContain('w=960');
    expect(proxiedImageUrl('https://cdn.example.com/a.jpg', 320)).toContain('w=320');
  });

  it('原始地址被完整编码（查询串里的 & 不会拼坏 URL）', () => {
    const src = 'https://cdn.example.com/a.jpg?w=100&h=200';
    const out = proxiedImageUrl(src);
    // 编码后的内容里不能再出现裸的 & 或 = 之外的分隔符
    const query = out.split('?')[1];
    expect(query).toBe(`url=${encodeURIComponent(src)}&w=640`);
    expect(out.indexOf('&h=200')).toBe(-1);
  });

  it('相对路径原样返回（本就是同源，进代理是多余一跳）', () => {
    expect(proxiedImageUrl('/static/a.png')).toBe('/static/a.png');
    expect(proxiedImageUrl('//cdn.example.com/a.png')).toBe('//cdn.example.com/a.png');
  });

  it('非 http(s) 协议一律拒绝', () => {
    expect(proxiedImageUrl('javascript:alert(1)')).toBe('');
    expect(proxiedImageUrl('data:image/png;base64,AAAA')).toBe('');
    expect(proxiedImageUrl('')).toBe('');
    expect(proxiedImageUrl(null)).toBe('');
  });

  it('产物不含引号/反斜杠/控制字符（拼进 CSS url() 也安全）', () => {
    const nasty = 'https://cdn.example.com/a.jpg");}body{background:red';
    const out = proxiedImageUrl(nasty);
    expect(out).not.toContain('"');
    expect(out).not.toContain('(');
    expect(out).not.toContain('\n');
  });

  it('bgFor 走代理；没有图才回落渐变', () => {
    const style = bgFor('n1', 'https://cdn.example.com/a.jpg');
    expect(style.startsWith('url("')).toBe(true);
    expect(style).toContain('/api/img?url=');
    expect(bgFor('n1', '')).toBe(gradientFor('n1'));
    expect(bgFor('n1', 'javascript:alert(1)')).toBe(gradientFor('n1'));
  });
});
