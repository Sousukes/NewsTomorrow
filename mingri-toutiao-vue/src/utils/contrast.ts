/**
 * WCAG 2.1 对比度计算（纯函数，供色板守护测试使用）。
 * 公式：相对亮度 L = 0.2126R + 0.7152G + 0.0722B（各通道先做 sRGB 反伽马），
 * 对比度 = (L1 + 0.05) / (L2 + 0.05)。
 *
 * 本模块只导出 `contrastRatio` 与两个 AA 阈值（P1-87 / D9）：
 * 其余三个原本也是 export，但全仓没有任何外部调用方——`parseHex` /
 * `relativeLuminance` 是 `contrastRatio` 的内部步骤，`meetsAA` 是个从未被
 * 用上的便捷包装（删掉了，需要时三行就能加回来）。导出即承诺公共面，
 * 没有消费者的 export 只会让人以为"这东西能从外面调"。
 */

/** 解析 #rgb / #rrggbb；非法值返回 null */
function parseHex(hex: string): [number, number, number] | null {
  const raw = hex.trim().replace(/^#/, '');
  const full =
    raw.length === 3
      ? raw
          .split('')
          .map((c) => c + c)
          .join('')
      : raw;
  if (!/^[0-9a-f]{6}$/i.test(full)) return null;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

/** 相对亮度（0~1）；无法解析时返回 null */
function relativeLuminance(hex: string): number | null {
  const rgb = parseHex(hex);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 两色对比度（1~21）；任一无法解析时返回 null */
export function contrastRatio(a: string, b: string): number | null {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  if (la === null || lb === null) return null;
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

/** WCAG 阈值：正文 4.5，大字/图形 3 */
export const AA_TEXT = 4.5;
export const AA_LARGE = 3;
