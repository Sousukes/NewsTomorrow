import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import EmptyState from './EmptyState.vue';

/**
 * 空态组件：标题/说明/插图渲染，以及**操作区的两种插槽用法**。
 *
 * 操作区这段是本文件的重点——组件原先只渲染具名插槽 `#action`，而仓库里 ~10 处
 * 视图把按钮放在默认插槽，那些按钮被静默丢弃。空态的意义是给用户「下一步」，
 * 按钮丢了就只剩一句解释，所以这里把两种写法都钉住，防回归。
 */
describe('components/EmptyState', () => {
  it('渲染标题与说明', () => {
    const w = mount(EmptyState, { props: { title: '暂无内容', description: '去看看别的吧' } });
    expect(w.find('.mt-empty__title').text()).toBe('暂无内容');
    expect(w.find('.mt-empty__desc').text()).toBe('去看看别的吧');
  });

  it('#action 具名插槽内容被渲染', () => {
    const w = mount(EmptyState, {
      props: { title: '空' },
      slots: { action: '<button class="go">去发现</button>' },
    });
    expect(w.find('button.go').exists()).toBe(true);
  });

  it('默认插槽内容同样被渲染（历史写法的兜底，防死按钮回归）', () => {
    const w = mount(EmptyState, {
      props: { title: '空' },
      slots: { default: '<button class="go2">去发现</button>' },
    });
    expect(w.find('button.go2').exists()).toBe(true);
  });

  it('两种插槽同时存在时以 #action 为准（显式优先于兜底）', () => {
    const w = mount(EmptyState, {
      props: { title: '空' },
      slots: {
        action: '<button class="named">具名</button>',
        default: '<button class="fallback">默认</button>',
      },
    });
    expect(w.find('button.named').exists()).toBe(true);
    expect(w.find('button.fallback').exists()).toBe(false);
  });
});
