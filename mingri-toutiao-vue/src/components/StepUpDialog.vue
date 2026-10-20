<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { useDialog } from '@/composables/useDialog';
import { useStepUpStore } from '@/stores/stepUp';
import { STEP_UP_REQUIRED_EVENT } from '@/api/http';

/**
 * 二次校验对话框（C10）。
 *
 * 由 `http.ts` 的 40304 拦截触发（广播 `STEP_UP_REQUIRED_EVENT`），
 * 也可以在界面里主动调 `open()` 预验证（避免用户操作到一半才被拦）。
 * 挂在 App 根部，与具体页面解耦。
 */
const store = useStepUpStore();

/** E9：Escape 取消 + 焦点陷阱 + 关闭后焦点还原（见 useDialog） */
const panel = ref<HTMLElement | null>(null);
useDialog(
  () => store.visible,
  () => store.cancel(),
  panel,
);

function onRequired() {
  // 已在显示中则不重复弹（并发高敏请求只验证一次）
  if (!store.visible) store.open();
}

onMounted(() => window.addEventListener(STEP_UP_REQUIRED_EVENT, onRequired));
onUnmounted(() => window.removeEventListener(STEP_UP_REQUIRED_EVENT, onRequired));

function onSubmit() {
  if (store.password.trim()) void store.submit();
}
</script>

<template>
  <div v-if="store.visible" class="mask" @click.self="store.cancel()">
    <div
      ref="panel"
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-label="二次校验"
      tabindex="-1"
    >
      <h3 class="dialog__title">身份验证</h3>
      <p class="dialog__desc">该操作需要再次验证身份，请输入登录密码</p>
      <input
        v-model="store.password"
        class="dialog__input"
        type="password"
        placeholder="登录密码"
        autocomplete="current-password"
        :disabled="store.submitting"
        @keyup.enter="onSubmit"
      />
      <p v-if="store.error" class="dialog__error">{{ store.error }}</p>
      <div class="dialog__actions">
        <button class="btn btn--ghost" :disabled="store.submitting" @click="store.cancel()">
          取消
        </button>
        <button class="btn btn--primary" :disabled="store.submitting" @click="onSubmit">
          {{ store.submitting ? '验证中…' : '验证' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: absolute;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(0 0 0 / 45%);
}

.dialog {
  width: 82%;
  max-width: 320px;
  padding: 20px;
  border-radius: var(--radius-card, 12px);
  background: var(--color-card-bg, #fff);
  box-shadow: 0 12px 32px rgb(0 0 0 / 18%);
}

.dialog__title {
  margin: 0 0 6px;
  font-size: 17px;
  color: var(--color-text-primary);
}

.dialog__desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.dialog__input {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  font-size: 14px;
  color: var(--color-text-primary);
  background: var(--color-page-bg);
  border: 1px solid var(--color-border, rgb(0 0 0 / 8%));
  border-radius: 8px;
}

.dialog__input:focus-visible {
  outline: 2px solid var(--color-primary, #d43d3d);
  outline-offset: 1px;
}

.dialog__error {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--color-primary, #d43d3d);
}

.dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.btn {
  padding: 8px 16px;
  font-size: 14px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.btn--ghost {
  color: var(--color-text-secondary);
  background: transparent;
}

.btn--primary {
  color: #fff;
  background: var(--color-primary, #d43d3d);
}
</style>
