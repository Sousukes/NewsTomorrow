/*
 * Service Worker：precache（cache-first）/ API GET（network-first + 回落）/
 * 图片（cache-first + 有界淘汰）。非 GET 不缓存不重试。
 * 清单与版本由 `vite.config.ts::pwaPlugin` 注入，勿手写。
 * 策略取舍见 docs/后端/E8-PWA与离线阅读设计.md。
 */
/* global self, caches, fetch, URL, Response, Request, Headers */

const VERSION = '__VERSION__';
const PRECACHE = `sw-precache-${VERSION}`;
const RUNTIME_API = 'sw-api';
const RUNTIME_IMG = 'sw-img';

// 构建期注入的产物清单。注释里不要出现该占位符字面量：
// replace 只换第一处，注释里多一份会让本行仍是 undefined。
// eslint-disable-next-line no-undef
const PRECACHE_URLS = __PRECACHE__;

const API_MAX_ENTRIES = 60;
const IMG_MAX_ENTRIES = 80;
// 注入的图片代理源 + SW 自身源（跨域 API 部署时缺它，配图一张都缓存不到）
// eslint-disable-next-line no-undef
const OWN_ORIGINS = __OWN_ORIGINS__.concat(self.location.origin);
const OFFLINE_FALLBACK_HTML = '<!doctype html><title>离线</title>';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PRECACHE);
      // 逐个 add：单个失败不该让整个 install 失败（否则 SW 永远装不上）
      await Promise.all(
        PRECACHE_URLS.map((url) =>
          cache.add(new Request(url, { cache: 'reload' })).catch(() => {}),
        ),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith('sw-precache-') && k !== PRECACHE)
          .map((k) => caches.delete(k)),
      );
      await self.clients.claim();
    })(),
  );
});

/** 页面主动请求换版本（用户点"刷新"时才 skipWaiting，见 `src/pwa/register.ts`） */
self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING') void self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // 写操作一律直连，不缓存不重试
  const url = new URL(req.url);
  if (!OWN_ORIGINS.includes(url.origin)) return; // 只处理我们自己的源（含图片代理）

  if (req.mode === 'navigate') {
    event.respondWith(navigationHandler(req));
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(apiHandler(req));
    return;
  }
  if (req.destination === 'image') {
    event.respondWith(imgHandler(req));
  }
});

/** 文档导航：network-first，断网时回落到外壳（SPA 只有一个 html） */
async function navigationHandler(req) {
  try {
    return await fetch(req);
  } catch {
    const cache = await caches.open(PRECACHE);
    return (
      (await cache.match('/index.html')) ??
      (await cache.match(req)) ??
      new Response(OFFLINE_FALLBACK_HTML, { headers: { 'Content-Type': 'text/html' } })
    );
  }
}

/**
 * API GET：network-first，失败才回落缓存，并在响应上打标记。
 *
 * 打标记而不是在 SW 里改写 body：调用方需要区分"这是旧副本"，
 * 若 SW 悄悄返回旧数据，UI 会把过期内容当成实时结果展示（那正是要避免的）。
 */
async function apiHandler(req) {
  try {
    const res = await fetch(req);
    if (res.ok) {
      const cache = await caches.open(RUNTIME_API);
      cache.put(req, res.clone());
      void trim(cache, API_MAX_ENTRIES);
    }
    return res;
  } catch {
    const cache = await caches.open(RUNTIME_API);
    const hit = await cache.match(req);
    if (hit) {
      const headers = new Headers(hit.headers);
      headers.set('X-Offline-Fallback', '1');
      return new Response(await hit.blob(), { headers, status: hit.status });
    }
    // 无缓存可回落：给一个明确的空响应，让调用方走自己的降级分支
    return new Response(JSON.stringify({ code: 0, message: 'offline', data: null }), {
      headers: { 'Content-Type': 'application/json', 'X-Offline-Fallback': '1' },
    });
  }
}

/** 图片：cache-first + 有界淘汰（缓存命中不进网络——刷不出新图但也不会重复下载） */
async function imgHandler(req) {
  const cache = await caches.open(RUNTIME_IMG);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok) {
      cache.put(req, res.clone());
      void trim(cache, IMG_MAX_ENTRIES);
    }
    return res;
  } catch {
    return new Response('', { status: 504, statusText: 'offline' });
  }
}

/** 淘汰：超出上限时删最旧的（caches 无原生 LRU，用插入顺序近似） */
async function trim(cache, max) {
  const keys = await cache.keys();
  if (keys.length <= max) return;
  await Promise.all(keys.slice(0, keys.length - max).map((k) => cache.delete(k)));
}
