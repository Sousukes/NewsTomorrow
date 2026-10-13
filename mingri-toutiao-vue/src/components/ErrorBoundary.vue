<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue';
import Button from '@/components/Button.vue';

/**
 * 全局错误边界：捕获子树渲染/生命周期异常，避免整页白屏。
 * 用法：包裹路由出口 <ErrorBoundary><router-view /></ErrorBoundary>
 * 提供「重试」（重挂载子树）与「返回首页」两个出口。
 */
const props = withDefaults(defineProps<{ title?: string }>(), { title: '页面出了点问题' });

const error = ref<Error | null>(null);
const key = ref(0);

onErrorCaptured((err) => {
  error.value = err instanceof Error ? err : new Error(String(err));
  // 返回 false 阻止继续向上抛出，避免触发全局 unhandledrejection
  return false;
});

function retry() {
  error.value = null;
  key.value += 1;
}

function goHome() {
  window.location.assign('/');
}
</script>

<template>
  <slot v-if="!error" :key="key" />
  <div v-else class="mt-boundary">
    <div class="mt-boundary__art">!</div>
    <p class="mt-boundary__title">{{ props.title }}</p>
    <p class="mt-boundary__desc">{{ error.message || '请稍后重试，或返回首页继续浏览' }}</p>
    <div class="mt-boundary__actions">
      <Button variant="secondary" label="重试" @click="retry" />
      <Button variant="primary" label="返回首页" @click="goHome" />
    </div>
  </div>
</template>

<style scoped>
.mt-boundary {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 100%;
  padding: 64px 32px;
  text-align: center;
}

.mt-boundary__art {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin-bottom: 8px;
  border-radius: 50%;
  background: var(--color-error-bg);
  color: var(--color-error);
  font-size: 28px;
  font-weight: 700;
}

.mt-boundary__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.mt-boundary__desc {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  word-break: break-all;
}

.mt-boundary__actions {
  display: flex;
  gap: 12px;
}
</style>
