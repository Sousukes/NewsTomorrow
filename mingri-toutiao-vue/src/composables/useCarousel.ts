import { computed, ref, watch, type Ref } from 'vue';

type FramesSource = Ref<readonly string[]> | (() => readonly string[]);

export interface UseCarouselOptions {
  /** 帧数据源：响应式数组或返回数组的函数 */
  frames: FramesSource;
  /** 滑动切换阈值（px），默认 50 */
  threshold?: number;
  /** 过渡时长（ms），用于兜底定时器，默认 250 */
  transitionMs?: number;
  /** 是否首尾克隆帧做无限回环，默认 true */
  infinite?: boolean;
}

/**
 * 通用轮播：首尾克隆帧无限回环 + Pointer 手势滑动 + transitionend 跟手收尾。
 * - slides：渲染用轨道（无限回环时含首尾克隆帧）
 * - trackStyle：直接绑到轨道的 style（含 translateX 位移）
 * - goTo / prev / next / onPointerXxx / onTrackClick / onTransitionEnd：绑到模板即可
 */
export function useCarousel(options: UseCarouselOptions) {
  const threshold = options.threshold ?? 50;
  const transitionMs = options.transitionMs ?? 250;
  const infinite = options.infinite ?? true;

  const list = computed<readonly string[]>(() =>
    typeof options.frames === 'function' ? options.frames() : options.frames.value,
  );
  const N = computed(() => list.value.length);
  const cloned = computed(() => infinite && N.value >= 2);
  const cloneOffset = computed(() => (cloned.value ? 1 : 0));
  const slides = computed<string[]>(() => {
    const f = list.value;
    if (!cloned.value) return [...f];
    return [f[f.length - 1], ...f, f[0]];
  });

  // index 指向轨道位置：真实帧 i 位于 i + cloneOffset；起始显示真实第 0 张
  const index = ref(cloneOffset.value);
  const dragging = ref(false);
  const noTransition = ref(false);
  const lock = ref(false);
  const pendingWrap = ref<number | null>(null);
  const startX = ref(0);
  const offset = ref(0);
  const moved = ref(false);

  /** 当前真实帧位（0..N-1），用于计数与缩略图高亮 */
  const realPos = computed(() => {
    const n = N.value;
    return n ? (((index.value - cloneOffset.value) % n) + n) % n : 0;
  });

  /** 绑到轨道的 style：位移 = 当前位置 + 拖拽偏移 */
  const trackStyle = computed(() => ({
    transform: `translateX(calc(${-index.value * 100}% + ${offset.value}px))`,
  }));

  function goTo(i: number) {
    if (lock.value || !N.value) return;
    const clamped = Math.max(0, Math.min(N.value - 1, i));
    index.value = clamped + cloneOffset.value;
    offset.value = 0;
  }

  /** 相邻切换：落到克隆帧后等 transform 过渡结束再静默复位 */
  function shift(dir: 1 | -1) {
    if (lock.value || !N.value) return;
    const n = N.value;
    const base = cloneOffset.value;
    const nextIndex = index.value + dir;
    // 非无限模式到边界则不动
    if (!cloned.value && (nextIndex < base || nextIndex > base + n - 1)) return;
    lock.value = true;
    pendingWrap.value =
      nextIndex === base + n ? base : nextIndex === base - 1 ? base + n - 1 : null;
    index.value = nextIndex;
    offset.value = 0;
    // 兜底：覆盖 reduced-motion / 过渡被打断等不触发 transitionend 的情况
    window.setTimeout(finishShift, transitionMs + 120);
  }

  const prev = () => shift(-1);
  const next = () => shift(1);

  /** 过渡结束（或兜底定时器）收尾：必要时静默复位克隆帧并释放锁 */
  function finishShift() {
    if (!lock.value) return;
    if (pendingWrap.value !== null) {
      const target = pendingWrap.value;
      pendingWrap.value = null;
      noTransition.value = true;
      index.value = target;
      requestAnimationFrame(() => {
        noTransition.value = false;
      });
    }
    lock.value = false;
  }

  function onTransitionEnd(e: TransitionEvent) {
    if (e.propertyName !== 'transform') return;
    finishShift();
  }

  /* Pointer 手势：触屏 / 鼠标统一 */
  function onPointerDown(e: PointerEvent) {
    if (lock.value) return;
    dragging.value = true;
    moved.value = false;
    startX.value = e.clientX;
    offset.value = 0;
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging.value) return;
    const dx = e.clientX - startX.value;
    offset.value = dx;
    if (Math.abs(dx) > 6) moved.value = true;
  }

  function onPointerUp() {
    if (!dragging.value) return;
    dragging.value = false;
    const dx = offset.value;
    offset.value = 0;
    if (dx <= -threshold) next();
    else if (dx >= threshold) prev();
  }

  function onPointerCancel() {
    if (!dragging.value) return;
    dragging.value = false;
    moved.value = false;
    offset.value = 0;
  }

  function onTrackClick() {
    if (moved.value) {
      moved.value = false;
      return;
    }
    next();
  }

  // 帧数量 / 回环模式变化时重置（例如异步数据到位、切换到不同条目）
  watch([N, cloned], () => {
    index.value = cloneOffset.value;
    offset.value = 0;
    pendingWrap.value = null;
    lock.value = false;
  });

  return {
    slides,
    N,
    index,
    realPos,
    offset,
    dragging,
    noTransition,
    moved,
    trackStyle,
    goTo,
    prev,
    next,
    onTransitionEnd,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    onTrackClick,
  };
}
