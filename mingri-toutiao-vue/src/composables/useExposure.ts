import { onBeforeUnmount, onMounted, ref } from 'vue';

export interface UseExposureOptions {
  /** 上报一批已曝光的 id。失败会被静默重试一次，仍失败则丢弃 */
  report: (ids: string[]) => Promise<unknown> | unknown;
  /** 可见比例阈值（默认 0.5）：低于此值不算"被看到" */
  ratio?: number;
  /** 停留时长（默认 500ms）：过滤"快速滑过" */
  dwellMs?: number;
  /** 攒批条数上限（默认 10）：到量即发 */
  batchMax?: number;
  /** 攒批最长等待（默认 5000ms）：不满也发 */
  flushMs?: number;
}

/**
 * 曝光判定（E3）：**进入视口 ≥ 50% 且停留 ≥ 500ms** 才算看过。
 *
 * 三条刻意设计：
 * 1. **不用"渲染即算"**：无限滚动会预渲染下方若干屏，渲染 ≠ 被看到。用渲染判定
 *    会把用户根本没扫到的内容计为已曝光，去重后信息流会明显变空。
 * 2. **攒批上报**（10 条 / 5 秒，先到先发）：曝光是最高频的写路径（一屏 20 条，
 *    翻两页就 60 条），逐条 POST 会把浏览变成 60 次写入请求。
 * 3. **失败静默重试一次，仍失败丢弃**：曝光丢失的代价只是"这条可能重复出现一次"，
 *    远小于阻塞浏览——所以这里**不向上抛错**。
 *
 * 页面隐藏 / 卸载时强制 flush（`visibilitychange` + `pagehide`），避免用户
 * 直接划走丢了最后几条。
 */
export function useExposure(options: UseExposureOptions) {
  const ratio = options.ratio ?? 0.5;
  const dwellMs = options.dwellMs ?? 500;
  const batchMax = options.batchMax ?? 10;
  const flushMs = options.flushMs ?? 5000;

  /** 待上报的 id（对外只读，用于测试与调试） */
  const pending = ref<string[]>([]);

  let observer: IntersectionObserver | null = null;
  /** 元素 ↔ id 双向映射：`unobserve` 只给得到元素，而上报只认 id */
  const idByEl = new Map<Element, string>();
  const elById = new Map<string, Element>();
  /** 已进入视口、正在计停留时长的定时器 */
  const dwellTimers = new Map<string, ReturnType<typeof setTimeout>>();
  let flushTimer: ReturnType<typeof setTimeout> | null = null;

  function clearFlushTimer(): void {
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
  }

  function scheduleFlush(): void {
    if (flushTimer) return;
    flushTimer = setTimeout(() => {
      flushTimer = null;
      void flush();
    }, flushMs);
  }

  /** 真发一次：失败**静默重试一次**，仍失败丢弃（不向上抛） */
  async function send(ids: string[]): Promise<void> {
    try {
      await options.report(ids);
      return;
    } catch {
      /* 重试一次 */
    }
    try {
      await options.report(ids);
    } catch {
      /* 丢弃：曝光丢失的代价小于阻塞浏览 */
    }
  }

  /** 立即上报待发批次 */
  async function flush(): Promise<void> {
    clearFlushTimer();
    const ids = [...pending.value];
    if (!ids.length) return;
    pending.value = [];
    await send(ids);
  }

  function addPending(id: string): void {
    if (pending.value.includes(id)) return;
    pending.value = [...pending.value, id];
    // 已计入曝光，不必再观察它（否则滚回来会重复触发）
    const el = elById.get(id);
    if (el) {
      observer?.unobserve(el);
      idByEl.delete(el);
    }
    elById.delete(id);
    if (pending.value.length >= batchMax) void flush();
    else scheduleFlush();
  }

  function ensureObserver(): IntersectionObserver | null {
    if (observer) return observer;
    if (typeof IntersectionObserver === 'undefined') return null;
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = idByEl.get(entry.target);
          if (!id) continue;
          if (entry.isIntersecting && Number(entry.intersectionRatio) >= ratio) {
            if (dwellTimers.has(id)) continue;
            const timer = setTimeout(() => {
              dwellTimers.delete(id);
              addPending(id);
            }, dwellMs);
            dwellTimers.set(id, timer);
            continue;
          }
          // 离开视口 / 未达比例：取消停留计时（"快速滑过"不算看过）
          const timer = dwellTimers.get(id);
          if (timer) {
            clearTimeout(timer);
            dwellTimers.delete(id);
          }
        }
      },
      { threshold: [ratio] },
    );
    return observer;
  }

  /** 注册一个元素的曝光追踪（配合模板 ref 使用） */
  function observe(el: Element | null | undefined, id: string): void {
    if (!el || !id) return;
    const io = ensureObserver();
    if (!io) return;
    if (idByEl.get(el) === id) return;
    idByEl.set(el, id);
    elById.set(id, el);
    io.observe(el);
  }

  function unobserve(el: Element | null | undefined): void {
    if (!el) return;
    const id = idByEl.get(el);
    if (id) {
      const timer = dwellTimers.get(id);
      if (timer) clearTimeout(timer);
      dwellTimers.delete(id);
      elById.delete(id);
    }
    idByEl.delete(el);
    observer?.unobserve(el);
  }

  function clearDwellTimers(): void {
    for (const timer of dwellTimers.values()) clearTimeout(timer);
    dwellTimers.clear();
  }

  function onVisibility(): void {
    if (document.visibilityState === 'hidden') void flush();
  }

  /** 卸载：断开观察、清定时器、摘监听，并把未发的最后一批送出去 */
  function destroy(): void {
    clearDwellTimers();
    clearFlushTimer();
    observer?.disconnect();
    observer = null;
    idByEl.clear();
    elById.clear();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibility);
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('pagehide', onHide);
    }
    void flush();
  }

  function onHide(): void {
    void flush();
  }

  onMounted(() => {
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibility);
    }
    if (typeof window !== 'undefined') {
      window.addEventListener('pagehide', onHide);
    }
  });

  onBeforeUnmount(destroy);

  return { pending, observe, unobserve, flush, destroy };
}
