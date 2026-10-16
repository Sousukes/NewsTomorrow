import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';

import AppNav from './AppNav.vue';

/**
 * A6：图标颜色不得写死。
 * 深色模式下导航底色是 `--color-card-bg`，而写死的 `#222` 与它几乎同色，
 * 图标直接"消失"。统一改为 `currentColor`，颜色由 CSS 变量在容器上给出。
 */
const mountAll = () =>
  mount(AppNav, {
    props: {
      showBack: true,
      showSearch: true,
      showMessage: true,
      showMore: true,
      variant: 'search',
    },
  });

describe('components/AppNav 图标配色（A6）', () => {
  it('图标不出现任何硬编码色值', () => {
    const w = mountAll();
    const hexish = w.findAll('svg, svg *').filter((el) => {
      const stroke = el.attributes('stroke') ?? '';
      const fill = el.attributes('fill') ?? '';
      return stroke.startsWith('#') || fill.startsWith('#');
    });
    expect(hexish).toHaveLength(0);
  });

  it('描边与填充使用 currentColor（颜色由容器 CSS 提供）', () => {
    const w = mountAll();
    const strokes = w.findAll('[stroke="currentColor"]');
    const fills = w.findAll('[fill="currentColor"]');
    // 返回 / 搜索框放大镜 / 搜索 / 消息 四条描边 + 更多按钮三个圆点
    expect(strokes.length).toBeGreaterThanOrEqual(4);
    expect(fills.length).toBe(3);
  });

  it('品牌变体不渲染图标按钮（无 currentColor 依赖）', () => {
    const w = mount(AppNav, { props: { variant: 'brand', title: '明日头条' } });
    expect(w.findAll('svg')).toHaveLength(0);
    expect(w.text()).toContain('明日头条');
  });
});
