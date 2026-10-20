<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useUserStore } from '@/stores/user';

/**
 * 修改密码（C4）。
 *
 * 两条入口，语义不同但共用一个页面：
 * - **被迫**：管理员代重置后的临时密码（`mustChangePassword`）→ 路由守卫只放它过来；
 * - **主动**：「我的」页里的入口，随时可改。
 *
 * 提交会触发二次校验（服务端 40304 → 全局密码对话框），所以本页**不**自己
 * 要求"再输一次当前密码"——服务端已经校验了 `oldPassword`，界面重复一遍
 * 只会让人多填一次。
 */
const user = useUserStore();
const router = useRouter();

const oldPassword = ref('');
const newPassword = ref('');
const confirmPassword = ref('');
const error = ref('');
const submitting = ref(false);

const forced = computed(() => user.mustChangePassword);

function validate(): string {
  if (!oldPassword.value) return '请输入当前密码';
  if (newPassword.value.length < 6) return '新密码至少 6 位';
  if (newPassword.value !== confirmPassword.value) return '两次输入的新密码不一致';
  if (newPassword.value === oldPassword.value) return '新密码不能与当前密码相同';
  return '';
}

async function onSubmit() {
  const msg = validate();
  if (msg) {
    error.value = msg;
    return;
  }
  submitting.value = true;
  error.value = '';
  try {
    await user.changePassword(oldPassword.value, newPassword.value);
    // 受限期进来的改完必须回主页，否则守卫会把它又弹回这里
    await router.push(forced.value ? '/' : '/profile');
  } catch (e) {
    error.value = e instanceof Error ? e.message : '修改失败，请重试';
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="page">
    <h2 class="page__title">{{ forced ? '请先修改初始密码' : '修改密码' }}</h2>
    <p v-if="forced" class="page__tip">
      管理员已为你重置密码。为保障账号安全，修改完成前无法发评论、收藏或进入管理后台。
    </p>

    <form class="form" @submit.prevent="onSubmit">
      <label class="field">
        <span class="field__label">当前密码</span>
        <input
          v-model="oldPassword"
          class="field__input"
          type="password"
          autocomplete="current-password"
        />
      </label>
      <label class="field">
        <span class="field__label">新密码</span>
        <input
          v-model="newPassword"
          class="field__input"
          type="password"
          autocomplete="new-password"
        />
      </label>
      <label class="field">
        <span class="field__label">确认新密码</span>
        <input
          v-model="confirmPassword"
          class="field__input"
          type="password"
          autocomplete="new-password"
        />
      </label>

      <p v-if="error" class="form__error">{{ error }}</p>

      <button class="form__submit" type="submit" :disabled="submitting">
        {{ submitting ? '提交中…' : '确认修改' }}
      </button>
    </form>
  </section>
</template>

<style scoped>
.page {
  padding: 16px;
}

.page__title {
  margin: 0 0 6px;
  font-size: 18px;
  color: var(--color-text-primary);
}

.page__tip {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field__label {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.field__input {
  padding: 10px 12px;
  font-size: 15px;
  color: var(--color-text-primary);
  background: var(--color-card-bg, #fff);
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
}

.field__input:focus-visible {
  outline: 2px solid var(--color-primary, #d43d3d);
  outline-offset: 1px;
}

.form__error {
  margin: 0;
  font-size: 13px;
  color: var(--color-primary, #d43d3d);
}

.form__submit {
  padding: 11px 16px;
  font-size: 15px;
  color: #fff;
  background: var(--color-primary, #d43d3d);
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.form__submit:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
