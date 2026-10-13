<script setup lang="ts">
import type { TabItem } from '@/types';

const props = withDefaults(
  defineProps<{
    items: TabItem[];
    /** 当前激活 Tab key（v-model:active） */
    active?: string;
    fixed?: boolean;
    /** 各 Tab 的未读数徽标，如 { message: 3 }；0/未传不显示 */
    badges?: Record<string, number>;
  }>(),
  {
    active: 'home',
    fixed: true,
    badges: () => ({}),
  },
);

const emit = defineEmits<{ (e: 'update:active', key: string): void }>();

function iconPath(icon: TabItem['icon']): string {
  switch (icon) {
    case 'home':
      return 'M3 11l9-8 9 8M5 10v10h14V10';
    case 'video':
      return 'M4 5h16v14H4zM10 9l5 3-5 3z';
    case 'message':
      return 'M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z';
    case 'mine':
      return 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0';
    default:
      return '';
  }
}

/** 徽标文案：超过 99 显示 99+ */
function badgeText(key: string): string {
  const n = props.badges?.[key] ?? 0;
  if (n <= 0) return '';
  return n > 99 ? '99+' : String(n);
}
</script>

<template>
  <nav class="mt-tabbar" :class="{ 'mt-tabbar--fixed': fixed }">
    <button
      v-for="item in items"
      :key="item.key"
      class="mt-tabbar__item"
      :class="{ 'is-active': item.key === active }"
      @click="emit('update:active', item.key)"
    >
      <span class="mt-tabbar__icon">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <path
            :d="iconPath(item.icon)"
            :stroke="item.key === active ? '#2d6cff' : '#9a9fa8'"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span v-if="badgeText(item.key)" class="mt-tabbar__badge">{{ badgeText(item.key) }}</span>
      </span>
      <span class="mt-tabbar__label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.mt-tabbar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  height: 62px;
  padding: 8px 0 12px;
  background: var(--color-card-bg);
  border-top: 1px solid var(--color-divider);
  flex-shrink: 0;
}

.mt-tabbar--fixed {
  position: sticky;
  bottom: 0;
}

.mt-tabbar__item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  border: none;
  background: transparent;
  padding: 0 12px;
  color: var(--color-text-secondary);
}

.mt-tabbar__item.is-active {
  color: var(--color-primary);
}

.mt-tabbar__icon {
  position: relative;
  display: inline-flex;
}

.mt-tabbar__badge {
  position: absolute;
  top: -4px;
  left: 12px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--color-error);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  font-family: var(--font-num);
}

.mt-tabbar__label {
  font-size: 11px;
  font-family: var(--font-cn);
}
</style>
