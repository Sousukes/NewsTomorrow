<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import NewsCard from '@/components/NewsCard.vue';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import { fetchHotTerms, type HotTerm } from '@/api/news';
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
const hasMore = ref(false);
const loading = ref(false);
const error = ref('');

/**
 * 零结果引导用的热搜词（E2 / P1-71）。
 *
 * 失败降级为空数组：热搜词是**零结果时的备选路径**，拿不到就不显示那一行，
 * 换词入口仍然可用——不能让"引导用的内容"自己变成新的报错源。
 */
const hotTerms = ref<HotTerm[]>([]);
onMounted(() => {
  void fetchHotTerms(6)
    .then((list) => (hotTerms.value = list))
    .catch(() => (hotTerms.value = []));
});

/** 点热搜词 = 换一次搜索：改写 URL query，watch 会触发重新查询 */
function retry(term: string) {
  void router.push({ path: '/search/results', query: { q: term } });
}

/**
 * 零结果文案把"已下架"这一例外讲清楚（懒标注的做法里没有这条）。
 * 用户搜一个刚被下架的话题拿到 0 结果，最容易得出的结论是"搜索坏了"。
 */
const zeroDesc = computed(() =>
  error.value ? '搜索服务暂不可用，换个关键词或稍后再试' : '换个关键词试试，或浏览首页推荐',
);

/** 追加时按 id 去重：翻页之间若有新增/排序变动，同一条可能出现在两页 */
function mergeUnique(prev: NewsItem[], next: NewsItem[]): NewsItem[] {
  const seen = new Set(prev.map((i) => i.id));
  return [...prev, ...next.filter((i) => !seen.has(i.id))];
}

async function runSearch(nextPage = 1) {
  const keyword = q.value.trim();
  if (!keyword) {
    results.value = [];
    total.value = 0;
    hasMore.value = false;
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
    // 第 1 页是「新搜索」，直接替换；后续页是「加载更多」，必须**追加**——
    // 此前整段赋值，用户点一次「加载更多」就只能看到最后一页，按钮文案
    // 「加载更多（x/total）」与实际行为完全不符。
    results.value = nextPage === 1 ? res.list : mergeUnique(results.value, res.list);
    total.value = res.total;
    page.value = res.page;
    // 用服务端 hasMore 判定是否还有下一页，而不是 `results.length >= total`：
    // total 是 offset 分页的 count 估算，与去重后的本地长度不是同一个量，
    // 用它当终止条件会出现「按钮还在但点出来空」或「明明还有却提前消失」。
    hasMore.value = res.hasMore;
    // 搜索接口异常时 store 已降级为本地过滤（hasMore=false），这里给出提示
    error.value = news.searchError;
  } finally {
    loading.value = false;
  }
}

function open(id: string) {
  router.push(`/news/${id}`);
}

function loadMore() {
  if (loading.value || !hasMore.value) return;
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
      <button v-if="hasMore" class="results__more-btn" :disabled="loading" @click="loadMore">
        {{ loading ? '加载中…' : `加载更多（${results.length}/${total}）` }}
      </button>
      <span v-else class="results__end">没有更多了</span>
    </div>

    <!-- 骨架屏 -->
    <div v-else-if="loading" class="results__list">
      <div v-for="i in 3" :key="i" class="results__skeleton" />
    </div>

    <EmptyState v-else icon="search" title="没有找到相关内容" :description="zeroDesc">
      <div v-if="hotTerms.length" class="results__retry">
        <p class="results__retry-h">试试这些热搜词</p>
        <div class="results__retry-tags">
          <button
            v-for="t in hotTerms.slice(0, 6)"
            :key="t.term"
            class="results__retry-tag"
            @click="retry(t.term)"
          >
            {{ t.term }}
          </button>
        </div>
      </div>
      <Button variant="primary" label="换个关键词" @click="router.push('/search')" />
      <Button variant="secondary" label="返回首页" @click="router.push('/')" />
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

/* 零结果引导（E2）：热搜词给"下一步"，换词按钮保留原路 */
.results__retry {
  margin-bottom: 14px;
  text-align: center;
}

.results__retry-h {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.results__retry-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}

.results__retry-tag {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-page-bg);
  font-size: 12px;
  color: var(--color-text-primary);
  cursor: pointer;
}

.results__retry-tag:hover {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.results__skeleton {
  height: 96px;
  margin: 0 8px;
  border-radius: var(--radius-card);
  background: var(--color-divider);
}
</style>
