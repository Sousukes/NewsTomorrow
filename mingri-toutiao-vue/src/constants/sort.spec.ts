import { describe, expect, it } from 'vitest';
import { DEFAULT_SORT, PERSONALIZED_SORT, SORT_MODES, isSortMode, toSort } from '@/constants/sort';

describe('constants/sort', () => {
  it('默认排序为 latest（与后端缺省一致）', () => {
    expect(DEFAULT_SORT).toBe('latest');
  });

  it('三模式齐备且标签非空', () => {
    expect(SORT_MODES.map((s) => s.key)).toEqual(['latest', 'hot', 'for-you']);
    for (const s of SORT_MODES) {
      expect(s.label).toBeTruthy();
    }
  });

  it('取值与后端 SORT_MODES 一致（漂移会导致请求被 422 拒绝）', () => {
    // 后端 app/ranking.py: SORT_MODES = ("latest", "hot", "for-you")
    expect(SORT_MODES.map((s) => s.key)).toEqual(['latest', 'hot', 'for-you']);
  });

  it('isSortMode 识别合法值、拒绝非法值', () => {
    expect(isSortMode('latest')).toBe(true);
    expect(isSortMode('hot')).toBe(true);
    expect(isSortMode('for-you')).toBe(true);
    expect(isSortMode('Hot')).toBe(false);
    expect(isSortMode('for_you')).toBe(false);
    expect(isSortMode('')).toBe(false);
    expect(isSortMode(undefined)).toBe(false);
  });

  it('toSort 对非法输入回退到默认（URL 参数不可信）', () => {
    expect(toSort('hot')).toBe('hot');
    expect(toSort('bogus')).toBe(DEFAULT_SORT);
    expect(toSort(null)).toBe(DEFAULT_SORT);
    expect(toSort(123)).toBe(DEFAULT_SORT);
  });

  it('个性化模式被单独标出（供 UI 提示）', () => {
    expect(PERSONALIZED_SORT).toBe('for-you');
  });
});
