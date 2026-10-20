import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { defineComponent, h, nextTick, ref } from 'vue';
import { focusableWithin, useDialog } from './useDialog';

/** Escape / 焦点陷阱 / 焦点还原：每一条坏掉的方式都很隐蔽。 */

/** 最小宿主：一个触发按钮 + 一个可开关的对话框 */
function mountHost() {
  const open = ref(false);
  const closed = vi.fn(() => {
    open.value = false;
  });
  const panel = ref<HTMLElement | null>(null);

  const Host = defineComponent({
    setup() {
      useDialog(() => open.value, closed, panel);
      return () =>
        h('div', [
          h('button', { id: 'trigger', onClick: () => (open.value = true) }, '打开'),
          open.value
            ? h('div', { class: 'panel', ref: panel, tabindex: -1 }, [
                h('button', { id: 'first' }, '第一个'),
                h('input', { id: 'middle' }),
                h('button', { id: 'last' }, '最后一个'),
              ])
            : null,
        ]);
    },
  });

  const w = mount(Host, { attachTo: document.body });
  return { w, open, closed, panel };
}

const key = (k: string, opts: KeyboardEventInit = {}) =>
  document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...opts }));

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('composables/useDialog', () => {
  it('打开后焦点进入对话框（第一个可聚焦元素）', async () => {
    const { w, open } = mountHost();
    (document.getElementById('trigger') as HTMLElement).focus();
    open.value = true;
    await nextTick();
    await nextTick();
    expect(document.activeElement?.id).toBe('first');
    w.unmount();
  });

  it('Escape 关闭对话框', async () => {
    const { w, open, closed } = mountHost();
    open.value = true;
    await nextTick();
    key('Escape');
    expect(closed).toHaveBeenCalledTimes(1);
    w.unmount();
  });

  it('未打开时 Escape 不触发关闭（避免误关背后的页面）', async () => {
    const { w, closed } = mountHost();
    key('Escape');
    expect(closed).not.toHaveBeenCalled();
    w.unmount();
  });

  it('Tab 在最后一个元素上 → 回到第一个（焦点不逃逸）', async () => {
    const { w, open } = mountHost();
    open.value = true;
    await nextTick();
    await nextTick();
    (document.getElementById('last') as HTMLElement).focus();
    key('Tab');
    expect(document.activeElement?.id).toBe('first');
    w.unmount();
  });

  it('Shift+Tab 在第一个元素上 → 跳到最后一个', async () => {
    const { w, open } = mountHost();
    open.value = true;
    await nextTick();
    await nextTick();
    (document.getElementById('first') as HTMLElement).focus();
    key('Tab', { shiftKey: true });
    expect(document.activeElement?.id).toBe('last');
    w.unmount();
  });

  it('焦点在面板外时 Tab 会被拉回面板内（防止点击遮罩后逃逸）', async () => {
    const { w, open } = mountHost();
    open.value = true;
    await nextTick();
    await nextTick();
    (document.getElementById('trigger') as HTMLElement).focus();
    key('Tab', { shiftKey: true });
    expect(document.activeElement?.id).toBe('last');
    w.unmount();
  });

  it('关闭后焦点还原到打开它的元素', async () => {
    const { w, open } = mountHost();
    const trigger = document.getElementById('trigger') as HTMLElement;
    trigger.focus();
    open.value = true;
    await nextTick();
    await nextTick();
    open.value = false;
    await nextTick();
    expect(document.activeElement?.id).toBe('trigger');
    w.unmount();
  });

  it('打开它的元素已被移除时不抛错（列表刷新后的常见情形）', async () => {
    const { w, open } = mountHost();
    const trigger = document.getElementById('trigger') as HTMLElement;
    trigger.focus();
    open.value = true;
    await nextTick();
    await nextTick();
    trigger.remove();
    expect(() => {
      open.value = false;
    }).not.toThrow();
    await nextTick();
    w.unmount();
  });

  it('面板内没有可聚焦元素时 Tab 不逃逸（焦点留在面板）', async () => {
    const open = ref(false);
    const panel = ref<HTMLElement | null>(null);
    const Host = defineComponent({
      setup() {
        useDialog(
          () => open.value,
          () => {},
          panel,
        );
        return () =>
          h('div', [
            h('button', { id: 'outside' }, '外面'),
            open.value ? h('div', { ref: panel, tabindex: -1 }, ['只有文字']) : null,
          ]);
      },
    });
    const w = mount(Host, { attachTo: document.body });
    open.value = true;
    await nextTick();
    await nextTick();
    key('Tab');
    // 面板自身可聚焦（tabindex=-1），焦点不该跑到 'outside'
    expect(document.activeElement?.id).not.toBe('outside');
    w.unmount();
  });

  it('focusableWithin 排除 disabled 元素（否则陷阱会把焦点送到不可交互处）', () => {
    const root = document.createElement('div');
    root.innerHTML =
      '<button id="a">A</button><button id="b" disabled>B</button><input id="c" /><a id="d">无 href</a>';
    document.body.appendChild(root);
    const ids = focusableWithin(root).map((el) => el.id);
    expect(ids).toEqual(['a', 'c']);
  });
});
