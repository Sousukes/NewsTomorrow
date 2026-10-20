import { describe, expect, it } from 'vitest';
import { mockSourceKey } from './source';

/** key 必须确定：否则演示数据顺序一变，已关注的关系就指向别的来源。 */
describe('utils/source 离线来源标识', () => {
  it('同一名称得到同一 key（可重复调用、可跨进程）', () => {
    expect(mockSourceKey('新华社')).toBe(mockSourceKey('新华社'));
  });

  it('不同名称得到不同 key', () => {
    expect(mockSourceKey('新华社')).not.toBe(mockSourceKey('人民日报'));
  });

  it('稳定不随调用顺序变化（不存在计数器）', () => {
    const first = mockSourceKey('科技日报');
    mockSourceKey('体育晨报');
    mockSourceKey('财经周刊');
    expect(mockSourceKey('科技日报')).toBe(first);
  });

  it('空串也有确定 key（不抛错、不返回空）', () => {
    expect(mockSourceKey('')).toBe(mockSourceKey(''));
    expect(mockSourceKey('')).not.toBe('');
  });

  it('大小写/空白不同算不同来源（与后端按名称快照一致，不做模糊归一）', () => {
    expect(mockSourceKey('新华社')).not.toBe(mockSourceKey(' 新华社 '));
  });
});
