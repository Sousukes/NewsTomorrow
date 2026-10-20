<script setup lang="ts">
import { computed } from 'vue';
import type { Tab } from '@/types';

const props = withDefaults(
  defineProps<{
    /** 标签项。用 `Tab`（key: string）而非 `ChannelTab`，因为本组件是通用标签栏：
     *  视频页会用「直播/影视/搞笑」等**非频道**子分类。
     *  `ChannelTab[]` 仍可传入（Channel ⊂ string）。 */
    tabs: Tab[];
    /** 当前激活标签 key（v-model:active） */
    active?: string;
    scrollable?: boolean;
  }>(),
  {
    active: '',
    scrollable: true,
  },
);

const emit = defineEmits<{ (e: 'update:active', key: string): void }>();

const activeKey = computed(
  () => props.active || props.tabs.find((t) => t.active)?.key || props.tabs[0]?.key,
);

function select(key: string) {
  emit('update:active', key);
}
</script>

<template>
  <nav class="mt-channels" :class="{ 'mt-channels--scroll': scrollable }">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      class="mt-channels__item"
      :class="{ 'is-active': tab.key === activeKey }"
      :aria-current="tab.key === activeKey ? 'true' : undefined"
      @click="select(tab.key)"
    >
      {{ tab.label }}
    </button>
  </nav>
</template>

<style scoped>
.mt-channels {
  display: flex;
  align-items: center;
  gap: 18px;
  height: 44px;
  padding: 0 16px;
  background: var(--color-card-bg);
  overflow-x: auto;
  scrollbar-width: none;
}

.mt-channels--scroll {
  flex-wrap: nowrap;
}

.mt-channels::-webkit-scrollbar {
  display: none;
}

.mt-channels__item {
  flex-shrink: 0;
  border: none;
  background: transparent;
  padding: 0;
  font-family: var(--font-cn);
  font-size: 15px;
  color: var(--color-text-secondary);
  position: relative;
  height: 44px;
  line-height: 44px;
}

.mt-channels__item.is-active {
  color: var(--color-primary);
  font-weight: 500;
  font-size: 16px;
}

.mt-channels__item.is-active::after {
  content: '';
  position: absolute;
  left: 50%;
  bottom: 8px;
  transform: translateX(-50%);
  width: 18px;
  height: 3px;
  border-radius: 2px;
  background: var(--color-primary);
}
</style>
