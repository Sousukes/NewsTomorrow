import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';

/**
 * `useExposure`（E3）：曝光判定的三条规则都是**用户能感知**的——
 * 判宽了信息流会变空（没看到的也算看过），判窄了去重形同虚设。
 *
 * 这里钉死的正是这三条：视口 ≥ 50%、停留 ≥ 500ms、攒批（10 条 / 5 秒）。
 * 另外两条是可靠性：页面隐藏/卸载必须把最后一批送出去，上报失败**不能**向上抛。
 */

type Entry = { target: Element; isIntersecting: boolean; intersectionRatio: number };

const instances: FakeIO[] = [];

class FakeIO {
  cb: (entries: Entry[]) => void;
  observed = new Set<Element>();
  constructor(cb: (entries: Entry[]) => void) {
    this.cb = cb;
    instances.push(this);
  }
  observe(el: Element) {
    this.observed.add(el);
  }
  unobserve(el: Element) {
    this.observed.delete(el);
  }
  disconnect() {
    this.observed.clear();
  }
  emit(entries: Entry[]) {
    this.cb(entries);
  }
}

function el(id: string): Element {
  return { id } as unknown as Element;
}

/** 挂一个最小宿主组件，让 onMounted / onBeforeUnmount 真跑起来 */
function mountUse(options: Parameters<typeof useExposure>[0]) {
  let api!: ReturnType<typeof useExposure>;
  const Host = defineComponent({
    setup() {
      api = useExposure(options);
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { api, wrapper };
}

import { useExposure } from '@/composables/useExposure';

/** 让组件进入视口（ratio 默认给 1，即完全可见） */
function enter(api: ReturnType<typeof useExposure>, id: string, ratio = 1) {
  const node = el(id);
  api.observe(node, id);
  const io = instances[instances.length - 1];
  io.emit([{ target: node, isIntersecting: true, intersectionRatio: ratio }]);
  return io;
}

/** jsdom 默认 `visibilityState` 恒为 'visible'，隐藏分支必须手动切 */
function setVisibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
}

function leave(id: string) {
  const node = el(id);
  instances[instances.length - 1].emit([
    { target: node, isIntersecting: false, intersectionRatio: 0 },
  ]);
}

beforeEach(() => {
  instances.length = 0;
  vi.stubGlobal('IntersectionObserver', FakeIO);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useExposure · 判定规则', () => {
  it('可见比例不足 50% 不算曝光', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'n1', 0.3);
    await vi.advanceTimersByTimeAsync(1000);
    expect(report).not.toHaveBeenCalled();
    expect(api.pending.value).toEqual([]);
  });

  it('达到 50% 但未停留够 500ms 就被划走 → 不算曝光', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'n1', 1);
    await vi.advanceTimersByTimeAsync(300);
    leave('n1');
    await vi.advanceTimersByTimeAsync(1000);
    expect(report).not.toHaveBeenCalled();
  });

  it('达到 50% 且停留 500ms → 计入待发批次', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'n1', 0.5);
    await vi.advanceTimersByTimeAsync(500);
    expect(api.pending.value).toEqual(['n1']);
    // 攒批未到量，此时还不该发
    expect(report).not.toHaveBeenCalled();
  });

  it('同一条重复进入视口只计一次，且计入后不再观察', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    const io = enter(api, 'n1', 1);
    await vi.advanceTimersByTimeAsync(500);
    // 再触发一次（模拟滚回来）：idByEl 已摘除，不会再计入
    io.emit([{ target: el('n1'), isIntersecting: true, intersectionRatio: 1 }]);
    await vi.advanceTimersByTimeAsync(500);
    expect(api.pending.value).toEqual(['n1']);
  });
});

describe('useExposure · 攒批', () => {
  it('攒到 batchMax 立即上报', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report, batchMax: 3 });
    for (const id of ['a', 'b', 'c']) {
      enter(api, id, 1);
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(report).toHaveBeenCalledTimes(1);
    expect(report.mock.calls[0][0]).toEqual(['a', 'b', 'c']);
    expect(api.pending.value).toEqual([]);
  });

  it('不满 batchMax 但到 flushMs 也上报（不无限攒）', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report, batchMax: 10, flushMs: 5000 });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    expect(report).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(5000);
    expect(report).toHaveBeenCalledWith(['a']);
  });

  it('flush 后清空待发批次', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    await api.flush();
    expect(api.pending.value).toEqual([]);
    // 再 flush 一次不应重复上报（空批次不发请求）
    await api.flush();
    expect(report).toHaveBeenCalledTimes(1);
  });
});

describe('useExposure · 可靠性', () => {
  it('上报失败静默重试一次，成功后不外抛', async () => {
    const report = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(undefined);
    const { api } = mountUse({ report });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    await expect(api.flush()).resolves.toBeUndefined();
    expect(report).toHaveBeenCalledTimes(2);
  });

  it('两次都失败也只丢弃，绝不向上抛（曝光不该阻塞浏览）', async () => {
    const report = vi.fn().mockRejectedValue(new Error('boom'));
    const { api } = mountUse({ report });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    await expect(api.flush()).resolves.toBeUndefined();
    expect(report).toHaveBeenCalledTimes(2);
  });

  it('页面隐藏时把最后一批送出去', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    setVisibility('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(report).toHaveBeenCalledWith(['a']);
  });

  it('转回**可见**时不 flush（visibilitychange 两侧都触发，只看 hidden）', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report, batchMax: 10, flushMs: 5000 });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    setVisibility('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(report).not.toHaveBeenCalled();
  });

  it('pagehide 时同样 flush（直接划走不能丢最后几条）', async () => {
    const report = vi.fn();
    const { api } = mountUse({ report });
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    window.dispatchEvent(new Event('pagehide'));
    await vi.advanceTimersByTimeAsync(0);
    expect(report).toHaveBeenCalledWith(['a']);
  });

  it('卸载时断开观察并 flush 残留', async () => {
    const report = vi.fn();
    const { api, wrapper } = mountUse({ report, batchMax: 10 });
    const io = enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(0);
    expect(io.observed.size).toBe(0);
    expect(report).toHaveBeenCalledWith(['a']);
  });

  it('卸载后摘掉全局监听，不再响应隐藏事件', async () => {
    const report = vi.fn();
    const { api, wrapper } = mountUse({ report });
    // 先产生一批待发：确保"没上报"不是因为本来就空
    enter(api, 'a', 1);
    await vi.advanceTimersByTimeAsync(500);
    wrapper.unmount();
    // 卸载本身会 flush 一次，那次不算；清掉后再验证监听器已摘除
    await vi.advanceTimersByTimeAsync(0);
    report.mockClear();
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(report).not.toHaveBeenCalled();
  });
});
