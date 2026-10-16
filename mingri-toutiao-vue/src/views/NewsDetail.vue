<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import NewsCard from '@/components/NewsCard.vue';
import { useUserStore } from '@/stores/user';
import { useCommentsStore } from '@/stores/comments';
import { useNewsDetail } from '@/composables/useNewsDetail';
import { bgFor } from '@/utils/image';
import { fetchRelated } from '@/api/news';
import type { Author, NewsItem } from '@/types';

const route = useRoute();
const router = useRouter();
const user = useUserStore();
const comments = useCommentsStore();

const { item, resolving } = useNewsDetail(() => route.params.id as string);
const commentCount = computed(() => (item.value ? comments.countOf(item.value.id) : 0));
const thumbCount = computed(() => Math.min(item.value?.images?.length ?? 0, 3));
const author = computed<Author | undefined>(() =>
  item.value ? { name: item.value.source, time: item.value.time } : undefined,
);

/**
 * 正文段落（E1）：后端按段落以 \n 分隔下发，这里切段渲染。
 * 没有正文时返回空数组——显示「暂无正文」，**不再有写死的占位文案**：
 * 界面假装有内容比承认没有内容更伤信任。
 */
const paragraphs = computed(() =>
  (item.value?.content ?? '')
    .split('\n')
    .map((p) => p.trim())
    .filter(Boolean),
);

/** 视频变体且有真实地址时可播放 */
const hasVideo = computed(
  () => item.value?.variant === 'video' && !!(item.value?.videoUrl ?? '').trim(),
);

/** 真实图片 URL 优先，缺省回退渐变占位 */
function coverStyle(seed: string, url?: string): Record<string, string> {
  return { backgroundImage: bgFor(seed, url) };
}

function formatCount(n?: number): string {
  if (!n) return '0';
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n);
}

/** 进入详情即记浏览历史（item 就绪后触发） */
watch(
  item,
  (val) => {
    if (val) user.addHistory(val.id);
  },
  { immediate: true },
);

/** 相关推荐：服务端按标题相似度/同频道召回，失败静默降级 */
const related = ref<NewsItem[]>([]);
watch(
  item,
  async (val) => {
    related.value = [];
    if (val) related.value = await fetchRelated(val.id);
  },
  { immediate: true },
);

function openRelated(id: string) {
  router.push(`/news/${id}`);
}

function onFavorite() {
  if (item.value) user.toggleFavorite(item.value.id);
}
</script>

