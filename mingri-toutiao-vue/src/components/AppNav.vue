<script setup lang="ts">
import type { Author } from '@/types';

withDefaults(
  defineProps<{
    variant?: 'title' | 'brand' | 'search' | 'detail';
    title?: string;
    showBack?: boolean;
    showSearch?: boolean;
    showMessage?: boolean;
    showMore?: boolean;
    author?: Author;
  }>(),
  {
    variant: 'title',
    title: '',
    showBack: false,
    showSearch: false,
    showMessage: false,
    showMore: false,
    author: undefined,
  },
);

const emit = defineEmits<{
  (e: 'back'): void;
  (e: 'search'): void;
  (e: 'message'): void;
  (e: 'more'): void;
  (e: 'follow'): void;
}>();

function fallthroughAttr(ev: string) {
  if (ev === 'search') emit('search');
  if (ev === 'message') emit('message');
  if (ev === 'more') emit('more');
}
</script>

<template>
  <header class="mt-nav">
    <!-- 返回 -->
    <button v-if="showBack" class="mt-nav__icon" aria-label="返回" @click="emit('back')">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
        <path
          d="M15 5l-7 7 7 7"
          stroke="#222"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>

    <!-- 品牌变体 -->
    <template v-if="variant === 'brand'">
      <div class="mt-nav__brand">
        <span class="mt-nav__logo">明</span>
        <span class="mt-nav__brand-name">{{ title || '明日头条' }}</span>
      </div>
    </template>

    <!-- 标题变体 -->
    <template v-else-if="variant === 'title'">
      <span class="mt-nav__title">{{ title }}</span>
    </template>

    <!-- 详情-作者关注 变体 -->
    <template v-else-if="variant === 'detail' && author">
      <div class="mt-nav__author">
        <span class="mt-nav__avatar" />
        <span class="mt-nav__author-info">
          <span class="mt-nav__author-name">{{ author.name }}</span>
          <span v-if="author.time" class="mt-nav__author-time">{{ author.time }}</span>
        </span>
      </div>
      <button
        class="mt-nav__follow"
        :class="{ 'is-followed': author.followed }"
        @click="emit('follow')"
      >
        {{ author.followed ? '已关注' : '关注' }}
      </button>
    </template>

    <!-- 搜索变体 -->
    <template v-else-if="variant === 'search'">
      <div class="mt-nav__search">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <circle cx="11" cy="11" r="7" stroke="#9a9fa8" stroke-width="2" />
          <path d="M20 20l-3.2-3.2" stroke="#9a9fa8" stroke-width="2" stroke-linecap="round" />
        </svg>
        <span>搜索你感兴趣的内容</span>
      </div>
    </template>

    <span class="mt-nav__spacer" />

    <div class="mt-nav__actions">
      <button
        v-if="showSearch"
        class="mt-nav__icon"
        aria-label="搜索"
        @click="fallthroughAttr('search')"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="11" cy="11" r="7" stroke="#222" stroke-width="2" />
          <path d="M20 20l-3.2-3.2" stroke="#222" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
      <button
        v-if="showMessage"
        class="mt-nav__icon"
        aria-label="消息"
        @click="fallthroughAttr('message')"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path
            d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z"
            stroke="#222"
            stroke-width="1.8"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <button
        v-if="showMore"
        class="mt-nav__icon"
        aria-label="更多"
        @click="fallthroughAttr('more')"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="5" cy="12" r="1.6" fill="#222" />
          <circle cx="12" cy="12" r="1.6" fill="#222" />
          <circle cx="19" cy="12" r="1.6" fill="#222" />
        </svg>
      </button>
    </div>
  </header>
</template>

<style scoped>
.mt-nav {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 16px;
  background: var(--color-card-bg);
  font-family: var(--font-cn);
  flex-shrink: 0;
}

.mt-nav__spacer {
  flex: 1;
}

.mt-nav__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  padding: 0;
}

.mt-nav__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.mt-nav__brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mt-nav__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: linear-gradient(135deg, #4e8bff, #1e4fd8);
  color: #fff;
  font-size: 16px;
  font-weight: 700;
}

.mt-nav__brand-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.mt-nav__title {
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.mt-nav__author {
  display: flex;
  align-items: center;
  gap: 10px;
}

.mt-nav__avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-avatar);
  background: linear-gradient(135deg, #2d6cff, #6a9bff);
}

.mt-nav__author-info {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.mt-nav__author-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.mt-nav__author-time {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.mt-nav__follow {
  margin-left: 8px;
  height: 28px;
  padding: 0 14px;
  border-radius: 14px;
  border: 1px solid var(--color-primary);
  background: var(--color-card-bg);
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 500;
}

.mt-nav__follow.is-followed {
  border-color: var(--color-border);
  color: var(--color-text-secondary);
}

.mt-nav__search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  height: 36px;
  padding: 0 14px;
  border-radius: 18px;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  font-size: 13px;
}
</style>
