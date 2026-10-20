import { nextTick, onBeforeUnmount, watch, type Ref } from 'vue';

/**
 * 对话框的键盘与焦点行为（E9 / P1-80）。
 *
 * 三件事一起做，缺一条这个对话框对键盘/读屏用户就是坏的：
 * 1. **Escape 关闭**——最基础的"我不想继续了"；
 * 2. **焦点陷阱**——`aria-modal="true"` 的诚实前提。没有陷阱时，读屏被告知
 *    "外界不可交互"，而 Tab 仍能跑到背后页面上，焦点"消失"在遮罩下面；
 * 3. **焦点还原**——关闭后回到**打开它的那个元素**。不还原的话焦点落到 `<body>`，
 *    键盘用户要从页面开头重新 Tab 一遍（在长列表里这几乎是惩罚）。
 *
 * 为什么抽成组合式函数而不是每个弹窗各写一份：本仓库的三个弹窗已经分叉了——
 * `ReportDialog` 有 Escape、另两个没有。分叉的原因不是谁偷懒，而是**没有一处
 * 承载这份逻辑**。现在有了。
 *
 * 用法：
 * ```ts
 * const dialogEl = ref<HTMLElement | null>(null);
 * useDialog(() => props.open, () => emit('update:open', false), dialogEl);
 * ```
 * 模板里把 `dialogEl` 绑到**对话框面板**（不是遮罩），焦点陷阱在面板内循环。
 */

/** 可聚焦元素的选择器（与浏览器 Tab 顺序一致的主要类型） */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/**
 * 元素是否可见（决定它该不该进入焦点序列）。
 *
 * **刻意不用 `el.offsetParent !== null`**：jsdom 不实现该属性（恒为 null），
 * 用它会让所有元素在测试里被判成不可见——于是测试全绿而真机上焦点乱跳，
 * 或者反过来（本仓库实测踩到）。改为沿祖先链查 `display` / `visibility` / `hidden`，
 * 这三样在浏览器与 jsdom 里行为一致。
 */
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
  /** 打开前的焦点元素——关闭时还给它 */
  let previouslyFocused: HTMLElement | null = null;

  function onKeydown(ev: KeyboardEvent) {
    if (!open()) return;
    if (ev.key === 'Escape') {
      // 阻止冒泡：弹窗内部的 Escape 不该同时触发外层（如全屏图片查看）
      ev.stopPropagation();
      ev.preventDefault();
      close();
      return;
    }
    if (ev.key !== 'Tab') return;

    const items = focusables(panel.value);
    if (!items.length) {
      // 没有任何可聚焦元素时，把焦点留在面板本身（否则 Tab 会逃出去）
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
      // 监听器**同步**挂上：Escape 是"立刻要能用"的东西，不该等 DOM 渲染完。
      // 只有"聚焦第一个元素"需要等面板真正挂载。
      document.addEventListener('keydown', onKeydown, true);
      await nextTick();
      const items = focusables(panel.value);
      // 优先聚焦**第一个可聚焦元素**而不是面板本身：面板通常不可聚焦，
      // 直接 focus() 会失败，焦点仍停在背后的页面上。
      (items[0] ?? panel.value)?.focus();
      return;
    }
    document.removeEventListener('keydown', onKeydown, true);
    // 还原焦点：元素可能已被移除（如列表刷新），此时静默跳过而不是抛错
    if (previouslyFocused?.isConnected) previouslyFocused.focus();
    previouslyFocused = null;
  });

  onBeforeUnmount(() => {
    document.removeEventListener('keydown', onKeydown, true);
  });

  return { onKeydown };
}

/**
 * 供测试使用：导出"面板内可聚焦元素"的计算入口。
 * 与内部实现同一份逻辑——测试断言的是真实行为，不是复制品。
 */
export const focusableWithin = focusables;
