import { nextTick, onBeforeUnmount, watch, type Ref } from 'vue';

/**
 * 对话框的 Escape / 焦点陷阱 / 焦点还原。
 * 用法：`useDialog(() => props.open, close, panelRef)`，panelRef 绑到面板（非遮罩）。
 * 背景见 docs/前端/E9可访问性与i18n设计.md。
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/** 不用 offsetParent 判断可见性：jsdom 不实现该属性（恒为 null）。 */
function isVisible(el: HTMLElement): boolean {
  let node: HTMLElement | null = el;
  while (node && node !== document.body) {
    if (node.hasAttribute('hidden') || node.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    node = node.parentElement;
  }
  return true;
}

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isVisible);
}

export function useDialog(open: () => boolean, close: () => void, panel: Ref<HTMLElement | null>) {
  /** 打开前的焦点元素，关闭时还给它 */
  let previouslyFocused: HTMLElement | null = null;

  function onKeydown(ev: KeyboardEvent) {
    if (!open()) return;
    if (ev.key === 'Escape') {
      // 不冒泡：内层弹窗的 Escape 不该同时关掉外层
      ev.stopPropagation();
      ev.preventDefault();
      close();
      return;
    }
    if (ev.key !== 'Tab') return;

    const items = focusables(panel.value);
    if (!items.length) {
      ev.preventDefault();
      panel.value?.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;

    if (ev.shiftKey && (active === first || !panel.value?.contains(active))) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  watch(open, async (isOpen) => {
    if (isOpen) {
      previouslyFocused = (document.activeElement as HTMLElement | null) ?? null;
      // 监听同步挂上（Escape 要立刻可用）；聚焦需等面板挂载
      document.addEventListener('keydown', onKeydown, true);
      await nextTick();
      const items = focusables(panel.value);
      (items[0] ?? panel.value)?.focus();
      return;
    }
    document.removeEventListener('keydown', onKeydown, true);
    // 打开者可能已被移除（列表刷新），此时静默跳过
    if (previouslyFocused?.isConnected) previouslyFocused.focus();
    previouslyFocused = null;
  });

  onBeforeUnmount(() => {
    document.removeEventListener('keydown', onKeydown, true);
  });

  return { onKeydown };
}

/** 供测试复用同一份实现 */
export const focusableWithin = focusables;
