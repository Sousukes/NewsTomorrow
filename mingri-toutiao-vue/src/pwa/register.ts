/**
 * Service Worker 注册与更新提示。
 * 三条守卫：仅生产环境、需支持 serviceWorker、需安全上下文。
 * 更新走提示刷新，不自动 skipWaiting（会打断阅读）。
 */

export interface PwaUpdateHandle {
  /** 是否有新版本待激活（供 UI 显示"发现新版本 · 点击刷新"） */
  readonly available: () => boolean;
  /** 用户点击"刷新" → 令新 SW 接管并重载 */
  refresh: () => void;
}

/** 无 SW 环境（jsdom / dev / 旧浏览器） */
const NOOP_UPDATE: PwaUpdateHandle = {
  available: () => false,
  refresh: () => {},
};

export function registerServiceWorker(): PwaUpdateHandle {
  if (!import.meta.env.PROD) return NOOP_UPDATE;
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return NOOP_UPDATE;
  if (typeof window !== 'undefined' && !window.isSecureContext) return NOOP_UPDATE;

  let updateReady = false;
  let waiting: ServiceWorker | null = null;
  let reloading = false;

  // 已有 SW 在控制页面时等待它；首次安装走 updatefound
  navigator.serviceWorker
    .register('/sw.js', { scope: '/' })
    .then((registration) => {
      if (registration.waiting && navigator.serviceWorker.controller) {
        waiting = registration.waiting;
        updateReady = true;
      }
      registration.addEventListener('updatefound', () => {
        const installing = registration.installing;
        if (!installing) return;
        installing.addEventListener('statechange', () => {
          // installed 且已有 controller = 新版本，不是首次安装
          if (installing.state === 'installed' && navigator.serviceWorker.controller) {
            waiting = installing;
            updateReady = true;
          }
        });
      });
    })
    .catch((err) => {
      // 注册失败不影响应用（离线是增强，不是前提）
      console.warn('[pwa] Service Worker 注册失败：', err);
    });

  // controllerchange 可能触发多次，只重载一次
  let refreshed = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshed || reloading) return;
    refreshed = true;
    window.location.reload();
  });

  return {
    available: () => updateReady,
    refresh: () => {
      reloading = true;
      waiting?.postMessage('SKIP_WAITING');
      // 兜底：controllerchange 未到时仍重载
      window.setTimeout(() => {
        if (reloading) window.location.reload();
      }, 500);
    },
  };
}
