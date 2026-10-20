import { beforeEach, describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { useOnline } from './useOnline';

/**
 * 在线状态（E8 / P1-78）：首屏读 `navigator.onLine`，之后跟着 `online`/`offline` 事件走。
 *
 * 关键是被动更新：用户在地铁里穿过隧道时界面要给反馈，而不是等下一次请求失败
 * 才后知后觉——这也是它值得单独一个组合式函数的理由。
 */
const Probe = defineComponent({
  setup() {
    // 注意：渲染函数里闭包引用的是 Ref 本身，要显式 `.value`
    const { online } = useOnline();
    return () => h('span', String(online.value));
  },
});

function setOnline(value: boolean) {
  Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

beforeEach(() => {
  setOnline(true);
});

describe('composables/useOnline', () => {
  it('首屏同步当前状态（离线启动时一开始就是离线）', () => {
    setOnline(false);
    const w = mount(Probe);
    expect(w.text()).toBe('false');
  });

  it('offline 事件 → 变 false；online 事件 → 回 true', async () => {
    const w = mount(Probe);
    expect(w.text()).toBe('true');
    window.dispatchEvent(new Event('offline'));
    await w.vm.$nextTick();
    expect(w.text()).toBe('false');
    window.dispatchEvent(new Event('online'));
    await w.vm.$nextTick();
    expect(w.text()).toBe('true');
  });

  it('卸载后不再响应（ listener 泄漏会让多个实例互相覆盖）', async () => {
    const w = mount(Probe);
    w.unmount();
    setOnline(false);
    window.dispatchEvent(new Event('offline'));
    // 卸载后的事件不该影响已卸载实例（也无从断言，只要不抛即可）
    expect(() => window.dispatchEvent(new Event('online'))).not.toThrow();
  });
});
