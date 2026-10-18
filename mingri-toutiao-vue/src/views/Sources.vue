<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import Button from '@/components/Button.vue';
import EmptyState from '@/components/EmptyState.vue';
import { useFollowsStore } from '@/stores/follows';
import { useUserStore } from '@/stores/user';
import { CHANNELS } from '@/constants/channels';
import { channelLabel } from '@/constants/channels';
import type { SourceItem } from '@/api/follows';

/**
 * 来源目录（E6b）：可关注哪些来源、当前已关注哪些。
 *
 * 内容用 `newsCount` 帮用户判断值不值得关注（"它有内容"比"它叫什么"更有信息量）；
 * 排序按「已关注优先 → 内容多优先」，让已关注的排在最上——管理关注比发现更常用。
 */
const router = useRouter();
const follows = useFollowsStore();
const user = useUserStore();

const activeChannel = ref('');

const list = computed<SourceItem[]>(() => {
  const rows = follows.sources.filter(
    (s) => !activeChannel.value || s.channel === activeChannel.value,
  );
  return [...rows].sort((a, b) => {
    const fa = follows.isFollowing(a.key) ? 1 : 0;
    const fb = follows.isFollowing(b.key) ? 1 : 0;
    if (fa !== fb) return fb - fa;
    return b.newsCount - a.newsCount;
  });
});

const followError = ref('');

async function toggle(source: SourceItem) {
  followError.value = '';
  if (!user.isLoggedIn) {
    void router.push('/login');
    return;
  }
  const ok = follows.isFollowing(source.key)
    ? await follows.unfollow(source.key)
    : await follows.follow(source.key);
  if (!ok) followError.value = follows.error || '操作失败，请稍后重试';
}

onMounted(() => {
  if (!follows.loaded) void follows.loadDirectory();
});
</script>

<template>
  <div class="sources">
    <AppNav variant="title" title="全部来源" show-back @back="router.back()" />

    <div class="sources__summary">
      <span class="sources__count">已关注 {{ follows.keys.length }} 个来源</span>
      <span v-if="follows.loading" class="sources__state">加载中…</span>
      <span v-else-if="follows.error" class="sources__state sources__state--error">
        {{ follows.error }}
      </span>
    </div>

    <!-- 频道筛选：目录几十条时纯滚动也能用，但有筛选更接近真实产品形态 -->
    <div class="sources__chips">
      <button
        class="sources__chip"
        :class="{ 'is-active': activeChannel === '' }"
        @click="activeChannel = ''"
      >
        全部
      </button>
      <button
        v-for="c in CHANNELS"
        :key="c.key"
        class="sources__chip"
        :class="{ 'is-active': activeChannel === c.key }"
        @click="activeChannel = c.key"
      >
        {{ channelLabel(c.key) }}
      </button>
    </div>

    <p v-if="followError" class="sources__error" role="status">{{ followError }}</p>

    <template v-if="list.length">
      <div v-for="s in list" :key="s.key" class="sources__row">
        <div class="sources__meta">
          <span class="sources__name">{{ s.name }}</span>
          <span class="sources__desc">
            {{ channelLabel(s.channel) }} · {{ s.newsCount }} 条内容
          </span>
        </div>
        <Button
          :variant="follows.isFollowing(s.key) ? 'secondary' : 'primary'"
          :label="follows.isFollowing(s.key) ? '已关注' : '关注'"
          @click="toggle(s)"
        />
      </div>
    </template>

    <EmptyState
      v-else-if="!follows.loading"
      icon="search"
      title="该频道下暂无来源"
      description="换个频道看看，或联系运营添加更多来源"
    />
  </div>
</template>

<style scoped>
.sources {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.sources__summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.sources__count {
  color: var(--color-text-primary);
  font-weight: 600;
}

.sources__state--error {
  color: var(--color-error);
}

.sources__chips {
  display: flex;
  gap: 8px;
  padding: 10px 12px 4px;
  overflow-x: auto;
}

.sources__chip {
  flex-shrink: 0;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border: 1px solid var(--color-border);
  border-radius: 14px;
  cursor: pointer;
}

.sources__chip.is-active {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.sources__error {
  padding: 6px 16px;
  font-size: 12px;
  color: var(--color-error);
}

.sources__row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--color-divider);
}

.sources__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.sources__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.sources__desc {
  font-size: 12px;
  color: var(--color-text-secondary);
}
</style>
