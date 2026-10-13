<script setup lang="ts">
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import AppNav from '@/components/AppNav.vue';
import EmptyState from '@/components/EmptyState.vue';
import Button from '@/components/Button.vue';
import { useMessagesStore } from '@/stores/messages';
import type { Message } from '@/types';

const router = useRouter();
const messages = useMessagesStore();

onMounted(() => {
  void messages.fetch();
});

async function open(m: Message) {
  await messages.markRead(m.id);
  if (m.newsId) router.push(`/news/${m.newsId}`);
}

/** 消息类型 → 徽标文案与配色（对应后端 type 字段） */
const TYPE_META: Record<Message['type'], { label: string; bg: string }> = {
  system: { label: '系统', bg: 'linear-gradient(135deg,#2d6cff,#6a9bff)' },
  comment: { label: '评论', bg: 'linear-gradient(135deg,#ff8a3d,#ffb066)' },
  recommend: { label: '推荐', bg: 'linear-gradient(135deg,#22b07d,#5ad3a3)' },
};
</script>

<template>
  <div class="messages">
    <AppNav variant="title" title="消息" show-back @back="router.back()" />

    <div v-if="messages.unreadCount" class="messages__bar">
      <span>{{ messages.unreadCount }} 条未读</span>
      <button class="messages__read-all" @click="messages.markAllRead()">全部已读</button>
    </div>

    <div v-if="messages.error" class="messages__tip">{{ messages.error }}</div>

    <div v-if="messages.loading && !messages.list.length" class="messages__list">
      <div v-for="i in 4" :key="i" class="msg msg--skeleton" />
    </div>

    <div v-else-if="messages.list.length" class="messages__list">
      <button
        v-for="m in messages.list"
        :key="m.id"
        class="msg"
        :class="{ 'is-read': m.read }"
        @click="open(m)"
      >
        <span class="msg__avatar" :style="{ background: TYPE_META[m.type].bg }">
          {{ TYPE_META[m.type].label }}
        </span>
        <span class="msg__body">
          <span class="msg__row">
            <span class="msg__title">{{ m.title }}</span>
            <span class="msg__time">{{ m.time }}</span>
          </span>
          <span class="msg__preview">{{ m.summary }}</span>
        </span>
        <span v-if="!m.read" class="msg__dot" />
      </button>
    </div>

    <EmptyState
      v-else
      icon="message"
      title="暂无新消息"
      description="你收到的评论、回复与系统通知都会显示在这里"
    >
      <Button variant="secondary" label="去逛逛" @click="router.push('/')" />
    </EmptyState>
  </div>
</template>

<style scoped>
.messages {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.messages__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
  background: var(--color-card-bg);
  border-bottom: 1px solid var(--color-divider);
}

.messages__read-all {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
}

.messages__tip {
  padding: 8px 16px;
  font-size: 12px;
  color: var(--color-error);
  background: var(--color-error-bg);
  border-bottom: 1px solid var(--color-divider);
}

.messages__list {
  display: flex;
  flex-direction: column;
  background: var(--color-card-bg);
}

.msg {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 16px;
  border: none;
  background: transparent;
  text-align: left;
  border-bottom: 1px solid var(--color-divider);
  cursor: pointer;
}

.msg--skeleton {
  height: 72px;
  cursor: default;
}

.msg.is-read {
  opacity: 0.6;
}

.msg__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: var(--radius-avatar);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
}

.msg__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.msg__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.msg__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg__time {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}

.msg__preview {
  font-size: 13px;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msg__dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-error);
}
</style>
