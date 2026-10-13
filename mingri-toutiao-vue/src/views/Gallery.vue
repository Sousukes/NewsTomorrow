<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import { useCarousel } from '@/composables/useCarousel';
import { useNewsDetail } from '@/composables/useNewsDetail';
import { bgFor } from '@/utils/image';

const route = useRoute();
const router = useRouter();

// 深链安全：内存无此条时自动按 id 拉取
const { item } = useNewsDetail(() => route.params.id as string);

// 图集帧：封面 + 三图 + 补帧，凑足 6 张以对齐 S15「图集 · 1/6」
const frames = computed<string[]>(() => {
  if (!item.value) return [];
  const base = item.value.id;
  const list: string[] = [];
  if (item.value.cover) list.push(base + 'cover');
  (item.value.images ?? []).slice(0, 3).forEach((_, i) => list.push(base + 'img' + i));
  while (list.length < 6) list.push(base + 'pad' + list.length);
  return list.slice(0, 6);
});

function frameBg(seed: string): string {
  return bgFor(seed, frameUrl(seed));
}

/** 帧种子 → 真实图片 URL（cover / images[i]，补帧无图走渐变） */
function frameUrl(seed: string): string | undefined {
  if (!item.value) return undefined;
  if (seed.endsWith('cover')) return item.value.cover;
  const m = seed.match(/img(\d)$/);
  if (m) return item.value.images?.[Number(m[1])];
  return undefined;
}

const {
  slides,
  N,
  realPos,
  trackStyle,
  dragging,
  noTransition,
  goTo,
  prev,
  next,
  onTransitionEnd,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  onTrackClick,
} = useCarousel({ frames });
</script>

<template>
  <div v-if="item && frames.length" class="gallery">
    <header class="gallery__nav">
      <button class="gallery__icon" aria-label="返回" @click="router.back()">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path
            d="M15 5l-7 7 7 7"
            stroke="#fff"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <span class="gallery__count">图集 · {{ realPos + 1 }}/{{ N }}</span>
      <button class="gallery__icon" aria-label="更多">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="5" cy="12" r="1.6" fill="#fff" />
          <circle cx="12" cy="12" r="1.6" fill="#fff" />
          <circle cx="19" cy="12" r="1.6" fill="#fff" />
        </svg>
      </button>
    </header>

    <div
      class="gallery__viewport"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerCancel"
      @click="onTrackClick"
    >
      <div
        class="gallery__track"
        :class="{ 'is-dragging': dragging || noTransition }"
        :style="trackStyle"
        @transitionend="onTransitionEnd"
      >
        <div class="gallery__slide" v-for="(f, i) in slides" :key="i">
          <div class="gallery__pic" :style="{ backgroundImage: frameBg(f) }" />
        </div>
      </div>
      <button class="gallery__arrow gallery__arrow--left" aria-label="上一张" @click.stop="prev">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
          <path
            d="M15 5l-7 7 7 7"
            stroke="#fff"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <button class="gallery__arrow gallery__arrow--right" aria-label="下一张" @click.stop="next">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
          <path
            d="M9 5l7 7-7 7"
            stroke="#fff"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>

    <div class="gallery__thumbs">
      <button
        v-for="(f, i) in frames"
        :key="i"
        class="gallery__thumb"
        :class="{ 'is-active': i === realPos }"
        :style="{ backgroundImage: frameBg(f) }"
        :aria-label="`第 ${i + 1} 张`"
        @click="goTo(i)"
      />
    </div>
  </div>

  <EmptyState
    v-else
    icon="network"
    title="内容不存在或已下架"
    description="该资讯可能已被删除，换条新闻看看吧"
  >
    <Button variant="primary" label="返回首页" @click="router.push('/')" />
  </EmptyState>
</template>

<style scoped>
.gallery {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: #111418;
}

.gallery__nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 12px;
  flex-shrink: 0;
}

.gallery__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  padding: 0;
  cursor: pointer;
}

.gallery__count {
  font-size: 14px;
  color: #fff;
  font-family: var(--font-num);
}

.gallery__viewport {
  position: relative;
  flex: 1;
  overflow: hidden;
  display: flex;
  align-items: center;
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.gallery__viewport:active {
  cursor: grabbing;
}

.gallery__track {
  display: flex;
  width: 100%;
  height: 100%;
  transition: transform 0.25s ease;
}

.gallery__track.is-dragging {
  transition: none;
}

.gallery__slide {
  flex: 0 0 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 8px;
}

.gallery__pic {
  width: 100%;
  height: 100%;
  max-height: 560px;
  border-radius: 12px;
  background-size: cover;
  background-position: center;
}

.gallery__arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.35);
  cursor: pointer;
}

.gallery__arrow--left {
  left: 12px;
}

.gallery__arrow--right {
  right: 12px;
}

.gallery__thumbs {
  display: flex;
  gap: 8px;
  padding: 16px 12px 24px;
  overflow-x: auto;
  flex-shrink: 0;
}

.gallery__thumb {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  border-radius: 8px;
  border: 2px solid transparent;
  background-size: cover;
  background-position: center;
  cursor: pointer;
  padding: 0;
}

.gallery__thumb.is-active {
  border-color: var(--color-primary);
}
</style>
