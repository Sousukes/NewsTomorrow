import { describe, expect, it } from 'vitest';
import { bgFor, gradientFor, safeImageUrl } from '@/utils/image';

describe('utils/image', () => {
  describe('gradientFor', () => {
    it('同一 seed 返回同一渐变（可复现）', () => {
      expect(gradientFor('n1')).toBe(gradientFor('n1'));
    });

    it('不同 seed 产出多样结果', () => {
      const set = new Set(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map(gradientFor));
      expect(set.size).toBeGreaterThan(1);
    });
  });

  describe('bgFor 安全过滤', () => {
    it('允许 http/https 真实地址', () => {
      expect(bgFor('n1', 'https://cdn.x.com/a.jpg')).toBe('url("https://cdn.x.com/a.jpg")');
      expect(bgFor('n1', 'http://cdn.x.com/a.jpg')).toBe('url("http://cdn.x.com/a.jpg")');
    });

    it('允许协议相对与相对路径', () => {
      expect(bgFor('n1', '//cdn.x.com/a.jpg')).toBe('url("//cdn.x.com/a.jpg")');
      expect(bgFor('n1', '/static/a.jpg')).toBe('url("/static/a.jpg")');
    });

    it('拒绝 javascript: / data: / file: 协议并回落渐变', () => {
      for (const bad of ['javascript:alert(1)', 'data:text/html,<script>', 'file:///etc/passwd']) {
        expect(bgFor('n1', bad)).toBe(gradientFor('n1'));
      }
    });

    it('转义引号，阻止 url() 逃逸注入', () => {
      const out = bgFor('n1', 'https://x.com/a.jpg");}body{background:red');
      //内部不得出现未转义的双引号
      const inner = out.slice(5, -2);
      expect(inner).not.toMatch(/(^|[^\\])"/);
      expect(inner).toContain('\\"');
    });

    it('转义反斜杠', () => {
      expect(bgFor('n1', 'https://x.com/a\\')).toBe('url("https://x.com/a\\\\")');
    });

    it('剔除换行（防注入）', () => {
      expect(bgFor('n1', 'https://x.com/a\n);}*{}')).not.toContain('\n');
    });

    it('空值/空白回落渐变', () => {
      expect(bgFor('n1', '')).toBe(gradientFor('n1'));
      expect(bgFor('n1', '   ')).toBe(gradientFor('n1'));
      expect(bgFor('n1', null)).toBe(gradientFor('n1'));
      expect(bgFor('n1', undefined)).toBe(gradientFor('n1'));
    });
  });

  describe('safeImageUrl（<img src> 用）', () => {
    it('放行 http/https 与相对路径', () => {
      expect(safeImageUrl('https://cdn.x.com/a.jpg')).toBe('https://cdn.x.com/a.jpg');
      expect(safeImageUrl('//cdn.x.com/a.jpg')).toBe('//cdn.x.com/a.jpg');
      expect(safeImageUrl('/static/a.jpg')).toBe('/static/a.jpg');
    });

    it('拒绝危险协议返回空串', () => {
      for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd']) {
        expect(safeImageUrl(bad)).toBe('');
      }
    });

    it('不做 CSS 转义（属性由框架转义）', () => {
      expect(safeImageUrl('https://x.com/a.jpg')).toBe('https://x.com/a.jpg');
    });

    it('空值返回空串', () => {
      expect(safeImageUrl('')).toBe('');
      expect(safeImageUrl('   ')).toBe('');
      expect(safeImageUrl(null)).toBe('');
      expect(safeImageUrl(undefined)).toBe('');
    });
  });
});
