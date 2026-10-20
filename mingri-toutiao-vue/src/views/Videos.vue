<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import EmptyState from '@/components/EmptyState.vue';
import NewsCard from '@/components/NewsCard.vue';
import { useNewsStore } from '@/stores/news';
import { useInfiniteList } from '@/composables/useInfiniteList';
import { DEFAULT_SORT } from '@/constants/sort';
import type { Channel, Tab } from '@/types';

const router = useRouter();
const news = useNewsStore();

/**
 * 视频页子分类：直播/影视/搞笑**不是资讯频道**（后端无对应 channel），
 * 因此用通用的 `Tab` 而非 `ChannelTab`。
 * 注意：这些子分类当前仅切换高亮，列表仍取 video 频道全量。
 */
const tabs: Tab[] = [
  { key: 'video', label: '推荐', active: true },
  { key: 'live', label: '直播' },
  { key: 'mv', label: '影视' },
  { key: 'fun', label: '搞笑' },
  { key: 'tech', label: '科技' },
];

const active = ref('video');

/** 本视图固定使用 video 频道桶（A4）：与首页的列表状态完全隔离，互不覆盖 */
const VIDEO_CHANNEL: Channel = 'video';
const bucket = computed(() => news.bucketOf(VIDEO_CHANNEL, DEFAULT_SORT));

/** 无限滚动：与首页同一套机制，但续接的是 video 桶自己的游标 */
const { sentinel } = useInfiniteList({
  onLoadMore: () => news.loadMore(VIDEO_CHANNEL, DEFAULT_SORT),
  hasMore: () => bucket.value.hasMore,
  loading: () => bucket.value.loading,
});

onMounted(() => {
  news.resetPaging(VIDEO_CHANNEL, DEFAULT_SORT);
  void news.fetchNews(VIDEO_CHANNEL, DEFAULT_SORT);
});

function onTab(key: string) {
  active.value = key;
}

function open(id: string) {
  router.push(`/news/${id}`);
}
</script>

<template>
  <div class="video">
    <AppNav
      variant="brand"
      show-search
      show-message
      @search="router.push('/search')"
      @message="router.push('/messages')"
    />
    <ChannelTabs :tabs="tabs" :active="active" @update:active="onTab" />
    <div class="video__feed">
      <template v-if="bucket.loading && !bucket.list.length">
        <div v-for="i in 3" :key="`sk-${i}`" class="video__skeleton" />
      </template>

      <!--
        空态（P1-85）：这一页原先只有骨架屏与列表，**没有空态**。
        而 video 频道当前恒为空（P1-84 已量证：全库 `video_url` 非空 0 条，
        抓取链路唯一写 `video_url` 的地方写的是 None）——也就是说进入这个 Tab
        的人**每次**都只看到一片空白，像页面坏了。空态要说清状态，并给一个出口。
      -->
      <EmptyState
        v-if="!bucket.loading && !bucket.list.length && !bucket.error"
        icon="collect"
        title="暂无视频内容"
        description="视频频道还没有内容，可以先去首页看看推荐资讯。"
      >
        <Button variant="primary" label="去看推荐" @click="router.push('/')" />
      </EmptyState>

      <NewsCard v-for="item in bucket.list" :key="item.id" :item="item" @click="open" />

      <div ref="sentinel" class="video__sentinel">
        <span v-if="bucket.loadingMore" class="video__hint">加载中…</span>
        <span v-else-if="bucket.error" class="video__hint video__hint--error">
          {{ bucket.error }}
        </span>
        <span v-else-if="!bucket.hasMore && bucket.list.length" class="video__hint">
          没有更多了
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.video {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.video__feed {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}

.video__skeleton {
  height: 120px;
  margin: 0 16px;
  border-radius: var(--radius-card);
  background: var(--color-card-bg);
}

.video__sentinel {
  padding: 12px 0 20px;
  text-align: center;
}

.video__hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.video__hint--error {
  color: var(--color-error);
}
</style>
