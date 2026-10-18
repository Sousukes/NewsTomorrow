<script setup lang="ts">
import { bgFor, proxiedImageUrl } from '@/utils/image';
import type { NewsItem } from '@/types';

defineProps<{ item: NewsItem }>();

const emit = defineEmits<{
  (e: 'click', id: string): void;
  (e: 'image', id: string, index: number): void;
}>();

/** 真实图片 URL（协议白名单 + 走图片代理 B4）；无则返回空串走渐变占位 */
function imgOf(url?: string | null): string {
  return proxiedImageUrl(url, 320);
}

/** 渐变占位样式（无真实配图时使用） */
function coverStyle(seed: string, url?: string): Record<string, string> {
  return { backgroundImage: bgFor(seed, url, 640) };
}

function formatCount(n?: number): string {
  if (!n) return '0';
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n);
}
</script>

<template>
  <article class="mt-card" @click="emit('click', item.id)">
    <!-- 纯文字 -->
    <template v-if="item.variant === 'text'">
      <div class="mt-card__head">
        <span v-if="item.isTop" class="mt-card__top">置顶</span>
        <h3 class="mt-card__title">{{ item.title }}</h3>
      </div>
      <p v-if="item.summary" class="mt-card__summary">{{ item.summary }}</p>
    </template>

    <!-- 左文右图 -->
    <template v-else-if="item.variant === 'left-right'">
      <div class="mt-card__lr">
        <div class="mt-card__lr-text">
          <h3 class="mt-card__title">{{ item.title }}</h3>
          <p v-if="item.summary" class="mt-card__summary">{{ item.summary }}</p>
        </div>
        <img
          v-if="imgOf(item.cover)"
          class="mt-card__cover mt-card__cover--sm"
          :src="imgOf(item.cover)"
          :alt="item.title"
          loading="lazy"
          decoding="async"
        />
        <div v-else class="mt-card__cover mt-card__cover--sm" :style="coverStyle(item.id)" />
      </div>
    </template>

    <!-- 三图 -->
    <template v-else-if="item.variant === 'triple'">
      <h3 class="mt-card__title">{{ item.title }}</h3>
      <div class="mt-card__triple">
        <template v-for="i in 3" :key="i">
          <img
            v-if="imgOf(item.images?.[i - 1])"
            class="mt-card__triple-img"
            :src="imgOf(item.images?.[i - 1])"
            :alt="`${item.title} 配图 ${i}`"
            loading="lazy"
            decoding="async"
            @click.stop="emit('image', item.id, i - 1)"
          />
          <div
            v-else
            class="mt-card__triple-img"
            :style="coverStyle(item.id + i, item.images?.[i - 1])"
            @click.stop="emit('image', item.id, i - 1)"
          />
        </template>
      </div>
    </template>

    <!-- 视频 -->
    <template v-else-if="item.variant === 'video'">
      <div class="mt-card__video">
        <img
          v-if="imgOf(item.cover)"
          class="mt-card__cover mt-card__cover--video"
          :src="imgOf(item.cover)"
          :alt="item.title"
          loading="lazy"
          decoding="async"
        />
        <div
          v-else
          class="mt-card__cover mt-card__cover--video"
          :style="coverStyle(item.id, item.cover)"
        />
        <span class="mt-card__play">▶ {{ formatCount(item.playCount) }}</span>
      </div>
      <h3 class="mt-card__title">{{ item.title }}</h3>
      <p v-if="item.summary" class="mt-card__summary">{{ item.summary }}</p>
    </template>

    <!-- 元信息：来源 · 评论/播放 · 时间 -->
    <div class="mt-card__meta">
      <span>{{ item.source }}</span>
      <template v-if="item.variant === 'video'">
        <span class="mt-card__dot">·</span>
        <span>{{ formatCount(item.playCount) }}播放</span>
      </template>
      <template v-else>
        <span class="mt-card__dot">·</span>
        <span>{{ formatCount(item.commentCount) }}评论</span>
      </template>
      <span class="mt-card__dot">·</span>
      <span>{{ item.time }}</span>
    </div>
  </article>
</template>

<style scoped>
.mt-card {
  background: var(--color-card-bg);
  border-radius: var(--radius-card);
  padding: 14px 16px;
  box-shadow: var(--shadow-card);
  cursor: pointer;
}

.mt-card__head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.mt-card__top {
  flex-shrink: 0;
  margin-top: 2px;
  padding: 1px 6px;
  border: 1px solid var(--color-primary);
  border-radius: 4px;
  color: var(--color-primary);
  font-size: 11px;
  line-height: 16px;
}

.mt-card__title {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
  line-height: 1.4;
  color: var(--color-text-primary);
}

.mt-card__summary {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.mt-card__lr {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.mt-card__lr-text {
  flex: 1;
  min-width: 0;
}

.mt-card__cover {
  background-size: cover;
  background-position: center;
  flex-shrink: 0;
  /* <img> 与渐变占位共用同一尺寸/裁剪规则 */
  object-fit: cover;
  display: block;
}

.mt-card__cover--sm {
  width: 110px;
  height: 76px;
  border-radius: var(--radius-card);
}

.mt-card__triple {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-top: 10px;
}

.mt-card__triple-img {
  aspect-ratio: 1 / 0.72;
  border-radius: var(--radius-card);
  background-size: cover;
  background-position: center;
  object-fit: cover;
  display: block;
  width: 100%;
}

.mt-card__video {
  position: relative;
  margin-top: 10px;
  border-radius: var(--radius-card);
  overflow: hidden;
}

.mt-card__cover--video {
  width: 100%;
  height: 200px;
}

.mt-card__play {
  position: absolute;
  left: 10px;
  bottom: 10px;
  padding: 3px 10px;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
  font-family: var(--font-num);
}

.mt-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.mt-card__dot {
  color: var(--color-text-secondary);
}
</style>
