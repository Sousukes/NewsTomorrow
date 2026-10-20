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
  user.history
    .map((id) => news.getById(id) ?? news.offlineDetail(id))
    .filter((x): x is NewsItem => x !== null),
);

function open(id: string) {
  router.push(`/news/${id}`);
}

function clear() {
  user.clearHistory();
}
</script>

<template>
  <div class="history">
    <AppNav variant="title" title="浏览历史" show-back @back="router.back()" />

    <div v-if="items.length" class="history__head">
      <span class="history__count">共 {{ items.length }} 条</span>
      <button class="history__clear" type="button" @click="clear">清空</button>
    </div>

    <div v-if="items.length" class="history__list">
      <NewsCard v-for="item in items" :key="item.id" :item="item" @click="open" />
    </div>

    <EmptyState
      v-else
      icon="history"
      title="暂无浏览历史"
      description="你最近看过的资讯会出现在这里"
    >
      <Button variant="primary" label="去首页" @click="router.push('/')" />
    </EmptyState>
  </div>
</template>

<style scoped>
.history {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.history__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.history__count {
  font-size: 13px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.history__clear {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
}

.history__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}
</style>
