import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';

export interface UseInfiniteListOptions {
  /** 触底加载回调；返回本次加载条数（0 视为无更多） */
  onLoadMore: () => Promise<number>;
  /** 是否还有更多 */
  hasMore: () => boolean;
  /** 是否正在加载（首屏或加载更多） */
  loading?: () => boolean;
  /** 视口根元素，默认为 window */
  root?: Ref<HTMLElement | null>;
  /** 提前触发距离（px） */
  rootMargin?: string;
}

/**
 * 无限滚动：观察哨兵元素进入视口即触发加载。
 * 组件卸载时自动断开观察，避免内存泄漏。
 */
export function useInfiniteList(options: UseInfiniteListOptions) {
  const sentinel = ref<HTMLElement | null>(null);
  /** 触底后短暂锁定，防止连续交叉触发 */
  const cooldown = ref(false);
  let observer: IntersectionObserver | null = null;

  async function tryLoad() {
    if (cooldown.value) return;
    if (options.loading?.()) return;
    if (!options.hasMore()) return;

    cooldown.value = true;
    try {
      await options.onLoadMore();
    } catch {
      /* 错误由调用方处理 */
    } finally {
      // 短暂冷却，避免一次触底连发多次
      setTimeout(() => (cooldown.value = false), 300);
    }
  }

  function disconnect() {
    observer?.disconnect();
    observer = null;
  }

  function observe() {
    disconnect();
    if (typeof IntersectionObserver === 'undefined') return;
    observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void tryLoad();
      },
      {
        root: options.root?.value ?? null,
        rootMargin: options.rootMargin ?? '200px',
      },
    );
    if (sentinel.value) observer.observe(sentinel.value);
  }

  onMounted(observe);
  onBeforeUnmount(disconnect);
  // 哨兵节点可能因 v-if 重新挂载，需重新观察
  watch(sentinel, (el) => {
    if (el) observe();
  });

  return { sentinel, tryLoad, disconnect, observe };
}
