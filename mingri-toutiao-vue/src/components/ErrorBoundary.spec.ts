import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';

/**
 * 全局错误边界——此前 0% 覆盖。
 *
 * 它的价值全在「主流程崩了之后」：如果它自己也不工作，用户看到的就是**白屏**，
 * 而且没有任何证据能说明发生了什么。所以这里验三件：
 * 1. 子组件抛错时**真的接住**（并返回 false 阻止继续向上抛）；
 * 2. 文案回落到默认值，不把空 message 显示成空白；
 * 3. 「重试」真的重新挂载子树（不只是清掉错误状态）。
 */

import ErrorBoundary from '@/components/ErrorBoundary.vue';

/** 一个可控制"这次渲染是否抛错"的子组件 */
function makeChild(shouldThrow: () => boolean) {
  return defineComponent({
    name: 'Boom',
    setup() {
      return () => {
        if (shouldThrow()) throw new Error('子组件炸了');
        return h('div', { class: 'ok' }, '正常内容');
      };
    },
  });
}

const mountBoundary = (child: ReturnType<typeof makeChild>, title?: string) =>
  mount(ErrorBoundary, {
    props: title ? { title } : {},
    slots: { default: () => h(child) },
  });

beforeEach(() => {
  vi.clearAllMocks();
  // onErrorCaptured 会吞掉错误；Vue 仍会打 console.error，这里静音避免刷屏
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('ErrorBoundary', () => {
  it('子树正常时原样渲染，不显示兜底 UI', () => {
    const w = mountBoundary(makeChild(() => false));
    expect(w.find('.ok').exists()).toBe(true);
    expect(w.find('.mt-boundary').exists()).toBe(false);
  });

  it('子树抛错时接住并显示兜底 UI（而不是白屏）', async () => {
    const w = mountBoundary(makeChild(() => true));
    await w.vm.$nextTick();
    expect(w.find('.mt-boundary').exists()).toBe(true);
    expect(w.text()).toContain('子组件炸了');
  });

  it('自定义标题生效，默认标题在未传时回落', async () => {
    const custom = mountBoundary(
      makeChild(() => true),
      '资讯加载失败',
    );
    await custom.vm.$nextTick();
    expect(custom.text()).toContain('资讯加载失败');

    const fallback = mountBoundary(makeChild(() => true));
    await fallback.vm.$nextTick();
    expect(fallback.text()).toContain('页面出了点问题');
  });

  it('抛出的不是 Error（比如字符串）也能显示，不渲染成 [object Object]', async () => {
    const ThrowString = defineComponent({
      setup: () => () => {
        throw '纯字符串错误';
      },
    });
    const w = mount(ErrorBoundary, { slots: { default: () => h(ThrowString) } });
    await w.vm.$nextTick();
    expect(w.find('.mt-boundary').exists()).toBe(true);
    expect(w.text()).not.toContain('[object');
    expect(w.text()).toContain('纯字符串错误');
  });

  it('「重试」真的重新挂载子树（子组件修好后能恢复）', async () => {
    let broken = true;
    const w = mountBoundary(makeChild(() => broken));
    await w.vm.$nextTick();
    expect(w.find('.mt-boundary').exists()).toBe(true);

    broken = false; // 模拟"外部原因已恢复"
    const retry = w.findAll('button').find((b) => b.text().includes('重试'));
    expect(retry, '兜底 UI 里必须有重试入口').toBeTruthy();
    await retry!.trigger('click');
    await w.vm.$nextTick();

    expect(w.find('.mt-boundary').exists()).toBe(false);
    expect(w.find('.ok').exists()).toBe(true);
  });

  it('「返回首页」跳根路径', async () => {
    const assign = vi.fn();
    const original = window.location;
    // jsdom 的 location 只读，替换成可控对象
    Object.defineProperty(window, 'location', {
      value: { ...original, assign },
      writable: true,
      configurable: true,
    });

    const w = mountBoundary(makeChild(() => true));
    await w.vm.$nextTick();
    const home = w.findAll('button').find((b) => b.text().includes('返回首页'));
    expect(home).toBeTruthy();
    await home!.trigger('click');
    expect(assign).toHaveBeenCalledWith('/');

    Object.defineProperty(window, 'location', {
      value: original,
      writable: true,
      configurable: true,
    });
  });

  it('错误被吞住，不再向上冒泡（否则全局 unhandledrejection 会重复报错）', async () => {
    const onError = vi.fn();
    const w = mount(ErrorBoundary, {
      slots: { default: () => h(makeChild(() => true)) },
      global: { config: { errorHandler: onError } },
    });
    await w.vm.$nextTick();
    expect(onError).not.toHaveBeenCalled();
  });
});
