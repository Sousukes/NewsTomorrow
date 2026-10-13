import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import router from './router';
import { UNAUTHORIZED_EVENT } from './api/http';
import { useTheme } from './composables/useTheme';
import './styles/tokens.css';
import './styles/global.css';

const app = createApp(App);

// 全局兜底：未被 ErrorBoundary 捕获的异常（如 store / 生命周期外）统一记录，避免静默失败
app.config.errorHandler = (err, _instance, info) => {
  console.error('[app error]', info, err);
};

app.use(createPinia());

// 主题必须在挂载**之前**应用，否则深色偏好会先渲染一帧浅色再跳变（闪白）
useTheme();

app.use(router).mount('#app');

// 401（登录态失效）→ 跳转登录页（登录态清理由 user store 监听同一事件完成）
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  if (router.currentRoute.value.name !== 'login') {
    void router.push({ name: 'login' });
  }
});
