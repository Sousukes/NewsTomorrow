<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import NewsCard from '@/components/NewsCard.vue';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import { useNewsStore } from '@/stores/news';
import { useUserStore } from '@/stores/user';
import type { NewsItem } from '@/types';

const route = useRoute();
const router = useRouter();
const news = useNewsStore();
const user = useUserStore();

const PAGE_SIZE = 20;

const q = ref((route.query.q as string) || '');
const results = ref<NewsItem[]>([]);
const total = ref(0);
const page = ref(1);
const loading = ref(false);
const error = ref('');

async function runSearch(nextPage = 1) {
  const keyword = q.value.trim();
  if (!keyword) {
    results.value = [];
    total.value = 0;
    return;
  }
  // 记录本地搜索历史。放在这里而非搜索页，可覆盖**所有入口**
  // （搜索页点热搜/历史、导航栏直达、外部深链），而不只是从搜索页发起的那一次。
  // 后端的热词统计由 search 接口在服务端自行记录，与此互不依赖。
  if (nextPage === 1) user.addSearchTerm(keyword);
  loading.value = true;
  error.value = '';
  try {
    const res = await news.search(keyword, nextPage, PAGE_SIZE);
    results.value = res.list;
    total.value = res.total;
    page.value = res.page;
    // 搜索接口异常时 store 已降级为本地过滤，这里给出提示
    error.value = news.searchError;
  } finally {
    loading.value = false;
  }
}

function open(id: string) {
  router.push(`/news/${id}`);
}

function loadMore() {
  if (loading.value || results.value.length >= total.value) return;
  runSearch(page.value + 1);
}

// 关键词变化（含从搜索页返回）时重新查询
watch(
  () => route.query.q,
  (val) => {
    q.value = (val as string) || '';
    runSearch(1);
  },
  { immediate: true },
);
</script>

<template>
  <div class="results">
    <AppNav variant="title" title="搜索结果" show-back @back="router.back()" />

    <div v-if="q" class="results__q">
      <template v-if="loading && !results.length">正在搜索…</template>
      <template v-else>“{{ q }}” 共 {{ total }} 条相关资讯</template>
    </div>

    <div v-if="error" class="results__tip">{{ error }}，已为你展示本地结果</div>

    <div v-if="results.length" class="results__list">
      <NewsCard v-for="item in results" :key="item.id" :item="item" @click="open" />
    </div>

    <!-- 加载更多 -->
    <div v-if="results.length" class="results__more">
      <button
        v-if="results.length < total"
        class="results__more-btn"
        :disabled="loading"
        @click="loadMore"
      >
        {{ loading ? '加载中…' : `加载更多（${results.length}/${total}）` }}
      </button>
      <span v-else class="results__end">没有更多了</span>
    </div>

    <!-- 骨架屏 -->
    <div v-else-if="loading" class="results__list">
      <div v-for="i in 3" :key="i" class="results__skeleton" />
    </div>

    <EmptyState
      v-else
      icon="search"
      title="没有找到相关内容"
      description="换个关键词试试，或浏览首页推荐"
    >
      <Button variant="primary" label="返回首页" @click="router.push('/')" />
    </EmptyState>
  </div>
</template>

<style scoped>
.results {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.results__q {
  padding: 10px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.results__tip {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--color-error);
  background: var(--color-error-bg);
  border-bottom: 1px solid var(--color-divider);
}

.results__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}

.results__more {
  padding: 12px 16px 20px;
  text-align: center;
}

.results__more-btn {
  padding: 8px 20px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
}

.results__more-btn:disabled {
  color: var(--color-text-secondary);
  cursor: default;
}

.results__end {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.results__skeleton {
  height: 96px;
  margin: 0 8px;
  border-radius: var(--radius-card);
  background: var(--color-divider);
}
</style>
