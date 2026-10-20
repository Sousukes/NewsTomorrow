import { describe, expect, it } from 'vitest';
import { palette, CSS_VAR_MAP } from './palette';
import { AA_LARGE, AA_TEXT, contrastRatio } from '@/utils/contrast';

/**
 * 色板对比度守护（E9 / P1-81）。
 *
 * 为什么需要它：`--color-text-secondary` 曾是 #9a9fa8，在白卡上只有 **2.66:1**
 * （AA 正文要 4.5）——它是来源/时间/评论数的颜色，出现频率仅次于正文，
 * 而这种缺陷没有任何运行时表现。阈值写进测试后，以后调色板会立刻红。
 *
 * 色值来自 `palette.ts`（唯一事实源）；`tokens.css` 与它的一致性由
 * 构建期校验保证（`vite.config.ts::tokensPlugin`），所以这里不需要读 CSS。
 */

const ratio = (a: string, b: string) => contrastRatio(a, b) ?? 0;

describe('色板对比度（WCAG AA）', () => {
  it('浅色：正文与元信息在卡片/页底上都 ≥ 4.5', () => {
    const p = palette.light;
    for (const [name, bg] of [
      ['卡片', p.cardBg],
      ['页底', p.pageBg],
    ] as const) {
      expect(ratio(p.textPrimary, bg), `primary on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
      expect(ratio(p.textSecondary, bg), `secondary on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('深色：正文与元信息在卡片/页底上都 ≥ 4.5', () => {
    const p = palette.dark;
    for (const [name, bg] of [
      ['卡片', p.cardBg],
      ['页底', p.pageBg],
    ] as const) {
      expect(ratio(p.textPrimary, bg), `primary on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
      expect(ratio(p.textSecondary, bg), `secondary on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('主色上的白字 ≥ 4.5（按钮文案按正文算）', () => {
    expect(ratio('#ffffff', palette.light.primary)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it('表单控件边界 ≥ 3（非文本对比度 1.4.11）', () => {
    expect(ratio(palette.light.borderStrong, palette.light.cardBg)).toBeGreaterThanOrEqual(
      AA_LARGE,
    );
    expect(ratio(palette.dark.borderStrong, palette.dark.cardBg)).toBeGreaterThanOrEqual(AA_LARGE);
  });

  it('tertiary 必须比 secondary 更淡（否则视觉层级会反）', () => {
    const sec = ratio(palette.light.textSecondary, palette.light.cardBg);
    const ter = ratio(palette.light.textTertiary, palette.light.cardBg);
    expect(ter).toBeLessThan(sec);
  });

  it('每个 CSS 变量都映射到 palette 字段（新增令牌不会漏校验）', () => {
    for (const key of Object.values(CSS_VAR_MAP)) {
      expect(palette.light[key]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(palette.dark[key]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});
