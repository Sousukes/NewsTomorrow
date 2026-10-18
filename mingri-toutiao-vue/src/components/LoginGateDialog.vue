<script setup lang="ts">
import { useRouter } from 'vue-router';
import Button from '@/components/Button.vue';

/**
 * 登录门禁对话框（E6b）。
 *
 * 用于「需要登录才有意义」的轻量操作（关注来源等）：未登录时不直接跳登录页，
 * 先说明"为什么要登录"，让用户自己决定。**别把失败留到最后一步**——用户操作
 * 到一半才被 401 打回，是最浪费操作路径的设计（举报弹窗同此取舍）。
 *
 * 与 `ReportDialog` 的差别：这里不承载任何业务表单，只做「解释 + 跳登录」。
 */
withDefaults(
  defineProps<{
    open: boolean;
    /** 标题：如「关注来源」 */
    title?: string;
    /** 一句解释：登录后这个操作意味着什么 */
    message?: string;
  }>(),
  { title: '需要登录', message: '该操作需要登录后使用，登录即可继续。' },
);

const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>();

const router = useRouter();

function goLogin() {
  emit('update:open', false);
  void router.push('/login');
}
</script>

<template>
  <div v-if="open" class="mask" @click.self="emit('update:open', false)">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="gate-title">
      <h3 id="gate-title" class="dialog__title">{{ title }}</h3>
      <p class="dialog__desc">{{ message }}</p>
      <div class="dialog__actions">
        <Button variant="secondary" label="取消" @click="emit('update:open', false)" />
        <Button variant="primary" label="去登录" @click="goLogin" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: absolute;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: rgb(0 0 0 / 45%);
}

.dialog {
  width: 100%;
  padding: 20px 16px;
  border-radius: 16px 16px 0 0;
  background: var(--color-card-bg);
  box-shadow: 0 -8px 24px rgb(0 0 0 / 18%);
  font-family: var(--font-cn);
}

.dialog__title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.dialog__desc {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.dialog__actions {
  display: flex;
  gap: 12px;
}

.dialog__actions :deep(.mt-btn) {
  flex: 1;
}
</style>
