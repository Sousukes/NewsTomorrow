<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';
import { useNewsStore } from '@/stores/news';

import type { Tab } from '@/types';

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

onMounted(() => news.fetchNews('video'));

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
      <NewsCard v-for="item in news.list" :key="item.id" :item="item" @click="open" />
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
</style>
