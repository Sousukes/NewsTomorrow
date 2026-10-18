<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';
import Button from '@/components/Button.vue';
import BannerCarousel from '@/components/BannerCarousel.vue';
import EmptyState from '@/components/EmptyState.vue';
import { useNewsStore } from '@/stores/news';
import { useFollowsStore } from '@/stores/follows';
import { useUserStore } from '@/stores/user';
import { useInfiniteList } from '@/composables/useInfiniteList';
import { HOME_TABS, FOLLOWING_FEED, toChannel, type ListKey } from '@/constants/channels';
import { DEFAULT_CHANNEL } from '@/constants/channels';
import { SORT_MODES, DEFAULT_SORT, toSort, type SortMode } from '@/constants/sort';
import type { ChannelTab } from '@/types';

const router = useRouter();
const news = useNewsStore();
const follows = useFollowsStore();
const user = useUserStore();

/**
 * 首页标签（E6b）：**关注在最左**，其余是真实频道。
 * 它来自 UI 级组合 `HOME_TABS`——刻意不进 `CHANNELS`，否则后端校验运营频道时
 * 会多出一个非法选项，且「关注」与频道本就是正交概念。
 */
const channels: ChannelTab[] = HOME_TABS.map((t) => ({ ...t }));

const activeTab = ref<ListKey>(DEFAULT_CHANNEL);
const activeSort = ref<SortMode>(DEFAULT_SORT);

const isFollowing = computed(() => activeTab.value === FOLLOWING_FEED);

/**
 * 本视图只读「自己的桶」（A4）：列表状态按 (列表键 × 排序) 隔离，
 * 视频页等其他视图的请求不会覆盖这里的列表。关注流与频道共用同一张桶表。
 */
const bucket = computed(() => news.bucketOf(activeTab.value, activeSort.value));

/** 无限滚动：哨兵进入视口即加载下一页（续接本视图的桶） */
const { sentinel } = useInfiniteList({
  onLoadMore: () => news.loadMore(activeTab.value, activeSort.value),
  hasMore: () => bucket.value.hasMore,
  loading: () => bucket.value.loading,
});

onMounted(() => {
  news.resetPaging(DEFAULT_CHANNEL, DEFAULT_SORT);
  void news.fetchNews(DEFAULT_CHANNEL, DEFAULT_SORT);
  // 目录是公开静态数据：已加载过就不再打接口（关注按钮只在详情页用得到）
  if (!follows.loaded) void follows.loadDirectory();
});

/** 标签栏 emit 的是 string，归一化后再请求（不可信输入兜底） */
function onTab(key: string) {
  const next: ListKey = key === FOLLOWING_FEED ? FOLLOWING_FEED : toChannel(key);
  if (next === activeTab.value) return;
  activeTab.value = next;
  news.resetPaging(next, activeSort.value);
  // 保持当前排序：切换标签不应把用户选的排序重置掉
  void news.fetchNews(next, activeSort.value);
}

/** 关注流的三种空态必须分开说：混成一句"暂无内容"会让人以为功能坏了 */
const followEmpty = computed(() => {
  if (!isFollowing.value) return false;
  if (bucket.value.loading || bucket.value.list.length || bucket.value.error) return false;
  if (!user.isLoggedIn) return 'guest' as const;
  return follows.keys.length ? 'empty' : ('none' as const);
});

function goLogin() {
  void router.push('/login');
}

function goSources() {
  void router.push('/sources');
}

/** 切换排序：必须 resetPaging —— 排序键变了，旧游标失效 */
function onSort(key: string) {
  const sort = toSort(key);
  if (sort === activeSort.value) return;
  activeSort.value = sort;
  news.resetPaging(activeTab.value, sort);
  void news.fetchNews(activeTab.value, sort);
}

function open(id: string) {
  router.push(`/news/${id}`);
}
</script>

<template>
  <div class="home">
    <AppNav
      variant="brand"
      show-search
      show-message
      @search="router.push('/search')"
      @message="router.push('/messages')"
    />
    <ChannelTabs :tabs="channels" :active="activeTab" @update:active="onTab" />

    <div class="home__sort">
      <button
        v-for="s in SORT_MODES"
        :key="s.key"
        class="home__sort-btn"
        :class="{ 'is-active': activeSort === s.key }"
        @click="onSort(s.key)"
      >
        {{ s.label }}
      </button>
      <span v-if="activeSort === 'for-you'" class="home__sort-note">按你的浏览与收藏偏好排序</span>
    </div>

    <BannerCarousel :items="bucket.list.slice(0, 3)" @select="open" />

    <div class="home__feed">
      <!-- 首屏骨架 -->
      <template v-if="bucket.loading && !bucket.list.length">
        <div v-for="i in 4" :key="`sk-${i}`" class="home__skeleton" />
      </template>

      <NewsCard v-for="item in bucket.list" :key="item.id" :item="item" @click="open" />

      <!-- E6b：关注流的三种空态分开说（未登录 / 未关注 / 已关注暂无内容） -->
      <EmptyState
        v-if="followEmpty === 'guest'"
        icon="message"
        title="登录后查看关注的内容"
        description="关注来源后，它们发布的内容会集中到这里"
      >
        <Button variant="primary" label="去登录" @click="goLogin" />
      </EmptyState>
      <EmptyState
        v-else-if="followEmpty === 'none'"
        icon="search"
        title="还没有关注任何来源"
        description="去来源列表看看，关注后这里就有内容了"
      >
        <Button variant="primary" label="去发现来源" @click="goSources" />
      </EmptyState>
      <EmptyState
        v-else-if="followEmpty === 'empty'"
        icon="network"
        title="关注的来源暂时没有新内容"
        description="换到「推荐」看看，或稍后再来"
      />

      <!-- 触底哨兵 + 状态提示 -->
      <div ref="sentinel" class="home__sentinel">
        <span v-if="bucket.loadingMore" class="home__hint">
          <span class="home__spinner" /> 加载中…
        </span>
        <span v-else-if="bucket.error" class="home__hint home__hint--error">
          {{ bucket.error }}
        </span>
        <span v-else-if="!bucket.hasMore && bucket.list.length" class="home__hint">没有更多了</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.home__sort {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 4px;
}

.home__sort-btn {
  padding: 4px 12px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: 14px;
  cursor: pointer;
  transition:
    color 0.15s,
    border-color 0.15s;
}

.home__sort-btn.is-active {
  color: var(--color-primary);
  border-color: var(--color-primary);
  font-weight: 500;
}

.home__sort-note {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.home__feed {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
}

.home__skeleton {
  height: 108px;
  margin: 0 8px;
  border-radius: var(--radius-card);
  background: var(--color-divider);
}

.home__sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 48px;
  padding: 8px 0 20px;
}

.home__hint {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-secondary);
}

.home__hint--error {
  color: var(--color-error);
}

.home__spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: home-spin 0.8s linear infinite;
}

@keyframes home-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
