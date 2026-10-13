<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import Button from '@/components/Button.vue';
import { useUserStore } from '@/stores/user';

const router = useRouter();
const user = useUserStore();

const phone = ref('');
const password = ref('');
const error = ref('');
const failField = ref<'' | 'phone' | 'password'>('');
const submitting = ref(false);

async function submit() {
  error.value = '';
  failField.value = '';
  if (!/^1\d{10}$/.test(phone.value)) {
    error.value = '请输入正确的 11 位手机号';
    failField.value = 'phone';
    return;
  }
  if (password.value.length < 6) {
    error.value = '密码至少 6 位';
    failField.value = 'password';
    return;
  }
  submitting.value = true;
  try {
    await user.login(phone.value, password.value);
    router.push('/');
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败，请稍后重试';
    failField.value = 'password';
  } finally {
    submitting.value = false;
  }
}

function guest() {
  router.push('/');
}
</script>

<template>
  <div class="login">
    <div class="login__brand">
      <span class="login__logo">明</span>
      <span class="login__name">明日头条</span>
    </div>

    <form class="login__form" @submit.prevent="submit">
      <label class="login__field" :class="{ 'is-error': failField === 'phone' }">
        <span class="login__prefix">+86</span>
        <input
          v-model="phone"
          class="login__input"
          type="tel"
          inputmode="numeric"
          maxlength="11"
          placeholder="请输入手机号"
          aria-label="手机号"
        />
      </label>

      <label class="login__field" :class="{ 'is-error': failField === 'password' }">
        <input
          v-model="password"
          class="login__input"
          type="password"
          placeholder="请输入密码"
          aria-label="密码"
        />
      </label>

      <div v-if="error" class="login__error">
        <span class="login__error-icon">!</span>
        <span>{{ error }}</span>
      </div>

      <Button
        variant="primary"
        block
        size="lg"
        label="登录 / 注册"
        type="submit"
        :disabled="submitting"
      />

      <div class="login__divider"><span>其他登录方式已移除</span></div>
    </form>

    <div class="login__footer">
      <button class="login__guest" type="button" @click="guest">不想注册？游客浏览</button>
      <span class="login__version">v1.0.0</span>
    </div>
  </div>
</template>

<style scoped>
.login {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 56px 28px 24px;
  background: var(--color-card-bg);
}

.login__brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 48px;
}

.login__logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 18px;
  background: linear-gradient(135deg, #4e8bff, #1e4fd8);
  color: #fff;
  font-size: 32px;
  font-weight: 700;
}

.login__name {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-primary);
}

.login__form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login__field {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 52px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-page-bg);
  transition: border-color 0.15s ease;
}

.login__field.is-error {
  border-color: var(--color-error);
  background: var(--color-error-bg);
}

.login__prefix {
  font-size: 15px;
  color: var(--color-text-primary);
  font-family: var(--font-num);
  padding-right: 10px;
  border-right: 1px solid var(--color-border);
}

.login__input {
  flex: 1;
  border: none;
  background: transparent;
  outline: none;
  font-size: 15px;
  color: var(--color-text-primary);
  font-family: var(--font-cn);
}

.login__input::placeholder {
  color: var(--color-text-secondary);
}

.login__error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--color-error-bg);
  color: var(--color-error);
  font-size: 13px;
}

.login__error-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-error);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.login__divider {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 4px;
}

.login__divider span {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.login__footer {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.login__guest {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 14px;
  cursor: pointer;
}

.login__version {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-num);
}
</style>
