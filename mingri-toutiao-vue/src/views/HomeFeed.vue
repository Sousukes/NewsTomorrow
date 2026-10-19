<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import ChannelTabs from '@/components/ChannelTabs.vue';
import NewsCard from '@/components/NewsCard.vue';
import Button from '@/components/Button.vue';
import BannerCarousel from '@/components/BannerCarousel.vue';
import EmptyState from '@/components/EmptyState.vue';
import ReportDialog from '@/components/ReportDialog.vue';
import { useNewsStore } from '@/stores/news';
import { useFollowsStore } from '@/stores/follows';
import { useUserStore } from '@/stores/user';
import { useInfiniteList } from '@/composables/useInfiniteList';
import { useExposure } from '@/composables/useExposure';
import { addDislike, reportExposures } from '@/api/feedback';
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

/* ---------------- E3：曝光、负反馈、已读移除 ---------------- */

/**
 * 曝光判定（E3 / D2）：进入视口 ≥ 50% 且停留 ≥ 500ms 才算看过，攒批上报。
 *
 * **未登录不上报**：曝光去重由服务端做，匿名上报只会吃 401。
 * 匿名用户的收益由下面的「已读原地移除」承担（本地生效、不需要账号）。
 * ——设计 §三 D4 里「匿名曝光落 localStorage 并在登录时采纳」的完整链路
 * 记进了 backlog，本批不做（它需要与 user store 的采纳流程一起改）。
 */
const exposure = useExposure({
  report: (ids) => (user.isLoggedIn ? reportExposures(ids) : undefined),
});

/** 卡片元素 → id：v-for 的函数 ref 只给得到组件实例，曝光观察要的是 DOM 节点 */
const cardEls = new Map<string, Element>();

function observeCard(el: unknown, id: string) {
  const node = (el as { $el?: Element } | null)?.$el;
  if (node instanceof Element) {
    cardEls.set(id, node);
    exposure.observe(node, id);
    return;
  }
  // 元素被 v-for 摘掉：同步停止观察，否则定时器会留在已销毁的节点上
  const prev = cardEls.get(id);
  if (prev) {
    exposure.unobserve(prev);
    cardEls.delete(id);
  }
}

/** 已读（点进过详情的）id；上限兜底，避免长期累积成一个巨大的数组 */
const READ_CAP = 300;
const READ_LS_KEY = 'mingri-toutiao:read';
const readIds = ref<string[]>([]);

onMounted(() => {
  try {
    const raw = JSON.parse(localStorage.getItem(READ_LS_KEY) ?? '[]');
    if (Array.isArray(raw)) readIds.value = raw.map(String).slice(-READ_CAP);
  } catch {
    readIds.value = [];
  }
});

/**
 * 已曝光/已读内容**原地移除**，而不是留一个"已读"样式——读过的还占位是噪声。
 * 只过滤**展示**，不动 store：下一次拉取时服务端（for-you）本就会排除它们，
 * 这里提前生效只是为了让「返回列表」这一下不闪回刚看过的那条。
 */
const visibleList = computed(() => bucket.value.list.filter((n) => !readIds.value.includes(n.id)));

function markRead(id: string) {
  if (readIds.value.includes(id)) return;
  readIds.value = [...readIds.value, id].slice(-READ_CAP);
  try {
    localStorage.setItem(READ_LS_KEY, JSON.stringify(readIds.value));
  } catch {
    /* 隐私模式：写不进去只影响"刷新后是否还记着"，不影响本次浏览 */
  }
}

/** 负反馈/举报的操作反馈：显式动作必须让用户知道有没有生效 */
const hint = ref('');
let hintTimer: ReturnType<typeof setTimeout> | undefined;
function flash(msg: string) {
  hint.value = msg;
  if (hintTimer) clearTimeout(hintTimer);
  hintTimer = setTimeout(() => (hint.value = ''), 2000);
}

const reportOpen = ref(false);
const reportTarget = ref('');

function onFeedback(p: { action: 'news' | 'source' | 'report'; id: string; sourceKey: string }) {
  if (p.action === 'report') {
    reportTarget.value = p.id;
    reportOpen.value = true;
    return;
  }
  if (p.action === 'news') {
    // 先原地移除再发请求：用户点了就该立刻看到效果，网络不该拖住这个反馈
    markRead(p.id);
    void addDislike({ newsId: p.id }).catch(() => flash('操作未生效，请稍后再试'));
    return;
  }
  void addDislike({ sourceKey: p.sourceKey })
    .then(() => flash(`已减少「${p.sourceKey}」的推荐`))
    .catch(() => flash('操作未生效，请稍后再试'));
}

function open(id: string) {
  markRead(id);
  // 进详情前把攒下的曝光送出去：否则返回时服务端还没排除刚看过的这批
  void exposure.flush();
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
      <span v-if="hint" class="home__sort-hint" role="status">{{ hint }}</span>
    </div>

    <BannerCarousel :items="visibleList.slice(0, 3)" @select="open" />

    <div class="home__feed">
      <!-- 首屏骨架 -->
      <template v-if="bucket.loading && !bucket.list.length">
        <div v-for="i in 4" :key="`sk-${i}`" class="home__skeleton" />
      </template>

      <!-- E3：`ref` 回调把 DOM 节点交给曝光观察（组件实例要取 `$el`） -->
      <NewsCard
        v-for="item in visibleList"
        :key="item.id"
        :ref="(el) => observeCard(el, item.id)"
        :item="item"
        @click="open"
        @feedback="onFeedback"
      />

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

    <!-- E3：举报走 E5 的弹窗（负反馈与举报严格分离，两者不共用字段） -->
    <ReportDialog v-model:open="reportOpen" target-type="news" :target-id="reportTarget" />
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

/* 负反馈/举报的操作反馈：与排序说明同一行，不额外占一整行高度 */
.home__sort-hint {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--color-primary);
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
