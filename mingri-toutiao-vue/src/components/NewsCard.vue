<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { bgFor, proxiedImageUrl } from '@/utils/image';
import type { NewsItem } from '@/types';

const props = defineProps<{ item: NewsItem }>();

const emit = defineEmits<{
  (e: 'click', id: string): void;
  (e: 'image', id: string, index: number): void;
  /**
   * 负向操作（E3）。**刻意不在这里调接口**：卡片只负责表达用户意图，
   * 由父级决定"举报弹窗怎么开、踩完要不要原地移除"。
   */
  (
    e: 'feedback',
    payload: { action: 'news' | 'source' | 'report'; id: string; sourceKey: string },
  ): void;
}>();

/** 负反馈菜单是否展开（默认收起：低频操作不该占视觉） */
const menuOpen = ref(false);

/** 「减少该来源」只在拿得到来源标识时给出——没有标识就别给点了会失败的选项 */
const canReduceSource = computed(() => Boolean(props.item.sourceKey));

function choose(action: 'news' | 'source' | 'report') {
  menuOpen.value = false;
  emit('feedback', { action, id: props.item.id, sourceKey: props.item.sourceKey ?? '' });
}

/** 点空白处收起：菜单是浮层，留在屏幕上会挡住后面的卡片 */
function onDocumentClick() {
  if (menuOpen.value) menuOpen.value = false;
}

watch(menuOpen, (open) => {
  if (open) document.addEventListener('click', onDocumentClick);
  else document.removeEventListener('click', onDocumentClick);
});

onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick));

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

      <!-- E3：负向操作入口。**不进主操作区**——与关注、收藏同属低频动作 -->
      <button
        class="mt-card__more"
        :aria-label="`${item.title} 的更多操作`"
        :aria-expanded="menuOpen"
        @click.stop="menuOpen = !menuOpen"
      >
        ×
      </button>

      <div v-if="menuOpen" class="mt-card__menu" role="menu" @click.stop>
        <button class="mt-card__menu-item" role="menuitem" @click="choose('news')">不感兴趣</button>
        <button
          v-if="canReduceSource"
          class="mt-card__menu-item"
          role="menuitem"
          @click="choose('source')"
        >
          减少「{{ item.source }}」的内容
        </button>
        <button class="mt-card__menu-item" role="menuitem" @click="choose('report')">举报</button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.mt-card {
  position: relative;
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

/* ---- E3 负反馈入口与菜单 ---- */

.mt-card__more {
  margin-left: auto;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.mt-card__more:hover {
  color: var(--color-text-primary);
}

.mt-card__menu {
  position: absolute;
  right: 12px;
  bottom: 34px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  min-width: 148px;
  padding: 4px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-card-bg);
  box-shadow: 0 6px 20px rgb(0 0 0 / 16%);
}

.mt-card__menu-item {
  padding: 9px 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-primary);
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.mt-card__menu-item:hover {
  background: var(--color-page-bg);
}
</style>
