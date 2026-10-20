import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';

import TabBar from './TabBar.vue';
import type { TabItem } from '@/types';

const items: TabItem[] = [
  { key: 'home', label: '首页', icon: 'home' },
  { key: 'message', label: '消息', icon: 'message' },
];

/**
 * A6：激活态颜色此前是内联绑定 `:stroke="active ? '#2d6cff' : '#9a9fa8'"`——
 * 深色模式下 `--color-primary` 是亮蓝而图标仍是暗蓝，且对比度不达标。
 * 改为 `currentColor` 后，颜色由 `.mt-tabbar__item` / `.is-active` 的 CSS 变量给出，
 * 图标与文字必然同色。
 */
describe('components/TabBar 图标配色（A6）', () => {
  it('图标描边为 currentColor，不随激活态内联变色', () => {
    const w = mount(TabBar, { props: { items, active: 'home' } });
    const paths = w.findAll('svg path');
    expect(paths).toHaveLength(2);
    for (const p of paths) {
      expect(p.attributes('stroke')).toBe('currentColor');
    }
    // 没有任何硬编码色值
    const hexish = w
      .findAll('svg, svg *')
      .filter((el) => (el.attributes('stroke') ?? '').startsWith('#'));
    expect(hexish).toHaveLength(0);
  });

  it('激活态只加 is-active 类（配色交给 CSS 变量）', () => {
    const w = mount(TabBar, { props: { items, active: 'message' } });
    const buttons = w.findAll('.mt-tabbar__item');
    expect(buttons[0].classes()).not.toContain('is-active');
    expect(buttons[1].classes()).toContain('is-active');
  });

  it('未读数徽标按 key 显示，且 0 / 未传不渲染', () => {
    const w = mount(TabBar, { props: { items, active: 'home', badges: { message: 3, home: 0 } } });
    const badges = w.findAll('.mt-tabbar__badge');
    expect(badges).toHaveLength(1);
    expect(badges[0].text()).toBe('3');
  });
});

/**
 * E9：当前项要有 `aria-current`。
 * 只靠 `.is-active` 类，读屏用户无法知道"我在哪个 tab"——视觉信息没有语义对应。
 */
describe('TabBar 可访问性（E9）', () => {
  it('仅当前项带 aria-current="page"', () => {
    const w = mount(TabBar, {
      props: {
        items: [
          { key: 'home', label: '首页', icon: 'home' },
          { key: 'mine', label: '我的', icon: 'mine' },
        ],
        active: 'mine',
      },
    });
    const items = w.findAll('.mt-tabbar__item');
    expect(items[0].attributes('aria-current')).toBeUndefined();
    expect(items[1].attributes('aria-current')).toBe('page');
  });
});
