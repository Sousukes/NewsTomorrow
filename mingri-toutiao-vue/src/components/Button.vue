<script setup lang="ts">
withDefaults(
  defineProps<{
    /** 主按钮 / 次按钮 / 文字按钮 */
    variant?: 'primary' | 'secondary' | 'text';
    disabled?: boolean;
    /** 占满父容器宽度 */
    block?: boolean;
    /** md=高度44 / lg=高度48 */
    size?: 'md' | 'lg';
    label?: string;
  }>(),
  {
    variant: 'primary',
    disabled: false,
    block: false,
    size: 'md',
    label: '',
  },
);

const emit = defineEmits<{ (e: 'click', ev: MouseEvent): void }>();

function onClick(ev: MouseEvent) {
  if ((ev.currentTarget as HTMLButtonElement).disabled) return;
  emit('click', ev);
}
</script>

<template>
  <button
    class="mt-btn"
    :class="[
      `mt-btn--${variant}`,
      `mt-btn--${size}`,
      { 'mt-btn--block': block, 'is-disabled': disabled },
    ]"
    :disabled="disabled"
    @click="onClick"
  >
    <slot>{{ label }}</slot>
  </button>
</template>

<style scoped>
.mt-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-button);
  font-family: var(--font-cn);
  font-weight: 500;
  white-space: nowrap;
  transition:
    opacity 0.15s ease,
    background 0.15s ease;
}

.mt-btn--md {
  height: 44px;
  padding: 0 24px;
  font-size: 15px;
}

.mt-btn--lg {
  height: 48px;
  padding: 0 28px;
  font-size: 16px;
}

.mt-btn--block {
  display: flex;
  width: 100%;
}

/* 主按钮：蓝胶囊 */
.mt-btn--primary {
  background: var(--color-primary);
  color: #ffffff;
}

.mt-btn--primary:not(.is-disabled):hover {
  opacity: 0.92;
}

/* 次按钮：白底蓝描边 */
.mt-btn--secondary {
  background: var(--color-card-bg);
  color: var(--color-primary);
  border: 1px solid var(--color-primary);
}

.mt-btn--secondary:not(.is-disabled):hover {
  background: rgba(45, 108, 255, 0.06);
}

/* 文字按钮：蓝链接 */
.mt-btn--text {
  background: transparent;
  color: var(--color-primary);
  padding: 0 8px;
}

.mt-btn--text:not(.is-disabled):hover {
  opacity: 0.8;
}

/* 禁用态：灰底 */
.mt-btn.is-disabled {
  background: var(--color-border);
  color: var(--color-text-secondary);
  border: none;
  cursor: not-allowed;
}
</style>
