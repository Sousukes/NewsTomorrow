<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 空态标题 */
    title: string;
    /** 辅助说明 */
    description?: string;
    /** 内置线性插图类型，对应 S11 消息 / S12 收藏 / S13 历史 / S14 网络 / 搜索 / 通用 */
    icon?: 'favorite' | 'history' | 'message' | 'search' | 'network' | 'collect';
  }>(),
  { description: '', icon: 'collect' },
);

const paths: Record<string, string> = {
  favorite:
    'M12 21s-7-4.4-9.5-8.5C1 9.5 2.5 6 6 6c2 0 3 1 4 2 1-1 2-2 4-2 3.5 0 5 3.5 3.5 6.5C19 16.6 12 21 12 21z',
  history: 'M3 12a9 9 0 1 0 3-6.7M3 4v4h4M12 8v4l3 2',
  message: 'M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z',
  search: 'M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M20 20l-3.2-3.2',
  network:
    'M2 8.5a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M8.5 15.5a6 6 0 0 1 7 0M12 19h.01M2 2l20 20',
  collect: 'M12 3l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.1l1-5.8L3.5 9.2l5.9-.9z',
};
</script>

<template>
  <div class="mt-empty">
    <div class="mt-empty__art">
      <svg width="56" height="56" viewBox="0 0 24 24" fill="none">
        <path
          :d="paths[icon]"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </div>
    <p class="mt-empty__title">{{ title }}</p>
    <p v-if="description" class="mt-empty__desc">{{ description }}</p>
    <slot name="action" />
  </div>
</template>

<style scoped>
.mt-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 32px;
  text-align: center;
  min-height: 60%;
}

.mt-empty__art {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 96px;
  height: 96px;
  border-radius: 50%;
  background: var(--color-page-bg);
  color: var(--color-text-secondary);
  margin-bottom: 16px;
}

.mt-empty__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.mt-empty__desc {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  max-width: 240px;
}

.mt-empty :slotted(*) {
  margin-top: 20px;
}
</style>
