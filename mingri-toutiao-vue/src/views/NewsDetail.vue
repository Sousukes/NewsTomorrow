<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import NewsCard from '@/components/NewsCard.vue';
import ReportDialog from '@/components/ReportDialog.vue';
import LoginGateDialog from '@/components/LoginGateDialog.vue';
import { useUserStore } from '@/stores/user';
import { useCommentsStore } from '@/stores/comments';
import { useFollowsStore } from '@/stores/follows';
import { useNewsDetail } from '@/composables/useNewsDetail';
import { bgFor } from '@/utils/image';
import { fetchRelated } from '@/api/news';
import type { Author, NewsItem } from '@/types';

const route = useRoute();
const router = useRouter();
const user = useUserStore();
const comments = useCommentsStore();
const follows = useFollowsStore();

const { item, resolving, failed } = useNewsDetail(() => route.params.id as string);
const commentCount = computed(() => (item.value ? comments.countOf(item.value.id) : 0));
const thumbCount = computed(() => Math.min(item.value?.images?.length ?? 0, 3));
const author = computed<Author | undefined>(() => {
  if (!item.value) return undefined;
  // 没有 sourceKey（历史数据 / 旧后端）时**不渲染作者区**：关注按钮依赖 key，
  // 缺 key 就把按钮显示出来又不可点，等于制造一个死按钮（AppNav 的「更多」刚被这么修过）
  if (!item.value.sourceKey) return undefined;
  return {
    name: item.value.source,
    time: item.value.time,
    followed: user.isLoggedIn && follows.isFollowing(item.value.sourceKey),
  };
});

/** 关注入口（E6b）：未登录先弹门禁，别让用户被 401 打回 */
const loginGateOpen = ref(false);
const followHint = ref('');
let hintTimer: ReturnType<typeof setTimeout> | undefined;

function showHint(msg: string) {
  followHint.value = msg;
  window.clearTimeout(hintTimer);
  hintTimer = window.setTimeout(() => {
    followHint.value = '';
  }, 2400);
}

onBeforeUnmount(() => window.clearTimeout(hintTimer));

async function toggleFollow() {
  const key = item.value?.sourceKey;
  if (!key) return;
  followHint.value = '';
  if (!user.isLoggedIn) {
    loginGateOpen.value = true;
    return;
  }
  const ok = follows.isFollowing(key) ? await follows.unfollow(key) : await follows.follow(key);
  if (!ok) showHint(follows.error || '操作失败，请稍后重试');
}

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

onMounted(() => {
  // 目录不随详情每次重载：已加载过就不再打接口（目录是公开静态数据）
  if (!follows.loaded) void follows.loadDirectory();
});

/**
 * 举报入口（E5）：挂在导航「更多」上。
 *
 * 为什么不放进底部操作区：举报是低频负向操作，与「收藏/评论」并排会抬高误触率，
 * 也让正向操作被稀释。新闻类 App 的惯例也是「更多 → 举报」。
 */
const reportOpen = ref(false);
</script>

<template>
  <div v-if="item" class="detail">
    <AppNav
      variant="detail"
      :author="author"
      show-back
      show-more
      @back="router.back()"
      @follow="toggleFollow"
      @more="reportOpen = true"
    />
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

    <!-- 举报弹窗（E5）：目标固定为当前资讯；登录态与提交由弹窗内部处理 -->
    <ReportDialog v-model:open="reportOpen" target-type="news" :target-id="item.id" />

    <!-- 关注失败/未登录提示（E6b）：失败必须让用户看见，别默默吞掉 -->
    <p v-if="followHint" class="detail__hint" role="status">{{ followHint }}</p>
    <LoginGateDialog
      v-model:open="loginGateOpen"
      title="关注来源"
      message="登录后即可关注该来源，关注内容会出现在首页「关注」标签里。"
    />
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

  <!-- 网络失败与「真的不存在」要分开说：把临时故障说成"已下架"会误导用户 -->
  <EmptyState
    v-else
    icon="network"
    :title="failed ? '加载失败，请稍后重试' : '内容不存在或已下架'"
    :description="
      failed ? '网络或服务暂时不可用，检查网络后重试' : '该资讯可能已被删除，换条新闻看看吧'
    "
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

/* E6b：关注操作的即时反馈（失败原因 / 未登录提示） */
.detail__hint {
  padding: 8px 16px 0;
  font-size: 12px;
  color: var(--color-text-secondary);
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