<template>
  <div v-if="item" class="detail">
    <AppNav variant="detail" :author="author" show-back show-more @back="router.back()" />
    <div class="detail__scroll">
      <h1 class="detail__title">{{ item.title }}</h1>
      <div class="detail__meta">
        <span>{{ item.source }}</span>
        <span class="detail__dot">·</span>
        <span v-if="item.variant === 'video'">{{ formatCount(item.playCount) }}播放</span>
        <span v-else>{{ formatCount(item.commentCount) }}评论</span>
        <span class="detail__dot">·</span>
        <span>{{ item.time }}</span>
      </div>

      <div class="detail__gallery">
        <video
          v-if="hasVideo"
          class="detail__video"
          :src="item.videoUrl"
          :poster="item.cover || undefined"
          controls
          preload="metadata"
        />
        <button
          v-else
          class="detail__cover detail__cover--btn"
          :style="coverStyle(item.id, item.cover)"
          @click="router.push(`/news/${item.id}/gallery`)"
        >
          <span class="detail__atlas">图集 ›</span>
        </button>
        <div v-if="thumbCount" class="detail__thumbs">
          <div
            v-for="i in thumbCount"
            :key="i"
            class="detail__thumb"
            :style="coverStyle(item.id + (i - 1), item.images?.[i - 1])"
            @click="router.push(`/news/${item.id}/gallery`)"
          />
        </div>
      </div>

      <!-- E1：真实正文按段渲染；源未提供正文时显式说明，不用占位文案冒充 -->
      <template v-if="paragraphs.length">
        <p v-for="(p, i) in paragraphs" :key="i" class="detail__para">{{ p }}</p>
      </template>
      <p v-else class="detail__para detail__para--empty">暂无正文</p>

      <!-- 相关推荐：给详情页一个出口，避免读完后即离开 -->
      <section v-if="related.length" class="detail__related">
        <h2 class="detail__related-title">相关推荐</h2>
        <div class="detail__related-list">
          <NewsCard v-for="r in related" :key="r.id" :item="r" @click="openRelated" />
        </div>
      </section>
    </div>

    <div class="detail__bar">
      <div class="detail__bar-actions">
        <Button
          variant="secondary"
          :label="user.isFavorite(item.id) ? '已收藏' : '收藏'"
          @click="onFavorite"
        />
        <Button
          variant="secondary"
          :label="commentCount ? `评论 ${commentCount}` : '评论'"
          @click="router.push(`/news/${item.id}/comments`)"
        />
      </div>
      <Button variant="primary" block label="返回首页" @click="router.push('/')" />
    </div>
  </div>

  <!-- 深链加载中：先显示骨架，避免误判为「内容不存在」 -->
  <div v-else-if="resolving" class="detail-skeleton">
    <div class="detail-skeleton__line detail-skeleton__line--title" />
    <div class="detail-skeleton__line detail-skeleton__line--meta" />
    <div class="detail-skeleton__block" />
    <div class="detail-skeleton__line" />
    <div class="detail-skeleton__line" />
    <div class="detail-skeleton__line detail-skeleton__line--short" />
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
.detail {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--color-card-bg);
}

.detail__scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px 16px 24px;
}

.detail__title {
  margin: 0 0 12px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text-primary);
}

.detail__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
  margin-bottom: 16px;
}

.detail__dot {
  color: var(--color-text-secondary);
}

.detail__gallery {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 16px;
}

.detail__cover {
  width: 100%;
  height: 220px;
  border-radius: var(--radius-card);
  background-size: cover;
  background-position: center;
}

.detail__cover--btn {
  position: relative;
  border: none;
  padding: 0;
  cursor: pointer;
}

.detail__video {
  width: 100%;
  height: 220px;
  border-radius: var(--radius-card);
  background: #000;
  object-fit: cover;
  display: block;
}

.detail__atlas {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 3px 10px;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
}

.detail__thumbs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.detail__thumb {
  aspect-ratio: 1 / 0.72;
  border-radius: var(--radius-card);
  background-size: cover;
  background-position: center;
}

.detail__para {
  margin: 0 0 14px;
  font-size: 16px;
  line-height: 1.8;
  color: var(--color-text-primary);
}

/* E1：暂无正文——弱化的说明文案，不伪装成内容 */
.detail__para--empty {
  padding: 24px 0;
  text-align: center;
  font-size: 14px;
  color: var(--color-text-secondary);
}

.detail__related {
  margin-top: 20px;
  padding-top: 12px;
  border-top: 1px solid var(--color-divider);
}

.detail__related-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.detail__related-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail__bar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid var(--color-divider);
  background: var(--color-card-bg);
  flex-shrink: 0;
}

.detail__bar-actions {
  display: flex;
  gap: 12px;
}

.detail__bar-actions :deep(.mt-btn) {
  flex: 1;
}

/* 深链加载骨架 */
.detail-skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px 16px;
  min-height: 100%;
  background: var(--color-card-bg);
}

.detail-skeleton__line {
  height: 14px;
  border-radius: 4px;
  background: var(--color-divider);
}

.detail-skeleton__line--title {
  height: 24px;
  width: 82%;
}

.detail-skeleton__line--meta {
  width: 40%;
}

.detail-skeleton__line--short {
  width: 60%;
}

.detail-skeleton__block {
  height: 220px;
  border-radius: var(--radius-card);
  background: var(--color-divider);
  margin: 6px 0;
}
</style>
