/**
 * Service Worker 注册与更新提示（E8 / P1-78）。
 *
 * 三条守卫，缺一条都会在特定环境里炸：
 * 1. **`import.meta.env.PROD`**：开发服务器下不注册——dev 的模块是按需加载的，
 *    SW  precache 会把半批模块缓存住，表现为"改了代码却不生效"（且极难归因）；
 * 2. **`'serviceWorker' in navigator`**：jsdom 与旧浏览器没有这个对象，
 *    不 guard 会直接 ReferenceError（单测因此能在 jsdom 里跑本文件）；
 * 3. **非安全上下文**（http 非 localhost）：浏览器根本不允许注册，
 *    `register` 会 reject——宁可跳过也不要留一条未处理的 Promise。
 *
 * 更新走**提示刷新**而不是自动 `skipWaiting`：自动激活会在用户阅读中途
 * 换掉整个执行环境，表现为页面忽然刷新、阅读位置丢失。
 */

export interface PwaUpdateHandle {
  /** 是否有新版本待激活（供 UI 显示"发现新版本 · 点击刷新"） */
  readonly available: () => boolean;
  /** 用户点击"刷新" → 令新 SW 接管并重载 */
  refresh: () => void;
}

/** 无 SW 的环境下的空实现（jsdom / 旧浏览器 / dev） */
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

  // 已经有 SW 在控制页面时，监听它即可；首次安装走 registration 上的事件
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
          // installed 且已有 controller → 这是"新版本到了"而不是首次安装
          if (installing.state === 'installed' && navigator.serviceWorker.controller) {
            waiting = installing;
            updateReady = true;
          }
        });
      });
    })
    .catch((err) => {
      // 注册失败不该影响应用本身：静默记录即可（离线缓存是增强，不是前提）
      console.warn('[pwa] Service Worker 注册失败：', err);
    });

  // 新 SW 接管完成后重载一次（只重载一次：controllerchange 可能触发多次）
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
      // 兜底：若 controllerchange 因故没到（个别浏览器），500ms 后仍重载
      window.setTimeout(() => {
        if (reloading) window.location.reload();
      }, 500);
    },
  };
}
