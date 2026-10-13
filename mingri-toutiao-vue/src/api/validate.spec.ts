import { describe, expect, it } from 'vitest';
import { validateComment, validateMessage, validateNewsItem } from './validate';

describe('api/validate（M8 运行时校验）', () => {
  describe('validateNewsItem', () => {
    it('合法对象无问题', () => {
      expect(
        validateNewsItem({
          id: 'n1',
          title: '标题',
          summary: '摘要',
          source: '来源',
          time: '刚刚',
          commentCount: 3,
          isTop: false,
          images: ['a.jpg'],
        }),
      ).toEqual([]);
    });

    it('缺 id/title 报错', () => {
      const issues = validateNewsItem({ title: '', id: '' });
      expect(issues.map((i) => i.path).sort()).toEqual(['id', 'title']);
    });

    it('类型错误报错', () => {
      const issues = validateNewsItem({
        id: 'x',
        title: 't',
        commentCount: '3' as unknown as number,
        isTop: 'yes' as unknown as boolean,
        images: 'a.jpg' as unknown as string[],
      });
      expect(issues.map((i) => i.path).sort()).toEqual(['commentCount', 'images', 'isTop']);
    });

    it('非对象直接报错', () => {
      expect(validateNewsItem(null)[0].message).toBe('不是对象');
    });
  });

  describe('validateComment', () => {
    it('合法对象无问题', () => {
      expect(validateComment({ id: 'c1', content: '内容', likes: 1, liked: true })).toEqual([]);
    });

    it('缺 content 报错', () => {
      expect(validateComment({ id: 'c1', content: '' })[0].path).toBe('content');
    });
  });

  describe('validateMessage', () => {
    it('合法对象无问题', () => {
      expect(validateMessage({ id: 'm1', title: 't', read: false })).toEqual([]);
    });

    it('read 非布尔报错', () => {
      expect(validateMessage({ id: 'm1', title: 't', read: 1 })[0].path).toBe('read');
    });
  });
});

describe('validateComment 归属字段', () => {
  it('接受 userId 为数字或 null', () => {
    expect(validateComment({ id: 'c1', content: 'x', userId: 7 })).toEqual([]);
    expect(validateComment({ id: 'c1', content: 'x', userId: null })).toEqual([]);
  });

  it('userId 类型错误要报错', () => {
    const issues = validateComment({ id: 'c1', content: 'x', userId: '7' });
    expect(issues.some((i) => i.path === 'userId')).toBe(true);
  });

  it('mine 非布尔要报错', () => {
    const issues = validateComment({ id: 'c1', content: 'x', mine: 'yes' });
    expect(issues.some((i) => i.path === 'mine')).toBe(true);
  });
});
