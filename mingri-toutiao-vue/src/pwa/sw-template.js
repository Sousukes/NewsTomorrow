/*
 * Service Worker（E8 / P1-78）。**手写，不引 workbox**。
 *
 * 三条策略，够用即止：
 * 1. `PRECACHE`（cache-first）：构建产物，文件名带 hash，永不失效、只在换版本时整批替换；
 * 2. `API_GET`（network-first + 失败回落缓存）：只缓存 GET，且回落时**不改响应**——
 *    调用方通过响应头 `X-Offline-Fallback: 1` 知道这是离线副本（UI 要标注，不能假装在线）；
 * 3. `IMG`（cache-first + 有界淘汰）：配图几十张就上百 MB，必须有条数上限，
 *    超出按最旧删除。缓存命中即返回，**不进网络**——图片不会因为你刷新而变新。
 *
 * 明确的**不**做：
 * - 不缓存 / 不重试任何非 GET（写操作的幂等键在服务端，重发会重复计数）；
 * - 不用 stale-while-revalidate（会"先看旧的再静默跳成新的"，比转圈更差）；
 * - 不做 Background Sync（Safari 全家不支持，做了也是半边功能）。
 *
 * 清单与版本号由 Vite 插件在 `generateBundle` 阶段注入（见 `vite.config.ts::pwaPlugin`），
 * 因此**不要在本文件里手写它们**（写死会让 precache 永远命不中上一批的 hash 名）。
 */

/*
 * 本文件在 Service Worker 全局作用域里执行：这里用 `/* global *\/` 声明它依赖的
 * 全局对象（否则 ESLint 会按 Node 环境把它们判成未定义）。
 *
 * 下面的占位符由 `precache.ts` 在构建期替换。**不要在注释里写出它的字面量**——
 * 注入用的是「替换第一处」，注释里多一份会让真正的赋值行永远留着占位符，
 * 产出一份 `PRECACHE_URLS` 为 undefined 的 SW（那种故障在运行时毫无报错）。
 */
/* global self, caches, fetch, URL, Response, Request, Headers */

const VERSION = '__VERSION__';
const PRECACHE = `sw-precache-${VERSION}`;
const RUNTIME_API = 'sw-api';
const RUNTIME_IMG = 'sw-img';

// 占位符：构建期被替换成带 hash 的产物清单（`precache.ts`
// 对"模板里没有它"会直接抛错——静默产出空清单比构建失败难查得多）
// eslint-disable-next-line no-undef
const PRECACHE_URLS = __PRECACHE__;

const API_MAX_ENTRIES = 60;
const IMG_MAX_ENTRIES = 80;
const OFFLINE_FALLBACK_HTML = '<!doctype html><title>离线</title>';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(PRECACHE);
      // 逐个 add 而不 all：单个失败（如被 CDN 拦截）不该让整个安装失败，
      // 那会让 SW 永远装不上，比"少缓存一个文件"严重得多。
      await Promise.all(
        PRECACHE_URLS.map((url) => cache.add(new Request(url, { cache: 'reload' })).catch(() => {})),
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
  if (url.origin !== self.location.origin) return; // 跨域（含图片代理前的源站）不碰

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
