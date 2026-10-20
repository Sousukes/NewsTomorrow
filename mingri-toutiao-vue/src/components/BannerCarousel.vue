<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useCarousel } from '@/composables/useCarousel';
import { proxiedImageUrl } from '@/utils/image';
import type { NewsItem } from '@/types';

const props = withDefaults(
  defineProps<{
    /** 轮播帧数据 */
    items: NewsItem[];
    /** 自动播放间隔(ms)，0 表示不自动播放 */
    interval?: number;
  }>(),
  { interval: 4000 },
);

const emit = defineEmits<{ (e: 'select', id: string): void }>();

const frames = computed(() => props.items.map((i) => i.id));

/** id → 条目 的索引表。
 *
 * 此前每帧渲染都要 `props.items.find()`（配图与标题各一次），
 * 帧数 × 列表长度 = O(n²) 且每次渲染重算；改为一次建表后查表 O(1)。
 */
const itemById = computed(() => new Map(props.items.map((i) => [i.id, i])));

const {
  slides,
  N,
  realPos,
  trackStyle,
  dragging,
  noTransition,
  moved,
  goTo,
  next,
  onTransitionEnd,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
} = useCarousel({ frames });

const palette = [
  'linear-gradient(135deg,#4e8bff,#1e4fd8)',
  'linear-gradient(135deg,#2f6bd8,#16337a)',
  'linear-gradient(135deg,#5b93ff,#274f9e)',
  'linear-gradient(135deg,#3a74e0,#122a63)',
];

/** 真实封面（协议白名单 + 图片代理 B4）；无则返回空串 */
function coverOf(id: string): string {
  return proxiedImageUrl(itemById.value.get(id)?.cover ?? '', 960);
}

/** 无真实封面时的品牌蓝渐变兜底 */
function slideBg(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

function titleOf(id: string): string {
  return itemById.value.get(id)?.title ?? '';
}

/** 拖拽(peek)后紧跟的点击不应触发跳转 */
function onSlideClick(id: string) {
  if (moved.value) {
    moved.value = false;
    return;
  }
  emit('select', id);
}

/* 自动播放：悬停 / 拖拽时暂停，离开恢复；到末尾回环（克隆帧） */
const hovering = ref(false);
let timer: ReturnType<typeof setInterval> | null = null;

function stopAuto() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

function startAuto() {
  stopAuto();
  if (props.interval > 0 && N.value > 1 && !hovering.value && !dragging.value) {
    timer = setInterval(() => next(), props.interval);
  }
}

watch([N, hovering, dragging], startAuto);
onBeforeUnmount(stopAuto);
startAuto();
</script>

<template>
  <div v-if="N" class="banner" @mouseenter="hovering = true" @mouseleave="hovering = false">
    <div
      class="banner__viewport"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerCancel"
    >
      <div
        class="banner__track"
        :class="{ 'is-dragging': dragging || noTransition }"
        :style="trackStyle"
        @transitionend="onTransitionEnd"
      >
        <!--
          key 必须是「轨道下标 + id」组合：
          无限回环时 slides = [末帧, ...全部, 首帧]，首帧与末帧各出现两次，
          仅用 id 作为 key 会产生重复 key，触发 Vue 告警并可能错误复用 DOM。
        -->
        <!--
          E9：轮播位同样是**链接**而非可点的 div。`.exact.prevent` 只接管
          无修饰键的点击（滑动后由 `moved` 守卫吞掉），中键/右键仍是原生行为。
        -->
        <a
          v-for="(id, i) in slides"
          :key="`${id}-${i}`"
          class="banner__slide"
          :href="`/news/${id}`"
          :style="{ backgroundImage: slideBg(id) }"
          @click.exact.prevent="onSlideClick(id)"
        >
          <img
            v-if="coverOf(id)"
            class="banner__img"
            :src="coverOf(id)"
            :alt="titleOf(id)"
            loading="lazy"
            decoding="async"
          />
          <span class="banner__title">{{ titleOf(id) }}</span>
        </a>
      </div>
    </div>

    <div class="banner__dots">
      <button
        v-for="(it, i) in items"
        :key="it.id"
        class="banner__dot"
        :class="{ 'is-active': i === realPos }"
        :aria-label="`第 ${i + 1} 张`"
        @click="goTo(i)"
      />
    </div>
  </div>
</template>

<style scoped>
.banner {
  padding: 8px 0;
}

.banner__viewport {
  overflow: hidden;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.banner__viewport:active {
  cursor: grabbing;
}

.banner__track {
  display: flex;
  transition: transform 0.25s ease;
}

.banner__track.is-dragging {
  transition: none;
}

.banner__slide {
  /* E9：作为链接渲染后要显式去掉下划线与默认色，视觉与改动前一致 */
  text-decoration: none;
  color: inherit;
  flex: 0 0 100%;
  height: 132px;
  margin: 0 8px;
  display: flex;
  align-items: flex-end;
  padding: 14px;
  border-radius: var(--radius-card);
  background-size: cover;
  background-position: center;
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

.banner__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.banner__title {
  position: relative;
  z-index: 1;
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}

.banner__dots {
  display: flex;
  justify-content: center;
  gap: 6px;
  margin-top: 8px;
}

.banner__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  border: none;
  background: var(--color-text-secondary);
  opacity: 0.4;
  padding: 0;
  cursor: pointer;
  transition:
    width 0.2s ease,
    opacity 0.2s ease;
}

.banner__dot.is-active {
  width: 16px;
  border-radius: 3px;
  background: var(--color-primary);
  opacity: 1;
}
</style>
