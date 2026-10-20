<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import NewsCard from '@/components/NewsCard.vue';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import { useNewsStore } from '@/stores/news';
import { useUserStore } from '@/stores/user';
import type { NewsItem } from '@/types';

const router = useRouter();
const news = useNewsStore();
const user = useUserStore();

/**
 * 列表解析（E8 / P1-79）：内存命中优先，未命中**回退到本地阅读副本**。
 *
 * 回退是离线阅读的关键：收藏的资讯**曾在详情页打开过**（否则不会进收藏流程的
 * 常见路径），副本里就有正文；断网重启后内存已空，只能靠副本。
 */
const items = computed<NewsItem[]>(() =>
  user.favorites
    .map((id) => news.getById(id) ?? news.offlineDetail(id))
    .filter((x): x is NewsItem => x !== null),
);

function open(id: string) {
  router.push(`/news/${id}`);
}
</script>

<template>
  <div class="fav">
    <AppNav variant="title" title="我的收藏" show-back @back="router.back()" />

    <div v-if="items.length" class="fav__list">
      <NewsCard v-for="item in items" :key="item.id" :item="item" @click="open" />
    </div>

    <EmptyState
      v-else
      icon="favorite"
      title="还没有收藏的内容"
      description="在资讯详情页点击收藏，喜欢的内容会保存在这里"
    >
      <Button variant="primary" label="去发现" @click="router.push('/')" />
    </EmptyState>
  </div>
</template>

<style scoped>
.fav {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.fav__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}
</style>
